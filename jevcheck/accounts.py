"""Personas: the account states the agent signs in as.

Two kinds, declared under [personas.<name>] in jevcheck.toml:

* static     — an account that already exists on staging: `email` and `password` (use
               `env:NAME`), plus optional `vars` for path placeholders.
* throw-away — no `email`: your hooks file creates the account for the run, in the exact
               shape your production code writes, and deletes it at the end (on failure too).

Throw-away accounts go into a local ledger the moment they exist. A run that was killed, or
kept with --keep, leaves its entries there, and every later run deletes the ones older than
[accounts] keep_hours on its way in, so a leftover never outlives a working day.
"""

import importlib.util
import json
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field

from .config import resolve


@dataclass
class Account:
    persona: str
    email: str
    password: str
    id: str = ""
    vars: dict = field(default_factory=dict)
    local_storage: dict = field(default_factory=dict)
    throwaway: bool = False
    run_id: str = ""
    created_at: float = field(default_factory=time.time)

    def placeholders(self):
        return {"id": self.id, "email": self.email, **{k: v for k, v in self.vars.items()}}

    def public(self):
        """What the ledger keeps: everything delete_account needs, never the password."""
        return {k: v for k, v in asdict(self).items() if k != "password"}


def load_hooks(cfg, required=False):
    if not cfg.hooks:
        if required:
            raise SystemExit(f"{cfg.path.name}: a throw-away persona needs [accounts] hooks = \"jevcheck_hooks.py\"")
        return None
    if not cfg.hooks.exists():
        raise SystemExit(f"hooks file not found: {cfg.hooks}")
    spec = importlib.util.spec_from_file_location("jevcheck_user_hooks", cfg.hooks)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(cfg.hooks.parent))
    spec.loader.exec_module(module)
    return module


def persona_config(cfg, persona):
    if persona not in cfg.personas:
        known = ", ".join(cfg.personas) or "none declared"
        raise SystemExit(f"unknown persona {persona!r} — {cfg.path.name} declares: {known}")
    return cfg.personas[persona]


def provision(cfg, db, persona, run_id, options=()):
    pc = persona_config(cfg, persona)
    where = f"[personas.{persona}]"
    if pc.get("email"):
        if options:
            print(f"  note: --with {', '.join(options)} ignored — {persona} is a static account")
        return Account(persona=persona, email=resolve(pc["email"], where),
                       password=resolve(pc.get("password", ""), where), id=str(pc.get("id", "")),
                       vars=dict(pc.get("vars", {})), local_storage=dict(pc.get("local_storage", {})),
                       run_id=run_id)
    hooks = load_hooks(cfg, required=True)
    if not hasattr(hooks, "create_account"):
        raise SystemExit(f"{cfg.hooks.name} has no create_account(persona, run_id, db, options)")
    made = hooks.create_account(persona, run_id=run_id, db=db, options=set(options))
    if not made or not made.get("email") or "password" not in made:
        raise RuntimeError(f"create_account({persona!r}) must return a dict with email and password, got {made!r}")
    account = Account(persona=persona, email=made["email"], password=made["password"],
                      id=str(made.get("id", "")), vars=dict(made.get("vars", {})),
                      local_storage={**pc.get("local_storage", {}), **made.get("local_storage", {})},
                      throwaway=True, run_id=run_id)
    ledger_add(cfg, account)
    return account


def teardown(cfg, db, account):
    """Delete a throw-away account. A static one is never touched."""
    if not account.throwaway:
        return False
    hooks = load_hooks(cfg, required=True)
    hooks.delete_account(account.public(), db=db)
    ledger_remove(cfg, account.run_id, account.persona, account.id or account.email)
    return True


# ─────────────────────────────────────────────────────────────── ledger ──

def _ledger_path(cfg):
    cfg.artifacts.mkdir(parents=True, exist_ok=True)
    return cfg.artifacts / "ledger.json"


@contextmanager
def _locked(cfg):
    cfg.artifacts.mkdir(parents=True, exist_ok=True)
    lock = open(cfg.artifacts / "ledger.lock", "w")
    try:
        try:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
        except ImportError:  # Windows: runs rarely overlap; the ledger is advisory
            pass
        path = _ledger_path(cfg)
        entries = json.loads(path.read_text()) if path.exists() else []
        box = {"entries": entries}
        yield box
        path.write_text(json.dumps(box["entries"], indent=1))
    finally:
        lock.close()


def ledger_add(cfg, account):
    with _locked(cfg) as box:
        box["entries"].append(account.public())


def ledger_remove(cfg, run_id, persona, key):
    with _locked(cfg) as box:
        box["entries"] = [e for e in box["entries"]
                          if not (e["run_id"] == run_id and e["persona"] == persona
                                  and (e.get("id") or e.get("email")) == key)]


def purge(cfg, db, everything=False):
    """Delete throw-away accounts older than keep_hours (or all of them), and return their emails.

    A run in progress is never touched by the default: its accounts are seconds old. `everything`
    is `jevcheck cleanup --all` — never while an audit is running.
    """
    cutoff = time.time() - cfg.keep_hours * 3600
    with _locked(cfg) as box:
        due = [e for e in box["entries"] if everything or e["created_at"] < cutoff]
    gone = []
    if due:
        hooks = load_hooks(cfg, required=True)
        for entry in due:
            try:
                hooks.delete_account(entry, db=db)
                gone.append(entry["email"])
            except Exception as exc:
                print(f"  could not delete {entry['email']}: {exc}")
                continue
            ledger_remove(cfg, entry["run_id"], entry["persona"], entry.get("id") or entry.get("email"))
    # The safety net for a run killed before its ledger write: a signature-based sweep in the
    # hooks, if you wrote one (e.g. every `jev-*@example.invalid` user older than N hours).
    hooks = load_hooks(cfg) if cfg.hooks else None
    if hooks and hasattr(hooks, "purge_accounts"):
        gone += list(hooks.purge_accounts(db=db, older_than_hours=0 if everything else cfg.keep_hours) or [])
    return gone
