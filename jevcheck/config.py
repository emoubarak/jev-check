"""jevcheck.toml: where the agent may go, how it signs in, what the answers are checked against.

The file lives in YOUR project (not in the skill), found in the working directory or any
parent. Every relative path inside it is relative to the file itself.
"""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

CONFIG_NAME = "jevcheck.toml"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def resolve(value, where=""):
    """`env:NAME` → that environment variable (required); anything else unchanged."""
    if isinstance(value, str) and value.startswith("env:"):
        name = value[4:].strip()
        got = (os.environ.get(name) or "").strip()
        if not got:
            raise SystemExit(f"{name} is not set (referenced as env:{name}{' in ' + where if where else ''})")
        return got
    return value


def load_env_file(path):
    """KEY=VALUE lines, without overriding what the shell already exports."""
    if not path or not Path(path).exists():
        return
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if value:
            os.environ.setdefault(key.strip(), value)


@dataclass
class Login:
    path: str = "/login"
    username_selector: str = "input[type=email], input[name=email], input[name=username]"
    password_selector: str = "input[type=password]"
    submit_selector: str = ""          # empty: requestSubmit() on the form around the password
    success_path_prefix: str = ""      # signed in once the URL path starts with this…
    success_selector: str = ""         # …or once this element exists
    error_pattern: str = "incorrect|invalid|wrong|not match|confirm your email"
    timeout: float = 45


@dataclass
class Config:
    path: Path
    base: str
    allowed_hosts: tuple
    local_guard: dict
    seed_path: str
    local_storage: dict
    cookies: dict
    busy_selector: str
    login: Login
    datastore: dict
    hooks: Path | None
    keep_hours: float
    personas: dict
    checks: Path | None
    artifacts: Path
    budget: float
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def root(self):
        return self.path.parent


def find(start=None):
    here = Path(start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / CONFIG_NAME).is_file():
            return folder / CONFIG_NAME
    return None


def load(path=None):
    path = Path(path).resolve() if path else find()
    if not path or not path.is_file():
        raise SystemExit(
            f"no {CONFIG_NAME} here or in any parent folder. Run `jevcheck init` in your project, "
            "or pass --config PATH."
        )
    raw = tomllib.loads(path.read_text())
    root = path.parent
    run = raw.get("run", {})
    load_env_file(root / run["env_file"] if run.get("env_file") else None)

    target = raw.get("target", {})
    base = str(target.get("base") or "").rstrip("/")
    if not base:
        raise SystemExit(f"{path}: [target] base is required (your STAGING origin)")
    hosts = tuple(h.lower() for h in target.get("allowed_hosts", []))
    if not hosts:
        raise SystemExit(f"{path}: [target] allowed_hosts is empty. List the staging host(s) the "
                         "agent may drive; nothing else will ever be reachable.")

    browser = raw.get("browser", {})
    accounts = raw.get("accounts", {})
    audit = raw.get("audit", {})
    login = Login(**{k: v for k, v in raw.get("login", {}).items() if k in Login.__dataclass_fields__})

    def rel(value):
        return (root / value).resolve() if value else None

    return Config(
        path=path,
        base=base,
        allowed_hosts=hosts,
        local_guard=target.get("local_guard") or {},
        seed_path=browser.get("seed_path", "/"),
        local_storage=dict(browser.get("local_storage", {})),
        cookies=dict(browser.get("cookies", {})),
        busy_selector=browser.get("busy_selector", "[aria-busy='true'], .animate-spin, .spinner"),
        login=login,
        datastore=dict(raw.get("datastore", {"kind": "none"})),
        hooks=rel(accounts.get("hooks")),
        keep_hours=float(accounts.get("keep_hours", 12)),
        personas=dict(raw.get("personas", {})),
        checks=rel(audit.get("checks")),
        artifacts=rel(run.get("artifacts", ".jevcheck")),
        budget=float(run.get("budget", 180)),
        raw=raw,
    )


def guard_target(cfg, base):
    """A whitelist, not a blacklist: a typo must fail closed, and production must never be
    reachable from here whatever the flag."""
    host = (urlparse(base).hostname or "").lower()
    if host not in cfg.allowed_hosts:
        raise SystemExit(
            f"refusing to drive an agent against {host or base!r}.\n"
            f"Allowed by {cfg.path.name}: {', '.join(cfg.allowed_hosts)}. An agent clicking "
            "through production is not a test, it is an incident."
        )
    if host in LOCAL_HOSTS:
        guard_local_backend(cfg)


def guard_local_backend(cfg):
    """A localhost front talks to whatever its env file points at. The host whitelist cannot
    see that, so when [target.local_guard] is set, assert that backend is the staging one."""
    g = cfg.local_guard
    if not g:
        return
    env_file = (cfg.root / g.get("file", ".env")).resolve()
    key, needle = g.get("key", ""), g.get("must_contain", "")
    text = env_file.read_text() if env_file.exists() else ""
    line = next((l for l in text.splitlines() if l.strip().startswith(f"{key}=")), "")
    if not key or not needle or needle not in line:
        found = line.split("=", 1)[-1].strip() if line else "<unset>"
        raise SystemExit(
            f"refusing a localhost run: {env_file} has {key}={found}, which does not contain "
            f"{needle!r}. Point the local dev server at staging first."
        )
