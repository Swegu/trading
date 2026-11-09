import hashlib, hmac, json, os, re, sqlite3, threading, time
from pathlib import Path

import pandas as pd
import yfinance as yf
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

BASE = Path(__file__).parent
DATA = Path(os.getenv("DATA_DIR", "/data"))
SYMBOL = r"[A-Za-z0-9.=^-]{1,15}"
USER_RE = r"[a-z0-9_.-]{2,32}"
SESSION_DAYS = 30

DATA.mkdir(parents=True, exist_ok=True)
_kf = DATA / "secret.key"
if not _kf.exists():
    _kf.write_text(os.urandom(32).hex())
    _kf.chmod(0o600)
SECRET = _kf.read_text().strip().encode()

_old = DATA / "watchlist.json"  # list from the earlier shared version becomes the starter list
DEFAULT_WL = json.loads((_old if _old.exists() else BASE / "config.json").read_text())["groups"]

RANGES = {"1D": ("1d", "5m"), "5D": ("5d", "15m"), "1M": ("1mo", "1h"), "6M": ("6mo", "1d"),
          "1Y": ("1y", "1d"), "5Y": ("5y", "1wk"), "MAX": ("max", "1mo")}
DEFAULTS = {"theme": "auto", "range": "1Y", "refresh": 60, "sma": 50, "heat": 3}
ALLOWED = {"theme": ["auto", "dark", "light"], "range": list(RANGES), "refresh": [30, 60, 120, 300],
           "sma": [0, 20, 50, 200], "heat": [1, 2, 3, 5, 10]}

app = FastAPI()
_cache, _quotes, _fails = {}, {}, {}
_lock, _wl_lock = threading.Lock(), threading.Lock()


@app.middleware("http")
async def headers(request, call_next):
    r = await call_next(request)
    r.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                      "Referrer-Policy": "same-origin"})
    return r


# ---------- storage and auth ----------
def q(sql, args=()):
    c = sqlite3.connect(DATA / "app.db", timeout=10)
    c.row_factory = sqlite3.Row
    try:
        rows = c.execute(sql, args).fetchall()
        c.commit()
        return rows
    finally:
        c.close()


q("create table if not exists users(name text primary key, pw text not null, watchlist text, settings text)")
if "admin" not in [r["name"] for r in q("pragma table_info(users)")]:
    q("alter table users add column admin integer default 0")
if not q("select 1 from users where admin=1"):
    q("update users set admin=1 where rowid=(select min(rowid) from users)")


def hash_pw(p):
    s = os.urandom(16)
    return f"scrypt${s.hex()}${hashlib.scrypt(p.encode(), salt=s, n=2**14, r=8, p=1).hex()}"


def check_pw(p, h):
    try:
        _, s, d = h.split("$")
        return hmac.compare_digest(hashlib.scrypt(p.encode(), salt=bytes.fromhex(s), n=2**14, r=8, p=1).hex(), d)
    except Exception:
        return False


DUMMY = hash_pw("dummy")  # equalises timing for unknown usernames


def pw_of(u):
    r = q("select pw from users where name=?", (u,))
    return r[0]["pw"] if r else None


def _sig(msg):
    return hmac.new(SECRET, msg.encode(), "sha256").hexdigest()


def set_session(resp, request, u, pw):
    m = f"{u}|{int(time.time()) + SESSION_DAYS * 86400}"
    resp.set_cookie("session", f"{m}|{_sig(m + pw)}", max_age=SESSION_DAYS * 86400, httponly=True,
                    samesite="lax", secure=request.headers.get("x-forwarded-proto") == "https")


def current(request):
    """Signature covers the password hash, so changing the password ends old sessions."""
    try:
        u, exp, sig = request.cookies.get("session", "").split("|")
        pw = pw_of(u)
        ok = pw and hmac.compare_digest(sig, _sig(f"{u}|{exp}{pw}"))
        return u if ok and int(exp) > time.time() else None
    except ValueError:
        return None


def auth(request: Request):
    u = current(request)
    if not u:
        raise HTTPException(401, "Not signed in")
    return u


def load_wl(u):
    r = q("select watchlist from users where name=?", (u,))
    return json.loads(r[0]["watchlist"]) if r and r[0]["watchlist"] else json.loads(json.dumps(DEFAULT_WL))


def save_wl(u, g):
    q("update users set watchlist=? where name=?", (json.dumps(g), u))


def load_settings(u):
    r = q("select settings from users where name=?", (u,))
    return {**DEFAULTS, **json.loads(r[0]["settings"] or "{}")}


# ---------- market data ----------
def cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        val = fn()
        _cache[key] = (time.time(), val)
        return val


def fetch_quotes(syms):
    df = yf.download(syms, period="5d", interval="1d", group_by="ticker",
                     auto_adjust=True, progress=False, threads=True)
    out = {}
    for s in syms:
        try:
            c = (df[s] if isinstance(df.columns, pd.MultiIndex) else df)["Close"].dropna()
            out[s] = (float(c.iloc[-1]), float(c.iloc[-2]))
        except Exception:
            pass
    return out


def quotes(syms):
    """Per-symbol 60s cache shared by all users; only stale symbols hit Yahoo."""
    with _lock:
        now = time.time()
        stale = [s for s in syms if s not in _quotes or now - _quotes[s][0] >= 60]
        if stale:
            got = fetch_quotes(stale)
            for s in stale:
                _quotes[s] = (now, got.get(s))
        return {s: _quotes[s][1] for s in syms if _quotes[s][1]}


def market_cap(sym):
    try:
        return float(yf.Ticker(sym).fast_info.market_cap)
    except Exception:
        return None


# ---------- pages ----------
def page(name):
    return FileResponse(BASE / "static" / name, headers={"Cache-Control": "no-store"})


@app.get("/")
def index(request: Request):
    return page("index.html") if current(request) else RedirectResponse("/login")


@app.get("/login")
def login_page(request: Request):
    return RedirectResponse("/") if current(request) else page("login.html")


# ---------- auth api ----------
class Cred(BaseModel):
    username: str
    password: str


class PwChange(BaseModel):
    current: str
    new: str


@app.post("/api/login")
def login(b: Cred, request: Request, response: Response):
    name = b.username.strip().lower()
    ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    n, t = _fails.get((ip, name), (0, 0))
    if time.time() - t > 300:
        n = 0
    if n >= 5:
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    pw = pw_of(name)
    ok = check_pw(b.password, pw or DUMMY)
    if not (pw and ok):
        if len(_fails) > 1000:
            _fails.clear()
        _fails[(ip, name)] = (n + 1, time.time())
        raise HTTPException(401, "Wrong username or password.")
    _fails.pop((ip, name), None)
    set_session(response, request, name, pw)
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("session")
    return {"ok": True}


@app.post("/api/password")
def change_pw(b: PwChange, request: Request, response: Response, u: str = Depends(auth)):
    if not check_pw(b.current, pw_of(u)):
        raise HTTPException(403, "Current password is wrong.")
    if len(b.new) < 8:
        raise HTTPException(400, "New password needs at least 8 characters.")
    new = hash_pw(b.new)
    q("update users set pw=? where name=?", (new, u))
    set_session(response, request, u, new)
    return {"ok": True}


def is_admin(u):
    r = q("select admin from users where name=?", (u,))
    return bool(r and r[0]["admin"])


def need_admin(u: str = Depends(auth)):
    if not is_admin(u):
        raise HTTPException(403, "Admins only")
    return u


@app.get("/api/me")
def me(u: str = Depends(auth)):
    return {"user": u, "admin": is_admin(u), "settings": load_settings(u)}


class NewUser(BaseModel):
    username: str
    password: str


@app.get("/api/users")
def list_users(u: str = Depends(need_admin)):
    return [{"name": r["name"], "admin": bool(r["admin"])} for r in q("select name, admin from users order by name")]


@app.post("/api/users")
def add_user(b: NewUser, u: str = Depends(need_admin)):
    name = b.username.strip().lower()
    if not re.fullmatch(USER_RE, name):
        raise HTTPException(400, "Username: 2-32 characters of a-z, 0-9, _ . -")
    if len(b.password) < 8:
        raise HTTPException(400, "Password needs at least 8 characters.")
    if pw_of(name):
        raise HTTPException(409, "That username is taken.")
    q("insert into users(name, pw, watchlist, settings) values(?,?,?,'{}')",
      (name, hash_pw(b.password), json.dumps(DEFAULT_WL)))
    return {"ok": True}


@app.delete("/api/users/{name}")
def del_user(name: str, u: str = Depends(need_admin)):
    if name == u:
        raise HTTPException(400, "You can't delete your own account.")
    q("delete from users where name=?", (name,))
    return {"ok": True}


@app.put("/api/settings")
def put_settings(s: dict, u: str = Depends(auth)):
    cur = load_settings(u)
    cur.update({k: v for k, v in s.items() if k in ALLOWED and v in ALLOWED[k]})
    q("update users set settings=? where name=?", (json.dumps(cur), u))
    return cur


# ---------- market api ----------
@app.get("/api/markets")
def markets(u: str = Depends(auth)):
    groups = load_wl(u)
    syms = sorted({i["symbol"] for g in groups for i in g["items"]})
    px = quotes(syms) if syms else {}
    out = []
    for g in groups:
        items = []
        for i in g["items"]:
            if i["symbol"] not in px:
                continue
            p, prev = px[i["symbol"]]
            w = cached("cap:" + i["symbol"], 86400, lambda s=i["symbol"]: market_cap(s)) if g.get("sizeByCap") else None
            items.append({**i, "price": p, "pct": (p / prev - 1) * 100, "w": w})
        if not all(i["w"] for i in items):  # partial caps would distort tiles
            for i in items:
                i["w"] = None
        if items:
            out.append({"name": g["name"], "items": items})
    return out


@app.get("/api/history", dependencies=[Depends(auth)])
def history(symbol: str, range: str = "1Y"):
    if not re.fullmatch(SYMBOL, symbol) or range not in RANGES:
        raise HTTPException(400, "bad symbol or range")
    period, iv = RANGES[range]

    def go():
        df = yf.download(symbol, period=period, interval=iv, auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Close"]).fillna({"Volume": 0})
        daily = iv in ("1d", "1wk", "1mo")
        return [{"time": ts.strftime("%Y-%m-%d") if daily else int(ts.timestamp()),
                 "open": float(r.Open), "high": float(r.High), "low": float(r.Low),
                 "close": float(r.Close), "volume": float(r.Volume)} for ts, r in df.iterrows()]

    return cached(f"h:{symbol}:{range}", 60 if range == "1D" else 300, go)


@app.get("/api/search", dependencies=[Depends(auth)])
def search(q: str):
    q = q.strip()[:40]
    if len(q) < 2:
        return []

    def go():
        res = yf.Search(q, max_results=8, news_count=0).quotes
        return [{"symbol": r["symbol"], "name": r.get("shortname") or r.get("longname") or r["symbol"],
                 "type": r.get("quoteType", ""), "exch": r.get("exchDisp", "")}
                for r in res if r.get("symbol")]
    try:
        return cached("s:" + q.lower(), 3600, go)
    except Exception:
        return []


class Item(BaseModel):
    group: str
    symbol: str
    name: str = ""


@app.post("/api/watchlist")
def add(it: Item, u: str = Depends(auth)):
    sym, grp = it.symbol.strip().upper(), it.group.strip()[:30]
    if not re.fullmatch(SYMBOL, sym) or not grp:
        raise HTTPException(400, "bad symbol or group")
    with _wl_lock:
        groups = load_wl(u)
        if any(i["symbol"] == sym for g in groups for i in g["items"]):
            raise HTTPException(409, "already in your watchlist")
        g = next((g for g in groups if g["name"] == grp), None)
        if not g:
            g = {"name": grp, "items": []}
            groups.append(g)
        g["items"].append({"symbol": sym, "name": it.name.strip()[:60] or sym})
        save_wl(u, groups)
    return {"ok": True}


@app.delete("/api/watchlist/{symbol}")
def remove(symbol: str, u: str = Depends(auth)):
    with _wl_lock:
        groups = load_wl(u)
        for g in groups:
            g["items"] = [i for i in g["items"] if i["symbol"] != symbol]
        save_wl(u, [g for g in groups if g["items"]])
    return {"ok": True}
