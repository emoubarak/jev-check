"""A dedicated automation Chrome, so the agent never touches your real browser profile.

browser-harness drives whatever Chrome it discovers, and by default that is your OWN running
instance on the default profile: the agent would inherit your real cookies (production
sessions included) and write into the browser you use every day. `BU_CDP_URL` is the
documented escape hatch: attach to a Chrome launched here on a throw-away --user-data-dir.
It also sidesteps Chrome's per-session "Allow remote debugging" prompt and the
default-profile DevTools lockdown.
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

BINARIES = (
    "google-chrome-stable",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "brave-browser",
    "microsoft-edge-stable",
)
MAC_APPS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)


def find_chrome():
    """BH_CHROME_PATH / CHROME_PATH first, like browser-harness itself."""
    for key in ("BH_CHROME_PATH", "CHROME_PATH"):
        raw = (os.environ.get(key) or "").strip()
        if raw and Path(raw).exists():
            return raw
    for name in BINARIES:
        if found := shutil.which(name):
            return found
    for app in MAC_APPS:
        if Path(app).exists():
            return app
    raise RuntimeError("no Chrome/Chromium/Brave found — install one or export BH_CHROME_PATH=/path/to/chrome")


def free_port(start=9333, tries=200):
    # each process starts at its own offset: two runs launched together both saw 9333 free and the second Chrome failed
    for port in (start + (os.getpid() + i) % tries for i in range(tries)):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"no free port in {start}..{start + tries - 1}")


def launch(profile_dir, port, *, headless=False, log_path=None):
    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        find_chrome(),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-search-engine-choice-screen",
        "--disable-features=Translate,MediaRouter,OptimizationHints",
        "--disable-backgrounding-occluded-windows",
        # jev drives an owned BACKGROUND tab: without these the renderer is throttled there,
        # animations stall and a screenshot waits for a frame that never comes.
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
        "--password-store=basic",
        "--use-mock-keychain",
        "--window-size=1120,900",
    ]
    if headless:
        args += ["--headless=new", "--disable-gpu"]
    args.append("about:blank")
    log = open(log_path, "ab") if log_path else subprocess.DEVNULL
    return subprocess.Popen(args, stdout=log, stderr=log, start_new_session=True)


def endpoint(port):
    """The DevTools websocket URL once Chrome is listening, else None."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
            return json.loads(r.read())["webSocketDebuggerUrl"]
    except (urllib.error.URLError, OSError, KeyError, ValueError):
        return None


def wait_ready(port, proc=None, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ws := endpoint(port):
            return ws
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(
                f"Chrome exited with code {proc.returncode} before opening port {port} — an orphan "
                "browser may still hold the profile (`pgrep -af remote-debugging-port`)"
            )
        time.sleep(0.2)
    raise RuntimeError(f"Chrome did not open the DevTools port {port} within {timeout}s")


def stop(proc):
    """Signal the whole process group, not just the pid we spawned.

    `google-chrome` / `brave-browser` on PATH is often a shell wrapper that runs the real
    binary as a CHILD: terminating the wrapper leaves a browser holding the DevTools port and
    the profile lock, and the next run finds the port busy and its own Chrome exiting 0 as it
    hands off to that orphan. `start_new_session=True` makes the wrapper a group leader, so one
    killpg takes the whole tree down.
    """
    if proc is None:
        return
    try:
        group = os.getpgid(proc.pid)
    except (ProcessLookupError, AttributeError):
        group = None
    for sig in (signal.SIGTERM, getattr(signal, "SIGKILL", signal.SIGTERM)):
        if group is not None:
            try:
                os.killpg(group, sig)
            except ProcessLookupError:
                break
        elif proc.poll() is None:
            proc.send_signal(sig)
        try:
            proc.wait(timeout=10 if sig == signal.SIGTERM else 5)
        except subprocess.TimeoutExpired:
            continue
        deadline = time.monotonic() + 5
        while group is not None and time.monotonic() < deadline:
            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
        if sig is not signal.SIGTERM:
            return


def profiles_in_use():
    """--user-data-dir of every browser running right now (Linux /proc; empty elsewhere)."""
    used = set()
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = cmdline.read_bytes().split(b"\0")
        except OSError:
            continue
        for arg in args:
            if arg.startswith(b"--user-data-dir="):
                used.add(Path(arg.split(b"=", 1)[1].decode(errors="replace")).resolve())
    return used


def purge_profiles(runs_dir, older_than_hours=None):
    """Remove kept browser profiles under `<runs>/<run>/chrome-profile` (~75 MB each): every one
    when `older_than_hours` is None, else only the older ones — never one a running browser
    still has open. Returns the run folders cleaned."""
    in_use = profiles_in_use()
    cutoff = None if older_than_hours is None else time.time() - older_than_hours * 3600
    cleaned = []
    for profile in sorted(Path(runs_dir).glob("*/chrome-profile")):
        if profile.resolve() in in_use:
            continue
        if cutoff is not None and profile.stat().st_mtime > cutoff:
            continue
        shutil.rmtree(profile, ignore_errors=True)
        cleaned.append(profile.parent.name)
    return cleaned
