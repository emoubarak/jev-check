"""The datastore every verdict is read from — the STAGING one, never production.

The agent's "done" is a claim, not a result. A check passes when the database says the row
landed (or did not), so each adapter answers one question: run this SQL, give me the rows.

    kind = "sqlite"    path = "db.sqlite3"
    kind = "postgres"  dsn = "env:STAGING_DATABASE_URL"         (needs the `postgres` extra)
    kind = "supabase"  project_ref = "abcd…"  token = "env:SUPABASE_ACCESS_TOKEN"
    kind = "command"   command = "./staging-sql.sh"   (SQL on stdin, JSON rows on stdout)
    kind = "none"      no database: checks read the page only
"""

import json
import sqlite3
import subprocess
import urllib.error
import urllib.request

from .config import resolve


def lit(value):
    """A single-quoted SQL literal. Every value in a check is yours, but quoting is free."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


class Datastore:
    kind = "none"
    lit = staticmethod(lit)

    def sql(self, query):
        raise RuntimeError('no datastore configured ([datastore] kind = "none") — this check needs one')

    def one(self, query):
        rows = self.sql(query)
        return rows[0] if rows else None

    def value(self, query):
        row = self.one(query)
        return next(iter(row.values())) if row else None

    def describe(self):
        return self.kind


class Sqlite(Datastore):
    kind = "sqlite"

    def __init__(self, path):
        self.path = path

    def sql(self, query):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            try:
                cur = con.execute(query)
                rows = [dict(r) for r in cur.fetchall()] if cur.description else []
            except sqlite3.ProgrammingError as exc:
                if "one statement" not in str(exc):
                    raise
                con.executescript(query)
                rows = []
            con.commit()
            return rows
        finally:
            con.close()

    def describe(self):
        return f"sqlite {self.path}"


class Postgres(Datastore):
    kind = "postgres"

    def __init__(self, dsn):
        self.dsn = dsn

    def sql(self, query):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError:
            raise RuntimeError(
                "kind = \"postgres\" needs psycopg: run with `uv run --project <skill> --extra postgres jevcheck …`"
            ) from None
        with psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row) as con:
            cur = con.execute(query)
            return [dict(r) for r in cur.fetchall()] if cur.description else []

    def describe(self):
        host = self.dsn.split("@")[-1].split("/")[0] if "@" in self.dsn else "?"
        return f"postgres @ {host}"


class Supabase(Datastore):
    """SQL through the Supabase Management API: a personal access token, no DB password."""

    kind = "supabase"

    def __init__(self, ref, token):
        self.ref, self.token = ref, token

    def sql(self, query):
        request = urllib.request.Request(
            f"https://api.supabase.com/v1/projects/{self.ref}/database/query",
            method="POST",
            data=json.dumps({"query": query}).encode(),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
                     "User-Agent": "jevcheck"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.loads(response.read() or b"[]")
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"Supabase Management API {error.code}: {error.read().decode()[:400]}") from None
        if isinstance(body, dict):  # an error comes back as an object, not a row list
            raise RuntimeError(f"SQL failed: {body.get('message') or body}")
        return body

    def describe(self):
        return f"supabase project {self.ref}"


class Command(Datastore):
    """Anything else (MySQL, an ORM shell, an HTTP admin endpoint): your script gets the SQL on
    stdin and prints the rows as a JSON array of objects on stdout."""

    kind = "command"

    def __init__(self, command, cwd):
        self.command, self.cwd = command, cwd

    def sql(self, query):
        run = subprocess.run(self.command, shell=True, input=query, capture_output=True,
                             text=True, cwd=self.cwd, timeout=120)
        if run.returncode != 0:
            raise RuntimeError(f"datastore command failed ({run.returncode}): {(run.stderr or run.stdout)[:400]}")
        out = run.stdout.strip()
        return json.loads(out) if out else []

    def describe(self):
        return f"command `{self.command}`"


def open_store(cfg):
    ds = cfg.datastore
    kind = (ds.get("kind") or "none").lower()
    where = f"[datastore] of {cfg.path.name}"
    if kind == "none":
        return Datastore()
    if kind == "sqlite":
        return Sqlite(str((cfg.root / resolve(ds["path"], where)).resolve()))
    if kind == "postgres":
        return Postgres(resolve(ds["dsn"], where))
    if kind == "supabase":
        return Supabase(resolve(ds["project_ref"], where), resolve(ds["token"], where))
    if kind == "command":
        return Command(resolve(ds["command"], where), str(cfg.root))
    raise SystemExit(f"{where}: unknown kind {kind!r} (sqlite, postgres, supabase, command, none)")
