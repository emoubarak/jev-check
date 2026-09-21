# Configuration reference

`jevcheck.toml` lives in your project, found from the working directory upward (or passed with
`--config`). Relative paths inside it are relative to the file. Any string value written as
`env:NAME` is read from the environment and is required.

## `[target]` — where the agent may go

| key | default | meaning |
|---|---|---|
| `base` | required | the **staging** origin, e.g. `https://staging.example.com` |
| `allowed_hosts` | required | hostnames the agent may drive. Anything else is refused, whatever `--base` says. |

`[target.local_guard]` (optional) applies when `--base` is `localhost`, `127.0.0.1` or `::1`:

| key | meaning |
|---|---|
| `file` | the env file your dev server reads (default `.env`) |
| `key` | the variable holding the backend URL, e.g. `VITE_API_URL`, `NEXT_PUBLIC_SUPABASE_URL` |
| `must_contain` | a string only the staging value contains (its host, its project id) |

A localhost front talks to whatever that file points at. The host whitelist cannot see it, so
this guard is what stops a local run from clicking through production data.

## `[browser]` — the state of the page before the agent arrives

| key | default | meaning |
|---|---|---|
| `seed_path` | `/` | page opened to write localStorage (any page of the app's origin) |
| `busy_selector` | `[aria-busy='true'], .animate-spin, .spinner` | the app's loading indicator. The agent's first look waits (max 8 s) until none is on the page |

`[browser.local_storage]` and `[browser.cookies]` are key/value tables written before the first
page, and again after every sign-out between personas. Typical entries: the cookie-consent
"accepted" flag (find the banner component and read which key it checks), the i18n language
key (`i18nextLng`, `NEXT_LOCALE`…), a "tour already seen" flag, a feature flag.

## `[login]` — how the harness signs in

The agent never touches the login form. The harness fills it over CDP with the native value
setter plus `input`/`change` events (so React/Vue/Svelte controlled inputs see the value), then
submits it and waits.

| key | default | meaning |
|---|---|---|
| `path` | `/login` | the login page |
| `username_selector` | `input[type=email], input[name=email], input[name=username]` | CSS selector of the email/username field |
| `password_selector` | `input[type=password]` | CSS selector of the password field |
| `submit_selector` | `""` | a button to click. Empty = `requestSubmit()` on the `<form>` around the password field |
| `success_path_prefix` | `""` | signed in once `location.pathname` starts with this (and is not the login path) |
| `success_selector` | `""` | …or once this element exists (an avatar, a sign-out button) |
| `error_pattern` | `incorrect\|invalid\|wrong\|not match\|confirm your email` | a JavaScript regex, case-insensitive. A match on the page is reported as the refusal reason |
| `timeout` | `45` | seconds |

With neither `success_*` set, any navigation away from the login path counts as success.
Two-step logins (email, then password on a second screen), SSO, magic links and CAPTCHAs are
not supported. Give staging a plain password login for test accounts.

## `[datastore]` — where verdicts are read (staging)

| `kind` | keys | notes |
|---|---|---|
| `sqlite` | `path` | stdlib, nothing to install |
| `postgres` | `dsn` (use `env:`) | run with `uv run --project <skill> --extra postgres jevcheck …` |
| `supabase` | `project_ref`, `token` (use `env:`) | SQL through the Management API: a personal access token, no DB password |
| `command` | `command` | any shell command: gets the SQL on stdin, prints a JSON array of row objects on stdout. Runs from the config's folder. MySQL, an ORM shell, an admin HTTP endpoint… |
| `none` | | checks can only read the page |

In a check or a hook, `db` offers `db.sql(q)` → list of dicts, `db.one(q)` → dict or None,
`db.value(q)` → first column of the first row, `db.lit(v)` → a quoted SQL literal.

**Staging only.** Hooks write to this database.

## `[accounts]` and `[personas.<name>]` — account states

| key | default | meaning |
|---|---|---|
| `accounts.hooks` | none | Python file with `create_account` / `delete_account` |
| `accounts.keep_hours` | `12` | a kept or orphaned throw-away account is deleted by the first run after this |

A persona **with** `email` is static: an account that already exists on staging.

```toml
[personas.admin]
email = "env:JEV_ADMIN_EMAIL"
password = "env:JEV_ADMIN_PASSWORD"
id = "42"                                  # optional, for {id}
vars = { org = "acme" }                    # optional, for {org} in paths
local_storage = { activeOrg = "acme" }     # optional, written after sign-in
description = "the staging admin"
```

A persona **without** `email` is throw-away: `create_account` makes it for the run and
`delete_account` removes it at the end, even on failure.

### Hooks API (`jevcheck_hooks.py`)

```python
def create_account(persona: str, run_id: str, db, options: set[str]) -> dict:
    # options: the --with values (ask) or the union of Check.options (audit) for this persona
    return {
        "id": "...",                  # optional, {id}
        "email": "...",               # required
        "password": "...",            # required
        "vars": {"draft": 123},       # optional, {draft} in paths, ctx.var("draft") in checks
        "local_storage": {...},       # optional, written after sign-in (active workspace, …)
    }

def delete_account(account: dict, db) -> None:
    # account: persona, id, email, vars, local_storage, run_id (never the password)
    ...

def purge_accounts(db, older_than_hours: float) -> list[str]:   # optional
    # safety net for a run killed before it recorded its accounts: delete every throw-away
    # user older than that, by the signature only they carry. Return their emails.
    ...
```

Rules for hooks:

- **Write each state the way production writes it.** Find the code that produces it (the
  payment webhook handler, the admin "grant" action, the expiry job) and copy its fields. A
  state no real path can reach makes the agent report bugs that do not exist.
- Hash the password the way your auth does, or call your staging admin API
  (`auth.admin.createUser` on Supabase, `/admin/users` on your own backend…). Create the
  account **confirmed** if staging requires email confirmation. The agent cannot read an inbox.
- Use an unroutable domain (`@example.invalid`) and a recognisable prefix, so
  `purge_accounts` can never match a real user.
- Every created account is recorded in `.jevcheck/ledger.json` right away and removed from it on
  deletion.

## `[audit]`

| key | meaning |
|---|---|
| `checks` | Python file defining `CHECKS = [Check(...), …]` (see `checks.md`) |

## `[run]`

| key | default | meaning |
|---|---|---|
| `artifacts` | `.jevcheck` | runs (`runs/<kind>-<timestamp>/`, `runs/latest`), the ledger. Gitignore it |
| `budget` | `180` | wall-clock seconds per goal |
| `env_file` | none | a `KEY=VALUE` file loaded without overriding the shell (keep it gitignored) |

## Environment

| variable | needed for |
|---|---|
| `TYPESAFE_API_KEY` | always: jev's decision model |
| `TYPESAFE_MODEL` | optional, default `jev-latest` |
| `TEXT_MODEL_API_KEY` | only goals that make the agent type (search boxes, forms) |
| `TEXT_MODEL_BASE_URL`, `TEXT_MODEL`, `TEXT_MODEL_REASONING` | any OpenAI-compatible endpoint for that text model |
| `BH_CHROME_PATH` | a browser binary the search does not find |
| `BH_TELEMETRY` | browser-harness telemetry. jevcheck sets it to `0` unless you set it |
