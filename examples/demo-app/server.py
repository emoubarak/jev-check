#!/usr/bin/env python3
"""Launchpad — a tiny app to try jevcheck on, with nothing to install.

    python3 server.py                  # http://127.0.0.1:8765, data in demo.db next to this file
    LAUNCHPAD_BUG=1 python3 server.py  # the same app with a silent billing regression in it

Free accounts may have ONE live project; publishing a second one opens an animated paywall
dialog (the kind an agent cannot see unless animations are killed). Pro accounts may publish
as many as they like. Sessions are an HttpOnly cookie. Standard library only.
"""

import hashlib
import html
import json
import os
import re
import secrets
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
DB = Path(os.environ.get("LAUNCHPAD_DB", HERE / "demo.db"))
FREE_LIVE_LIMIT = 1
# LAUNCHPAD_BUG=1 ships a regression on purpose, to watch the audit catch it: a free account
# publishing over its limit silently unpublishes its other project instead of being refused.
BUGGY = os.environ.get("LAUNCHPAD_BUG") == "1"
SESSIONS = {}  # token -> user id


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
    return f"pbkdf2${salt}${digest}"


def check_password(password, stored):
    try:
        _, salt, _ = stored.split("$")
    except ValueError:
        return False
    return secrets.compare_digest(hash_password(password, salt), stored)


def db():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.executescript("""
          create table if not exists users (
            id integer primary key, email text unique not null, password_hash text not null,
            plan text not null default 'free', created_at text not null default current_timestamp);
          create table if not exists projects (
            id integer primary key, user_id integer not null references users(id),
            name text not null, published integer not null default 0);
        """)


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title} · Launchpad</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font: 15px/1.5 system-ui, sans-serif; margin: 0; background: #f6f7f9; color: #1d2330; }}
  header {{ display: flex; justify-content: space-between; align-items: center; padding: 14px 28px;
           background: #fff; border-bottom: 1px solid #e3e6eb; }}
  main {{ max-width: 720px; margin: 32px auto; padding: 0 16px; }}
  .card {{ background: #fff; border: 1px solid #e3e6eb; border-radius: 10px; padding: 20px 24px; margin-bottom: 16px; }}
  .badge {{ font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 99px; background: #eef1f5; }}
  .live {{ background: #dcf5e6; color: #11663a; }}
  button, .button {{ font: inherit; padding: 8px 14px; border-radius: 8px; border: 1px solid #c9ced6;
                     background: #fff; cursor: pointer; text-decoration: none; color: inherit; }}
  .primary {{ background: #2748d8; border-color: #2748d8; color: #fff; }}
  input {{ font: inherit; width: 100%; box-sizing: border-box; padding: 8px 10px; margin: 4px 0 14px;
           border: 1px solid #c9ced6; border-radius: 8px; }}
  .error {{ color: #b3261e; }}
  .backdrop {{ position: fixed; inset: 0; background: rgba(20, 24, 33, .45); display: grid; place-items: center; }}
  [role=dialog] {{ background: #fff; border-radius: 12px; padding: 24px; max-width: 420px;
                   animation: enter .25s ease-out both; }}
  @keyframes enter {{ from {{ opacity: 0; transform: scale(.96); }} }}
  ul {{ padding: 0; list-style: none; }} li {{ display: flex; justify-content: space-between; padding: 8px 0; }}
</style></head><body>
<header><strong>Launchpad</strong><span>{nav}</span></header>
<main>{body}</main></body></html>"""


def page(title, body, user=None):
    nav = ""
    if user:
        nav = (f'<span class="badge">{html.escape(user["plan"].title())} plan</span> '
               f'{html.escape(user["email"])} <form method="post" action="/logout" style="display:inline">'
               '<button>Sign out</button></form>')
    return PAGE.format(title=html.escape(title), nav=nav, body=body)


LOGIN = """<div class="card"><h1>Sign in</h1>
<form method="post" action="/login">
  <label for="email">Email</label><input id="email" name="email" type="email" autocomplete="username" required>
  <label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required>
  {error}<button class="primary" type="submit">Sign in</button>
</form></div>"""

PROJECT_JS = """<script>
const id = %d;
async function toggle(action) {
  const res = await fetch(`/api/projects/${id}/${action}`, { method: 'POST' });
  if (res.ok) { location.reload(); return; }
  const body = await res.json();
  const wrap = document.createElement('div');
  wrap.className = 'backdrop';
  wrap.innerHTML = `<div role="dialog" aria-modal="true" aria-labelledby="t">
    <h2 id="t">Upgrade to Pro</h2><p>${body.error}</p>
    <a class="button primary" href="/app/billing">See the plans</a>
    <button onclick="this.closest('.backdrop').remove()">Close</button></div>`;
  document.body.appendChild(wrap);
}
</script>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "Launchpad/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("  launchpad: " + fmt % args + "\n")

    # ─── plumbing ──
    def user(self):
        cookie = self.headers.get("Cookie") or ""
        match = re.search(r"(?:^|;\s*)sid=([^;]+)", cookie)
        uid = SESSIONS.get(match.group(1)) if match else None
        if uid is None:
            return None
        with db() as con:
            return con.execute("select * from users where id = ?", (uid,)).fetchone()

    def send(self, status, body, content_type="text/html; charset=utf-8", headers=()):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, where, headers=()):
        self.send(303, "", headers=(("Location", where), *headers))

    def form(self):
        length = int(self.headers.get("Content-Length") or 0)
        return {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}

    # ─── routes ──
    def do_GET(self):
        path = urlparse(self.path).path
        user = self.user()
        if path == "/":
            return self.redirect("/app" if user else "/login")
        if path == "/login":
            return self.send(200, page("Sign in", LOGIN.format(error="")))
        if not user:
            return self.redirect("/login")
        if path == "/app":
            return self.dashboard(user)
        if path == "/app/billing":
            return self.send(200, page("Plans", '<div class="card"><h1>Plans</h1><p>Free: 1 live project. '
                                                'Pro: unlimited. Billing is not part of this demo.</p></div>', user))
        if m := re.fullmatch(r"/projects/(\d+)", path):
            return self.project(user, int(m.group(1)))
        self.send(404, page("Not found", '<div class="card"><h1>Page not found</h1></div>', user))

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/login":
            data = self.form()
            with db() as con:
                row = con.execute("select * from users where email = ?", (data.get("email", ""),)).fetchone()
            if not row or not check_password(data.get("password", ""), row["password_hash"]):
                error = '<p class="error">Incorrect email or password.</p>'
                return self.send(200, page("Sign in", LOGIN.format(error=error)))
            token = secrets.token_urlsafe(24)
            SESSIONS[token] = row["id"]
            return self.redirect("/app", headers=(("Set-Cookie", f"sid={token}; Path=/; HttpOnly; SameSite=Lax"),))
        if path == "/logout":
            return self.redirect("/login", headers=(("Set-Cookie", "sid=; Path=/; Max-Age=0"),))
        user = self.user()
        m = re.fullmatch(r"/api/projects/(\d+)/(publish|unpublish)", path)
        if not user or not m:
            return self.send(404, json.dumps({"error": "not found"}), "application/json")
        project_id, action = int(m.group(1)), m.group(2)
        with db() as con:
            project = con.execute("select * from projects where id = ? and user_id = ?",
                                  (project_id, user["id"])).fetchone()
            if not project:
                return self.send(404, json.dumps({"error": "Project not found."}), "application/json")
            if action == "publish" and user["plan"] == "free":
                live = con.execute("select count(*) from projects where user_id = ? and published = 1",
                                   (user["id"],)).fetchone()[0]
                if live >= FREE_LIVE_LIMIT and BUGGY:
                    # The silent failure: "make room" instead of refusing. The UI looks fine.
                    con.execute("update projects set published = 0 where user_id = ?", (user["id"],))
                elif live >= FREE_LIVE_LIMIT:
                    return self.send(402, json.dumps({"error": (
                        f"The Free plan includes {FREE_LIVE_LIMIT} live project. Unpublish another "
                        "project, or upgrade to Pro to publish this one.")}), "application/json")
            con.execute("update projects set published = ? where id = ?", (int(action == "publish"), project_id))
        self.send(200, json.dumps({"ok": True}), "application/json")

    # ─── pages ──
    def dashboard(self, user):
        with db() as con:
            rows = con.execute("select * from projects where user_id = ? order by id", (user["id"],)).fetchall()
        items = "".join(
            f'<li><a href="/projects/{r["id"]}">{html.escape(r["name"])}</a>'
            f'<span class="badge {"live" if r["published"] else ""}">{"Live" if r["published"] else "Draft"}</span></li>'
            for r in rows) or "<li>No projects yet.</li>"
        body = f'<div class="card"><h1>Your projects</h1><ul>{items}</ul></div>'
        self.send(200, page("Projects", body, user))

    def project(self, user, project_id):
        with db() as con:
            r = con.execute("select * from projects where id = ? and user_id = ?", (project_id, user["id"])).fetchone()
        if not r:
            return self.send(404, page("Not found", '<div class="card"><h1>Project not found</h1></div>', user))
        status = "Live" if r["published"] else "Draft"
        action = ('<button onclick="toggle(\'unpublish\')">Unpublish</button>' if r["published"]
                  else '<button class="primary" onclick="toggle(\'publish\')">Publish</button>')
        body = (f'<div class="card"><p><a href="/app">← All projects</a></p><h1>{html.escape(r["name"])}</h1>'
                f'<p>Status: <span class="badge {"live" if r["published"] else ""}">{status}</span></p>'
                f'{action}</div>' + PROJECT_JS % project_id)
        self.send(200, page(r["name"], body, user))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("LAUNCHPAD_PORT", 8765))
    init_db()
    print(f"Launchpad on http://127.0.0.1:{port} (data: {DB})" + ("  [LAUNCHPAD_BUG=1: gate broken]" if BUGGY else ""))
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
