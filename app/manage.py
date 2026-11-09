import getpass, json, re, sys
from main import DEFAULT_WL, USER_RE, hash_pw, q


def ask_pw():
    a = getpass.getpass("Password (min 8 characters): ")
    if len(a) < 8 or a != getpass.getpass("Repeat: "):
        sys.exit("Passwords differ or are shorter than 8 characters.")
    return hash_pw(a)


cmd, name = (sys.argv[1:3] + ["", ""])[:2]
name = name.lower()
if cmd == "list":
    names = [r["name"] for r in q("select name from users order by name")]
    print("\n".join(names) if names else "(no users)")
elif cmd in ("add", "passwd", "delete") and re.fullmatch(USER_RE, name):
    exists = bool(q("select 1 from users where name=?", (name,)))
    if cmd == "add":
        if exists:
            sys.exit("User already exists.")
        first = not q("select 1 from users")
        q("insert into users(name, pw, watchlist, settings, admin) values(?,?,?,'{}',?)",
          (name, ask_pw(), json.dumps(DEFAULT_WL), int(first)))
    elif not exists:
        sys.exit("No such user.")
    elif cmd == "passwd":
        q("update users set pw=? where name=?", (ask_pw(), name))
    else:
        q("delete from users where name=?", (name,))
    print("Done.")
else:
    sys.exit("Usage: python manage.py list | add NAME | passwd NAME | delete NAME\n"
             "Names: 2-32 characters of a-z, 0-9, _ . -")
