import hashlib, hmac, json, os, re, sqlite3, threading, time
from contextlib import asynccontextmanager
from pathlib import Path
import pandas as pd
import yfinance as yf
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

BASE=Path(__file__).parent
DATA=Path(os.getenv("DATA_DIR","/data")); DATA.mkdir(parents=True,exist_ok=True)
DATABASE_URL=os.environ["DATABASE_URL"]
SYMBOL=r"[A-Za-z0-9.=^-]{1,15}"; USER_RE=r"[a-z0-9_.-]{2,32}"; SESSION_DAYS=30
RANGES={"1D":("1d","5m"),"5D":("5d","15m"),"1M":("1mo","1h"),"6M":("6mo","1d"),"1Y":("1y","1d"),"5Y":("5y","1wk"),"MAX":("max","1mo")}
DEFAULTS={"theme":"auto","style":"hybrid","density":"balanced","accent":"mint","range":"1Y","refresh":60,"sma":50,"heat":3}
ALLOWED={"theme":["auto","dark","light"],"style":["hybrid","terminal","premium","minimal"],"density":["balanced","compact","comfortable"],"accent":["mint","blue","violet","amber"],"range":list(RANGES),"refresh":[30,60,120,300],"sma":[0,20,50,200],"heat":[1,2,3,5,10]}
DEFAULT_WL=json.loads((BASE/"config.json").read_text())["groups"]
key_file=DATA/"secret.key"
if not key_file.exists():
    key_file.write_text(os.urandom(32).hex()); key_file.chmod(0o600)
SECRET=key_file.read_text().strip().encode()
pool=ConnectionPool(conninfo=DATABASE_URL,min_size=1,max_size=8,open=False)
_cache={}; _cache_locks={}; _cache_guard=threading.Lock(); _fails={}; _fail_lock=threading.Lock()

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
            exists=source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
            if exists:
                columns={r["name"] for r in source.execute("PRAGMA table_info(users)")}
                for row in source.execute("SELECT * FROM users").fetchall():
                    wl=json.loads(row["watchlist"] or "[]"); settings=json.loads(row["settings"] or "{}")
                    admin=bool(row["admin"]) if "admin" in columns else False
                    q("INSERT INTO users(name,pw,watchlist,settings,admin) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(name) DO NOTHING",
                      (row["name"],row["pw"],Jsonb(wl),Jsonb(settings),admin))
        finally: source.close()
        q("INSERT INTO app_migrations(name) VALUES(%s) ON CONFLICT DO NOTHING",("sqlite-users-v1",))
    counts=q("SELECT COUNT(*) AS users,COUNT(*) FILTER (WHERE admin) AS admins FROM users")[0]
    if counts["users"] and not counts["admins"]:
        first=q("SELECT name FROM users ORDER BY name LIMIT 1")[0]["name"]
        q("UPDATE users SET admin=TRUE WHERE name=%s",(first,))

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
                for old in [k for k,v in _cache.items() if v[0]<cutoff][:500]: _cache.pop(old,None)
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

def quotes(symbols): return cached("quotes:"+",".join(sorted(symbols)),60,lambda:fetch_quotes(symbols)) if symbols else {}
def market_cap(symbol):
    try: return float(yf.Ticker(symbol).fast_info.market_cap)
    except Exception: return None

def hash_pw(password):
    salt=os.urandom(16); digest=hashlib.scrypt(password.encode(),salt=salt,n=2**14,r=8,p=1).hex()
    return f"scrypt\${salt.hex()}\${digest}"

def check_pw(password,stored):
    try:
        _,salt,digest=stored.split("$")
        actual=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=2**14,r=8,p=1).hex()
        return hmac.compare_digest(actual,digest)
    except Exception: return False
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
    return rows[0]["watchlist"] if rows and rows[0]["watchlist"] else json.loads(json.dumps(DEFAULT_WL))
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
    response.headers.update({"X-Content-Type-Options":"nosniff","X-Frame-Options":"DENY","Referrer-Policy":"strict-origin-when-cross-origin","Permissions-Policy":"camera=(), microphone=(), geolocation=()"})
    return response

@app.get("/")
def index(request:Request): return page("index.html") if current_user(request) else RedirectResponse("/login")
@app.get("/login")
def login_page(request:Request): return RedirectResponse("/") if current_user(request) else page("login.html")
@app.get("/api/status")
def status(): return {"setup_required":q("SELECT COUNT(*) AS n FROM users")[0]["n"]==0,"application":"Markets"}

class Credentials(BaseModel):
    username:str=Field(min_length=2,max_length=32)
    password:str=Field(min_length=8,max_length=256)

@app.post("/api/setup")
def setup_user(body:Credentials,request:Request,response:Response):
    username=body.username.strip().lower()
    if not re.fullmatch(USER_RE,username): raise HTTPException(400,"Username must be 2–32 characters: a-z, 0-9, _ . -")
    with pool.connection() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT pg_advisory_xact_lock(8142701)")
                cur.execute("SELECT COUNT(*) AS n FROM users")
                if cur.fetchone()["n"]: raise HTTPException(409,"Initial setup has already been completed.")
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
    stored=pw_of(username); valid=check_pw(body.password,stored or DUMMY_HASH)
    if not (stored and valid):
        with _fail_lock: _fails[username]=(count+1,started)
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
    current=load_settings(username)
    for key,value in settings.items():
        if key in ALLOWED and value in ALLOWED[key]: current[key]=value
    q("UPDATE users SET settings=%s WHERE name=%s",(Jsonb(current),username)); return current

class NewUser(BaseModel):
    username:str
    password:str
@app.get("/api/users")
def list_users(username:str=Depends(need_admin)):
    return [{"name":r["name"],"admin":bool(r["admin"])} for r in q("SELECT name,admin FROM users ORDER BY name")]
@app.post("/api/users")
def add_user(body:NewUser,username:str=Depends(need_admin)):
    name=body.username.strip().lower()
    if not re.fullmatch(USER_RE,name): raise HTTPException(400,"Username: 2–32 characters of a-z, 0-9, _ . -")
    if len(body.password)<8: raise HTTPException(400,"Password needs at least 8 characters.")
    try: q("INSERT INTO users(name,pw,watchlist,settings,admin) VALUES(%s,%s,%s,%s,FALSE)",(name,hash_pw(body.password),Jsonb(DEFAULT_WL),Jsonb(DEFAULTS)))
    except Exception as exc:
        if "users_pkey" in str(exc): raise HTTPException(409,"That username is taken.") from exc
        raise
    return {"ok":True}
@app.delete("/api/users/{name}")
def delete_user(name:str,username:str=Depends(need_admin)):
    if name==username: raise HTTPException(400,"You can't delete your own account.")
    q("DELETE FROM users WHERE name=%s",(name,)); return {"ok":True}

@app.get("/api/markets")
def markets(username:str=Depends(auth)):
    groups=load_wl(username); symbols=sorted({i["symbol"] for g in groups for i in g.get("items",[])})
    px=quotes(symbols); output=[]
    for group in groups:
        items=[]
        for item in group.get("items",[]):
            quote=px.get(item["symbol"])
            if not quote: continue
            price,previous=quote["price"],quote["previous"]
            cap=cached("cap:"+item["symbol"],86400,lambda s=item["symbol"]:market_cap(s)) if group.get("sizeByCap") else None
            items.append({**item,"price":price,"pct":((price/previous)-1)*100 if previous else 0,"w":cap,"as_of":quote.get("as_of"),"source":"Yahoo Finance"})
        if not all(i["w"] for i in items):
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
        frame=frame.dropna(subset=["Close"]).fillna({"Volume":0}); daily=interval in ("1d","1wk","1mo")
        return [{"time":stamp.strftime("%Y-%m-%d") if daily else int(stamp.timestamp()),"open":float(row["Open"]),"high":float(row["High"]),"low":float(row["Low"]),"close":float(row["Close"]),"volume":float(row.get("Volume",0) or 0)} for stamp,row in frame.iterrows()]
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

class WatchlistItem(BaseModel):
    group:str
    symbol:str
    name:str=""
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
    with pool.connection() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT watchlist FROM users WHERE name=%s FOR UPDATE",(username,))
                groups=cur.fetchone()["watchlist"] or []
                for group in groups: group["items"]=[i for i in group.get("items",[]) if i["symbol"]!=symbol]
                groups=[g for g in groups if g["items"]]
                cur.execute("UPDATE users SET watchlist=%s WHERE name=%s",(Jsonb(groups),username))
    return {"ok":True}
