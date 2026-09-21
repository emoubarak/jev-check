"""Throw-away accounts, created the way YOUR production code creates them.

The one rule: every state written here must be one your app or your payment provider can
actually produce. Copy the shape from the code that writes it (the webhook handler, the admin
"grant plan" action, the downgrade job) — never invent it. A state production can never reach
makes the agent report bugs that do not exist.

`db` is the datastore from jevcheck.toml: db.sql(q) → rows, db.one(q) → row | None,
db.value(q) → first column, db.lit(v) → a quoted SQL literal.
"""

# A domain that cannot exist (RFC 2606), so a sweep can never match a real user.
DOMAIN = "example.invalid"
# A plain, fixed password: it is only ever typed by the harness, over CDP.
PASSWORD = "Jevcheck-Throwaway-1!"


def create_account(persona, run_id, db, options):
    """Create one account in the state `persona` names. Return at least email + password.

    options: the --with values (ask) or the union of Check.options (audit) for this persona,
             e.g. {"draft", "live"}: extra rows this account should own.
    Return:  {"id": ..., "email": ..., "password": ...,
              "vars": {...},            # {placeholders} for --path / Check.path, e.g. {"draft": project_id}
              "local_storage": {...}}   # written after sign-in (e.g. the active workspace)
    """
    email = f"jev-{persona}-{run_id}@{DOMAIN}"
    raise NotImplementedError(
        "write create_account for your app: insert the user (hash the password the way your auth "
        "does, or call your staging admin API), then put it in the state for " + repr(persona)
    )
    # Example shape, for an app with a users table and a plan column:
    #
    # user_id = db.one(f"insert into users (email, password_hash, plan) values "
    #                  f"({db.lit(email)}, {db.lit(hash_password(PASSWORD))}, "
    #                  f"{db.lit('pro' if persona == 'pro' else 'free')}) returning id")["id"]
    # vars = {}
    # if "draft" in options:
    #     vars["draft"] = db.one(f"insert into projects (user_id, name, published) values "
    #                            f"({db.lit(user_id)}, 'Draft', false) returning id")["id"]
    # return {"id": user_id, "email": email, "password": PASSWORD, "vars": vars}


def delete_account(account, db):
    """Delete everything create_account made. `account` has id, email, persona, vars — no password.

    Called at the end of every run (on failure too), and by the TTL sweep for a kept account.
    """
    raise NotImplementedError("delete the user and whatever create_account inserted for it")
    # db.sql(f"delete from users where id = {db.lit(account['id'])}")   # if it cascades


# Optional safety net for a run killed before it could record its accounts: delete every
# throw-away user older than `older_than_hours`, by the signature only they carry.
#
# def purge_accounts(db, older_than_hours):
#     rows = db.sql(f"delete from users where email like 'jev-%@{DOMAIN}' "
#                   f"and created_at < now() - interval '{int(older_than_hours)} hours' returning email")
#     return [r["email"] for r in rows]
