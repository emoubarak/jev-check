"""Throw-away Launchpad accounts, written exactly the way server.py writes them."""

from server import hash_password, init_db

DOMAIN = "example.invalid"
PASSWORD = "Jevcheck-Throwaway-1!"


def create_account(persona, run_id, db, options):
    init_db()  # the demo server may not have run yet
    plan = "pro" if persona == "pro" else "free"
    email = f"jev-{persona}-{run_id}@{DOMAIN}"
    user_id = db.one(
        "insert into users (email, password_hash, plan) values "
        f"({db.lit(email)}, {db.lit(hash_password(PASSWORD))}, {db.lit(plan)}) returning id"
    )["id"]
    vars = {}
    for option, name, published in (("live", "Live project", 1), ("draft", "Draft project", 0)):
        if option in options:
            vars[option] = db.one(
                "insert into projects (user_id, name, published) values "
                f"({user_id}, {db.lit(name)}, {published}) returning id"
            )["id"]
    return {"id": user_id, "email": email, "password": PASSWORD, "vars": vars}


def delete_account(account, db):
    db.sql(f"delete from projects where user_id = {db.lit(int(account['id']))}")
    db.sql(f"delete from users where id = {db.lit(int(account['id']))}")


def purge_accounts(db, older_than_hours):
    rows = db.sql(
        f"select id, email from users where email like 'jev-%@{DOMAIN}' "
        f"and created_at < datetime('now', '-{float(older_than_hours)} hours')"
    )
    for row in rows:
        delete_account(row, db)
    return [r["email"] for r in rows]
