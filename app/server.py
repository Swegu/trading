import hashlib
import hmac
import json
import os
import main
from fastapi import Depends, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Keep the existing scrypt format compatible with the original SQLite installation.
def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex()
    return "scrypt$" + salt.hex() + "$" + digest

def check_password(password, stored):
    try:
        _, salt, digest = stored.split("$")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()
        return hmac.compare_digest(actual, digest)
    except Exception:
        return False

main.hash_pw = hash_password
main.check_pw = check_password
main.DUMMY_HASH = hash_password("not-a-real-password")

main.app.mount("/static", StaticFiles(directory=main.BASE / "static"), name="static")

def load_watchlists(username):
    rows = main.q("SELECT watchlist FROM users WHERE name=%s", (username,))
    if rows and rows[0]["watchlist"] is not None:
        return rows[0]["watchlist"]
    return json.loads(json.dumps(main.DEFAULT_WL))
main.load_wl = load_watchlists

for route in list(main.app.router.routes):
    if getattr(route, "path", None) == "/api/markets" and "GET" in (getattr(route, "methods", set()) or set()):
        main.app.router.routes.remove(route)

@main.app.get("/api/markets")
def markets(username: str = Depends(main.auth)):
    groups = main.load_wl(username)
    symbols = sorted({item["symbol"] for group in groups for item in group.get("items", [])})
    try:
        price_data = main.quotes(symbols)
    except Exception:
        price_data = {}
    output = []
    for group in groups:
        items = []
        for item in group.get("items", []):
            quote = price_data.get(item["symbol"])
            if quote:
                price, previous = quote["price"], quote["previous"]
                pct = ((price / previous) - 1) * 100 if previous else 0
                stamp = quote.get("as_of")
            else:
                price, pct, stamp = None, None, None
            items.append({**item, "price": price, "pct": pct, "w": None, "as_of": stamp, "source": "Yahoo Finance"})
        if items:
            output.append({"name": group["name"], "items": items})
    return output

class PasswordChange(BaseModel):
    current: str = Field(min_length=1, max_length=256)
    new: str = Field(min_length=8, max_length=256)

@main.app.post("/api/password")
def change_password(body: PasswordChange, request: Request, response: Response, username: str = Depends(main.auth)):
    old_hash = main.pw_of(username)
    if not old_hash or not main.check_pw(body.current, old_hash):
        raise HTTPException(403, "Current password is incorrect.")
    new_hash = main.hash_pw(body.new)
    main.q("UPDATE users SET pw=%s WHERE name=%s", (new_hash, username))
    main.set_session(response, request, username, new_hash)
    return {"ok": True}

app = main.app
