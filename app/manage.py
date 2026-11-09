import getpass
import re
import sys

from psycopg.types.json import Jsonb
from main import DEFAULTS, DEFAULT_WL, USER_RE, hash_pw, q

def ask_password():
    first = getpass.getpass("Password (min 8 characters): ")
    second = getpass.getpass("Repeat password: ")
    if len(first) < 8 or first != second:
        sys.exit("Passwords differ or are shorter than 8 characters.")
    return hash_pw(first)

args = sys.argv[1:]
command = args[0] if args else ""
name = args[1].lower() if len(args) > 1 else ""

if command == "list":
    rows = q("SELECT name,admin FROM users ORDER BY name")
    print("\n".join(row["name"] + (" (admin)" if row["admin"] else "") for row in rows) if rows else "(no users)")
elif command in ("add", "passwd", "delete") and re.fullmatch(USER_RE, name):
    exists = bool(q("SELECT 1 FROM users WHERE name=%s", (name,)))
    if command == "add":
        if exists:
            sys.exit("User already exists.")
        first_user = not bool(q("SELECT 1 FROM users LIMIT 1"))
        q(
            "INSERT INTO users(name,pw,watchlist,settings,admin) VALUES(%s,%s,%s,%s,%s)",
            (name, ask_password(), Jsonb(DEFAULT_WL), Jsonb(DEFAULTS), first_user),
        )
    elif not exists:
        sys.exit("No such user.")
    elif command == "passwd":
        q("UPDATE users SET pw=%s WHERE name=%s", (ask_password(), name))
    else:
        q("DELETE FROM users WHERE name=%s", (name,))
    print("Done.")
else:
    sys.exit("Usage: python manage.py list | add NAME | passwd NAME | delete NAME\nNames: 2-32 characters of a-z, 0-9, _ . -")
