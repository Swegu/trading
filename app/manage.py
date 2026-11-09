import getpass, re, sys
from psycopg.types.json import Jsonb
import main

def ask_password():
    first = getpass.getpass("Password (min 8 characters): ")
    if len(first) < 8 or first != getpass.getpass("Repeat password: "):
        sys.exit("Passwords differ or are shorter than 8 characters.")
    return main.hash_pw(first)

USAGE = "Usage: python manage.py list | add NAME | passwd NAME | delete NAME\nNames: 2-32 characters of a-z, 0-9, _ . -"
command, name = (sys.argv[1:] + ["", ""])[:2]
name = name.lower()
if command != "list" and not (command in ("add", "passwd", "delete") and re.fullmatch(main.USER_RE, name)):
    sys.exit(USAGE)

main.pool.open(wait=True)
try:
    main.init_database()
    q = main.q
    if command == "list":
        rows = q("SELECT name,admin FROM users ORDER BY name")
        print("\n".join(r["name"] + (" (admin)" if r["admin"] else "") for r in rows) or "(no users)")
        sys.exit(0)
    exists = bool(q("SELECT 1 FROM users WHERE name=%s", (name,)))
    if command == "add":
        if exists: sys.exit("User already exists.")
        q("INSERT INTO users(name,pw,watchlist,settings,admin) VALUES(%s,%s,%s,%s,NOT EXISTS(SELECT 1 FROM users))",
          (name, ask_password(), Jsonb(main.DEFAULT_WL), Jsonb(main.DEFAULTS)))
    elif not exists: sys.exit("No such user.")
    elif command == "passwd": q("UPDATE users SET pw=%s WHERE name=%s", (ask_password(), name))
    else: q("DELETE FROM users WHERE name=%s", (name,))
    print("Done.")
finally:
    main.pool.close()
