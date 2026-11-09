import hashlib, hmac, json, os, re, sqlite3, threading, time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
import pandas as pd
import yfinance as yf
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

BASE=Path(__file__).parent
DATA=Path(os.getenv("DATA_DIR","/data")); DATA.mkdir(parents=True,exist_ok=True)
DATABASE_URL=os.getenv("DATABASE_URL","")  # empty -> libpq falls back to PGHOST/PGUSER/PGPASSWORD/PGDATABASE
SYMBOL=r"[A-Za-z0-9.=^-]{1,15}"; USER_RE=r"[a-z0-9_.-]{2,32}"; SESSION_DAYS=30; QUOTE_TTL=60
RANGES={"1D":("1d","5m"),"5D":("5d","15m"),"1M":("1mo","1h"),"6M":("6mo","1d"),"1Y":("1y","1d"),"5Y":("5y","1wk"),"MAX":("max","1mo")}
DEFAULTS={"theme":"auto","style":"hybrid","density":"balanced","accent":"mint","range":"1Y","refresh":60,"sma":50,"heat":3}
ALLOWED={"theme":["auto","dark","light"],"style":["hybrid","terminal","premium","minimal"],"density":["balanced","compact","comfortable"],"accent":["mint","blue","violet","amber"],"range":list(RANGES),"refresh":[30,60,120,300],"sma":[0,20,50,200],"heat":[1,2,3,5,10]}
CSP="default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
DEFAULT_WL=json.loads((BASE/"config.json").read_text())["groups"]
key_file=DATA/"secret.key"
if not key_file.exists():
    key_file.write_text(os.urandom(32).hex()); key_file.chmod(0o600)
SECRET=key_file.read_text().strip().encode()
pool=ConnectionPool(conninfo=DATABASE_URL,min_size=1,max_size=8,open=False,check=ConnectionPool.check_connection)
_cache={}; _cache_locks={}; _cache_guard=threading.Lock()
_quotes={}; _quote_lock=threading.Lock()
_fails={}; _fail_lock=threading.Lock()
_executor=ThreadPoolExecutor(8)

def q(sql,args=()):
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql,args)
            return cur.fetchall() if cur.description else cur.rowcount

def init_database():
    q("""CREATE TABLE IF NOT EXISTS users(name TEXT PRIMARY KEY,pw TEXT NOT NULL,
        watchlist JSONB NOT NULL DEFAULT '[]'::jsonb,settings JSONB NOT NULL DEFAULT '{}'::jsonb,
        admin BOOLEAN NOT NULL DEFAULT FALSE)""")
    q("CREATE TABLE IF NOT EXISTS app_migrations(name TEXT PRIMARY KEY,applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
    if not q("SELECT 1 FROM app_migrations WHERE name=%s",("sqlite-users-v1",)) and (DATA/"app.db").exists():
        source=sqlite3.connect(DATA/"app.db"); source.row_factory=sqlite3.Row
        try:
            if source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone():
                columns={r["name"] for r in source.execute("PRAGMA table_info(users)")}
                for row in source.execute("SELECT * FROM users").fetchall():
                    q("INSERT INTO users(name,pw,watchlist,settings,admin) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(name) DO NOTHING",
                      (row["name"],row["pw"],Jsonb(json.loads(row["watchlist"] or "[]")),Jsonb(json.loads(row["settings"] or "{}")),bool(row["admin"]) if "admin" in columns else False))
        finally: source.close()
        q("INSERT INTO app_migrations(name) VALUES(%s) ON CONFLICT DO NOTHING",("sqlite-users-v1",))
    if q("SELECT 1 FROM users LIMIT 1") and not q("SELECT 1 FROM users WHERE admin LIMIT 1"):
        q("UPDATE users SET admin=TRUE WHERE name=(SELECT name FROM users ORDER BY name LIMIT 1)")

def cached(key,ttl,fn):
    entry=_cache.get(key)
    if entry and time.time()-entry[0]<ttl: return entry[1]
    with _cache_guard: lock=_cache_locks.setdefault(key,threading.Lock())
    with lock:
        entry=_cache.get(key)
        if entry and time.time()-entry[0]<ttl: return entry[1]
        try:
            value=fn(); _cache[key]=(time.time(),value)
            if len(_cache)>2500:
                cutoff=time.time()-86400
                for old in [k for k,v in _cache.items() if v[0]<cutoff][:500]: _cache.pop(old,None); _cache_locks.pop(old,None)
            return value
        except Exception:
            if entry: return entry[1]
            raise

def fetch_quotes(symbols):
    frame=yf.download(symbols,period="5d",interval="1d",group_by="ticker",auto_adjust=True,progress=False,threads=True)
    result={}
    for symbol in symbols:
        try:
            part=frame[symbol] if isinstance(frame.columns,pd.MultiIndex) else frame
            close=part["Close"].dropna()
            if len(close):
                result[symbol]={"price":float(close.iloc[-1]),"previous":float(close.iloc[-2]) if len(close)>1 else float(close.iloc[-1]),"as_of":close.index[-1].isoformat()}
        except (KeyError,IndexError,TypeError,ValueError): pass
    return result

def quotes(symbols):
    """Per-symbol cache shared by all users; a failed refresh falls back to the last good quote."""
    out={}
    with _quote_lock:
        now=time.time(); missing=[s for s in symbols if not ((hit:=_quotes.get(s)) and now-hit[0]<QUOTE_TTL)]
        if missing:
            try: got=fetch_quotes(missing)
            except Exception: got={}
            for s in missing: _quotes[s]=(now,got.get(s) or (_quotes.get(s) or (0,None))[1])
        for s in symbols:
            if _quotes[s][1]: out[s]=_quotes[s][1]
    return out

def market_cap(symbol):
    cap=yf.Ticker(symbol).fast_info.market_cap
    return float(cap) if cap else 0.0  # 0 = no cap exists (cached); exceptions are not cached
def cap_of(symbol):
    try: return cached("cap:"+symbol,86400,lambda:market_cap(symbol))
    except Exception: return None

def _scrypt(password,salt): return hashlib.scrypt(password.encode(),salt=salt,n=2**14,r=8,p=1).hex()
def hash_pw(password):
    salt=os.urandom(16); return f"scrypt${salt.hex()}${_scrypt(password,salt)}"
def check_pw(password,stored):
    try:
        _,salt,digest=stored.replace("\\","").split("$")  # also accepts hashes written by the old backslash bug
        return hmac.compare_digest(_scrypt(password,bytes.fromhex(salt)),digest)
    except ValueError: return False
DUMMY_HASH=hash_pw("not-a-real-password")
def pw_of(username):
    rows=q("SELECT pw FROM users WHERE name=%s",(username,)); return rows[0]["pw"] if rows else None
def signature(message): return hmac.new(SECRET,message.encode(),"sha256").hexdigest()
def set_session(response,request,username,password_hash):
    expires=int(time.time())+SESSION_DAYS*86400; message=f"{username}|{expires}"
    secure=request.url.scheme=="https" or request.headers.get("x-forwarded-proto","").lower()=="https"
    response.set_cookie("session",f"{message}|{signature(message+password_hash)}",max_age=SESSION_DAYS*86400,httponly=True,samesite="lax",secure=secure)
def current_user(request):
    try:
        username,expires,sig=request.cookies.get("session","").split("|"); password_hash=pw_of(username)
        valid=password_hash and hmac.compare_digest(sig,signature(f"{username}|{expires}"+password_hash))
        return username if valid and int(expires)>time.time() else None
    except (ValueError,TypeError): return None
def auth(request:Request):
    username=current_user(request)
    if not username: raise HTTPException(401,"Not signed in")
    return username
def is_admin(username):
    rows=q("SELECT admin FROM users WHERE name=%s",(username,)); return bool(rows and rows[0]["admin"])
def need_admin(username:str=Depends(auth)):
    if not is_admin(username): raise HTTPException(403,"Admins only")
    return username
def load_wl(username):
    rows=q("SELECT watchlist FROM users WHERE name=%s",(username,))
    return rows[0]["watchlist"] if rows and rows[0]["watchlist"] is not None else json.loads(json.dumps(DEFAULT_WL))
def load_settings(username):
    rows=q("SELECT settings FROM users WHERE name=%s",(username,))
    return {**DEFAULTS,**(rows[0]["settings"] if rows else {})}
def page(name): return FileResponse(BASE/"static"/name,headers={"Cache-Control":"no-store"})

@asynccontextmanager
async def lifespan(app):
    pool.open(wait=True); init_database()
    yield
    pool.close()
app=FastAPI(title="Markets",lifespan=lifespan)

@app.middleware("http")
async def security_headers(request,call_next):
    response=await call_next(request)
    response.headers.update({"X-Content-Type-Options":"nosniff","X-Frame-Options":"DENY","Referrer-Policy":"strict-origin-when-cross-origin","Permissions-Policy":"camera=(), microphone=(), geolocation=()","Content-Security-Policy":CSP})
    return response

@app.get("/")
def index(request:Request): return page("index.html") if current_user(request) else RedirectResponse("/login")
@app.get("/login")
def login_page(request:Request): return RedirectResponse("/") if current_user(request) else page("login.html")
@app.get("/api/status")
def status(): return {"setup_required":not q("SELECT 1 FROM users LIMIT 1"),"application":"Markets"}

class Credentials(BaseModel):
    username:str=Field(min_length=2,max_length=32)
    password:str=Field(min_length=8,max_length=256)
class PasswordChange(BaseModel):
    current:str=Field(min_length=1,max_length=256)
    new:str=Field(min_length=8,max_length=256)
class WatchlistItem(BaseModel):
    group:str=Field(max_length=100)
    symbol:str=Field(max_length=32)
    name:str=Field("",max_length=200)

def valid_username(raw):
    name=raw.strip().lower()
    if not re.fullmatch(USER_RE,name): raise HTTPException(400,"Username must be 2–32 characters: a-z, 0-9, _ . -")
    return name

@app.post("/api/setup")
def setup_user(body:Credentials,request:Request,response:Response):
    username=valid_username(body.username)
    with pool.connection() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT pg_advisory_xact_lock(8142701)")
                cur.execute("SELECT 1 FROM users LIMIT 1")
                if cur.fetchone(): raise HTTPException(409,"Initial setup has already been completed.")
                password_hash=hash_pw(body.password)
                cur.execute("INSERT INTO users(name,pw,watchlist,settings,admin) VALUES(%s,%s,%s,%s,TRUE)",(username,password_hash,Jsonb(DEFAULT_WL),Jsonb(DEFAULTS)))
    set_session(response,request,username,password_hash); return {"ok":True}

@app.post("/api/login")
def login(body:Credentials,request:Request,response:Response):
    username=body.username.strip().lower(); now=time.time()
    with _fail_lock:
        count,started=_fails.get(username,(0,now))
        if now-started>300: count,started=0,now
        if count>=5: raise HTTPException(429,"Too many attempts. Try again in a few minutes.")
    stored=pw_of(username)
    if not (check_pw(body.password,stored or DUMMY_HASH) and stored):
        with _fail_lock:
            _fails[username]=(count+1,started)
            if len(_fails)>1000:
                for k in [k for k,(_,t) in _fails.items() if now-t>300]: _fails.pop(k)
        raise HTTPException(401,"Wrong username or password.")
    with _fail_lock: _fails.pop(username,None)
    set_session(response,request,username,stored); return {"ok":True}

@app.post("/api/logout")
def logout(response:Response):
    response.delete_cookie("session",httponly=True,samesite="lax"); return {"ok":True}
@app.get("/api/me")
def me(username:str=Depends(auth)): return {"user":username,"admin":is_admin(username),"settings":load_settings(username)}
@app.put("/api/settings")
def put_settings(settings:dict,username:str=Depends(auth)):
    valid={k:v for k,v in settings.items() if k in ALLOWED and not isinstance(v,bool) and v in ALLOWED[k]}
    q("UPDATE users SET settings=settings||%s WHERE name=%s",(Jsonb(valid),username))
    return load_settings(username)
@app.post("/api/password")
def change_password(body:PasswordChange,request:Request,response:Response,username:str=Depends(auth)):
    old_hash=pw_of(username)
    if not old_hash or not check_pw(body.current,old_hash): raise HTTPException(403,"Current password is incorrect.")
    new_hash=hash_pw(body.new); q("UPDATE users SET pw=%s WHERE name=%s",(new_hash,username))
    set_session(response,request,username,new_hash); return {"ok":True}

@app.get("/api/users")
def list_users(username:str=Depends(need_admin)): return q("SELECT name,admin FROM users ORDER BY name")
@app.post("/api/users")
def add_user(body:Credentials,username:str=Depends(need_admin)):
    name=valid_username(body.username)
    try: q("INSERT INTO users(name,pw,watchlist,settings,admin) VALUES(%s,%s,%s,%s,FALSE)",(name,hash_pw(body.password),Jsonb(DEFAULT_WL),Jsonb(DEFAULTS)))
    except UniqueViolation as exc: raise HTTPException(409,"That username is taken.") from exc
    return {"ok":True}
@app.delete("/api/users/{name}")
def delete_user(name:str,username:str=Depends(need_admin)):
    if name==username: raise HTTPException(400,"You can't delete your own account.")
    q("DELETE FROM users WHERE name=%s",(name,)); return {"ok":True}

@app.get("/api/markets")
def markets(username:str=Depends(auth)):
    groups=load_wl(username)
    px=quotes(sorted({i["symbol"] for g in groups for i in g.get("items",[])}))
    sized=sorted({i["symbol"] for g in groups if g.get("sizeByCap") for i in g.get("items",[]) if i["symbol"] in px})
    caps=dict(zip(sized,_executor.map(cap_of,sized)))
    output=[]
    for group in groups:
        items=[]
        for item in group.get("items",[]):
            quote=px.get(item["symbol"]); price=pct=as_of=None
            if quote:
                price,as_of=quote["price"],quote["as_of"]
                pct=(price/quote["previous"]-1)*100 if quote["previous"] else 0
            items.append({**item,"price":price,"pct":pct,"w":caps.get(item["symbol"]) if group.get("sizeByCap") else None,"as_of":as_of,"source":"Yahoo Finance"})
        if not all(i["w"] for i in items if i["price"] is not None):
            for item in items: item["w"]=None
        if items: output.append({"name":group["name"],"items":items})
    return output

@app.get("/api/history")
def history(symbol:str,range:str="1Y",username:str=Depends(auth)):
    if not re.fullmatch(SYMBOL,symbol) or range not in RANGES: raise HTTPException(400,"Invalid symbol or range.")
    period,interval=RANGES[range]
    def fetch():
        frame=yf.download(symbol,period=period,interval=interval,auto_adjust=True,progress=False)
        if isinstance(frame.columns,pd.MultiIndex): frame.columns=frame.columns.get_level_values(0)
        if frame.empty: return []
        if "Volume" not in frame: frame["Volume"]=0
        frame=frame.dropna(subset=["Open","High","Low","Close"]).fillna({"Volume":0}).sort_index()
        frame=frame[~frame.index.duplicated(keep="last")]  # charts need strictly ascending, unique times
        daily=interval in ("1d","1wk","1mo")
        return [{"time":t.strftime("%Y-%m-%d") if daily else int(t.timestamp()),"open":float(o),"high":float(h),"low":float(l),"close":float(c),"volume":float(v)}
                for t,o,h,l,c,v in zip(frame.index,frame["Open"],frame["High"],frame["Low"],frame["Close"],frame["Volume"])]
    try: return cached(f"history:{symbol}:{range}",60 if range=="1D" else 300,fetch)
    except Exception: raise HTTPException(502,"Market data provider is temporarily unavailable.")

@app.get("/api/search")
def search(q:str,username:str=Depends(auth)):
    query=q.strip()[:40]
    if len(query)<2: return []
    def fetch():
        found=yf.Search(query,max_results=8,news_count=0).quotes
        return [{"symbol":r["symbol"],"name":r.get("shortname") or r.get("longname") or r["symbol"],"type":r.get("quoteType",""),"exch":r.get("exchDisp","")} for r in found if r.get("symbol")]
    try: return cached("search:"+query.lower(),3600,fetch)
    except Exception: return []

@app.post("/api/watchlist")
def add_watchlist_item(body:WatchlistItem,username:str=Depends(auth)):
    symbol,group_name=body.symbol.strip().upper(),body.group.strip()[:30]
    if not re.fullmatch(SYMBOL,symbol) or not group_name: raise HTTPException(400,"Invalid symbol or group.")
    with pool.connection() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT watchlist FROM users WHERE name=%s FOR UPDATE",(username,))
                groups=cur.fetchone()["watchlist"] or []
                if any(i["symbol"]==symbol for g in groups for i in g.get("items",[])): raise HTTPException(409,"That symbol is already in your watchlist.")
                group=next((g for g in groups if g["name"]==group_name),None)
                if group is None: group={"name":group_name,"items":[]}; groups.append(group)
                group["items"].append({"symbol":symbol,"name":body.name.strip()[:60] or symbol})
                cur.execute("UPDATE users SET watchlist=%s WHERE name=%s",(Jsonb(groups),username))
    return {"ok":True}

@app.delete("/api/watchlist/{symbol}")
def remove_watchlist_item(symbol:str,username:str=Depends(auth)):
    symbol=symbol.strip().upper()
    with pool.connection() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT watchlist FROM users WHERE name=%s FOR UPDATE",(username,))
                groups=cur.fetchone()["watchlist"] or []
                for group in groups: group["items"]=[i for i in group.get("items",[]) if i["symbol"]!=symbol]
                cur.execute("UPDATE users SET watchlist=%s WHERE name=%s",(Jsonb([g for g in groups if g["items"]]),username))
    return {"ok":True}

app.mount("/static",StaticFiles(directory=BASE/"static"),name="static")
