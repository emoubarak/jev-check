"""Durable checks: a start path, a goal in plain English, and a verdict read from the database.

    from jevcheck import Check, result, has_any

    FREE_CANNOT_PUBLISH = Check(
        key="free_publish_blocked",
        title="a free account cannot publish a second project",
        persona="free", options=("live", "draft"),
        path="/projects/{draft}",
        goal="Publish this project. You are done when it is live, OR when the app refuses and says why.",
        verify=lambda ctx, state: result(
            db_says_no=not ctx.db.value(f"select published from projects where id = {ctx.db.lit(ctx.var('draft'))}"),
            user_was_told=has_any(state, "upgrade", "limit"),
        ),
        expect_action=True,   # a gate check that never clicked proves nothing
    )

    CHECKS = [FREE_CANNOT_PUBLISH]
"""

import importlib.util
import re
import sys
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Ctx:
    """What a check knows: the target, the datastore, and the accounts of this run."""

    base: str
    db: object
    account: object = None                         # the acting Account, None when anonymous
    accounts: dict = field(default_factory=dict)   # every persona of the run, by name

    def url(self, path):
        return self.base.rstrip("/") + fill(path, self.account)

    def var(self, name, persona=None):
        """A placeholder value: the acting account's by default, another persona's if named."""
        account = self.accounts[persona] if persona else self.account
        if account is None:
            raise KeyError(f"no account for {name!r} — this check is anonymous")
        values = account.placeholders()
        if name not in values:
            raise KeyError(f"{account.persona} has no var {name!r} (has: {', '.join(values)})")
        return values[name]


@dataclass
class Check:
    key: str
    title: str
    goal: object                     # str, or Callable[[Ctx], str]
    verify: Callable                 # (ctx, state) -> result(...)
    persona: str = None              # None: an anonymous visitor
    path: object = "/"               # str with {placeholders}, or Callable[[Ctx], str]
    options: tuple = ()              # passed to create_account for a throw-away persona
    local_storage: object = None     # dict (None value = remove) or Callable[[Ctx], dict], set before the run
    expect_action: bool = False      # fail if the agent never acted: "nothing happened" proves nothing then
    budget: float = None             # wall-clock seconds, when the default is wasteful
    viewport: str = None             # "phone", "tablet" or "390x844[@dpr]": emulate that screen
    layout: bool = False             # add a `layout_clean` verdict: nothing off-screen, nothing covered


def fill(path, account):
    """Substitute {placeholders} from the account; refuse to open a URL with one left over."""
    if account is not None:
        for key, value in account.placeholders().items():
            path = path.replace("{" + key + "}", str(value))
    left = re.findall(r"\{(\w+)\}", path)
    if left:
        have = ", ".join(account.placeholders()) if account else "none (anonymous)"
        raise SystemExit(f"unknown placeholder(s) {', '.join(left)} in {path!r} — this account has: {have}")
    return path


def result(**checks):
    """Every named boolean must hold. Name them after what they prove."""
    return {"passed": all(bool(v) for v in checks.values()), "checks": {k: bool(v) for k, v in checks.items()}}


def text_of(state):
    """Both observations: what was on screen when the model stopped, and what is there now."""
    return "\n".join((state.get(key) or {}).get("text") or "" for key in ("page", "page_at_decision"))


def has_any(state, *needles):
    low = text_of(state).lower()
    return any(n.lower() in low for n in needles)


def final_url(state):
    return (state.get("page") or {}).get("url") or ""


def actions(state):
    """Labels of what the agent actually did, in order."""
    return [h.get("action") or "" for h in state.get("history", [])]


def offered(state):
    """What the page let this account act on at the end — often the real answer to "is it gated?"."""
    return [(a["kind"], a.get("label") or "") for a in (state.get("page") or {}).get("actions", [])
            if a.get("kind") != "wait"]


def status(state):
    """jev's own last word: 'done', 'blocked' or 'ready' (stopped by a budget). Never the verdict."""
    return state.get("status")


def load(cfg):
    if not cfg.checks:
        raise SystemExit(f'{cfg.path.name}: set [audit] checks = "jevcheck_checks.py" to run an audit')
    if not cfg.checks.exists():
        raise SystemExit(f"checks file not found: {cfg.checks}")
    spec = importlib.util.spec_from_file_location("jevcheck_user_checks", cfg.checks)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(cfg.checks.parent))
    spec.loader.exec_module(module)
    found = list(getattr(module, "CHECKS", []))
    keys = [c.key for c in found]
    if dupes := {k for k in keys if keys.count(k) > 1}:
        raise SystemExit(f"duplicate check keys in {cfg.checks.name}: {', '.join(sorted(dupes))}")
    return found
