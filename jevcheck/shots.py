"""Screenshots for a ticket: take them on the agent's tab, publish them where issues show them.

    jevcheck ask … --shot after.png --shot-selector "[role=dialog]"
    jevcheck publish-shots 42 before=before.png after=after.png
    → prints a "Before | After" markdown table to paste into issue #42

Published to an orphan branch (default `jevcheck-assets`, `issues/<n>/`) through the GitHub
API (`gh api`), never through your working tree. Orphan, so it carries none of your code
history; if your CI only runs on your main branches, pushing there starts nothing. In a
private repo the images render for signed-in members only, which is the point.
"""

import base64
import json
import subprocess
import sys
import time
from pathlib import Path


def save_shot(cdp_session, path, selector=""):
    """A PNG of what the account sees now, cropped to `selector` (plus a margin) if given.

    jev opens its tab in the BACKGROUND, and a background tab composites no frames: a capture
    there waits forever. It is brought to the front first (our own Chrome, not yours), and
    given 30 s instead of browser-harness's 5 s default, which a software rasteriser misses.
    """
    from browser_harness.helpers import cdp

    params = {"format": "png"}
    if selector:
        rect = cdp(
            "Runtime.evaluate", session_id=cdp_session, returnByValue=True,
            expression=(
                "(() => { const e = document.querySelector(" + json.dumps(selector) + ");"
                " if (!e) return null; e.scrollIntoView({block: 'center'});"
                " const r = e.getBoundingClientRect();"
                " return {x: r.x + scrollX, y: r.y + scrollY, w: r.width, h: r.height}; })()"
            ),
        ).get("result", {}).get("value")
        if rect and rect["w"] and rect["h"]:
            pad = 16
            params["clip"] = {
                "x": max(0, rect["x"] - pad), "y": max(0, rect["y"] - pad),
                "width": rect["w"] + 2 * pad, "height": rect["h"] + 2 * pad, "scale": 1,
            }
        else:
            print(f"  (shot: {selector!r} is not on the page — full page instead)")
    cdp("Page.bringToFront", session_id=cdp_session)
    time.sleep(0.4)
    data = cdp("Page.captureScreenshot", session_id=cdp_session, _response_timeout=30, **params)["data"]
    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(data))
    cropped = f", cropped to {selector}" if "clip" in params else ""
    print(f"shot    : {out} ({out.stat().st_size // 1024} KB{cropped})")


# ───────────────────────────────────────────────────────────── publishing ──

def _gh(repo, method, path, payload=None, ok_404=False):
    args = ["gh", "api", "-X", method, f"repos/{repo}/{path}"]
    if payload is not None:
        args += ["--input", "-"]
    run = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                         capture_output=True, text=True)
    if run.returncode != 0:
        if ok_404 and "404" in (run.stderr + run.stdout):
            return None
        raise RuntimeError(f"gh api {method} {path} failed: {(run.stderr or run.stdout)[:400]}")
    return json.loads(run.stdout) if run.stdout.strip() else {}


def current_repo():
    run = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                         capture_output=True, text=True)
    if run.returncode != 0 or not run.stdout.strip():
        raise SystemExit("could not tell which GitHub repo this is — pass --repo owner/name (and check `gh auth status`)")
    return run.stdout.strip()


def ensure_branch(repo, branch):
    """Create the orphan branch once: a README, a root commit with no parent, a ref."""
    if _gh(repo, "GET", f"git/ref/heads/{branch}", ok_404=True):
        return
    readme = (f"# {branch}\n\nScreenshots referenced from issues (before / after a fix), one folder "
              "per issue: `issues/<number>/`. Written by jevcheck. Orphan branch: no code, no CI.\n")
    blob = _gh(repo, "POST", "git/blobs", {"content": readme, "encoding": "utf-8"})["sha"]
    tree = _gh(repo, "POST", "git/trees", {"tree": [
        {"path": "README.md", "mode": "100644", "type": "blob", "sha": blob}]})["sha"]
    commit = _gh(repo, "POST", "git/commits", {"message": f"chore: {branch} (screenshots for issues)",
                                               "tree": tree, "parents": []})["sha"]
    _gh(repo, "POST", "git/refs", {"ref": f"refs/heads/{branch}", "sha": commit})
    print(f"created orphan branch {branch} in {repo}", file=sys.stderr)


def upload(repo, branch, issue, name, local_path):
    remote = f"issues/{issue}/{name}"
    existing = _gh(repo, "GET", f"contents/{remote}?ref={branch}", ok_404=True)
    payload = {
        "message": f"issue #{issue}: {name}",
        "content": base64.b64encode(Path(local_path).read_bytes()).decode(),
        "branch": branch,
    }
    if existing and existing.get("sha"):
        payload["sha"] = existing["sha"]  # replace an earlier shot of the same name
    _gh(repo, "PUT", f"contents/{remote}", payload)
    # The blob URL with ?raw=true: for a signed-in member, GitHub redirects it to the raw file,
    # the same way images in a private README render.
    return f"https://github.com/{repo}/blob/{branch}/{remote}?raw=true"


def markdown(urls):
    """Before/after side by side when both exist; otherwise one image per line."""
    if set(urls) == {"before", "after"}:
        return ("| Before | After |\n|---|---|\n"
                f"| ![before]({urls['before']}) | ![after]({urls['after']}) |")
    return "\n\n".join(f"**{k}**\n\n![{k}]({u})" for k, u in urls.items())


def publish(issue, pairs, repo=None, branch="jevcheck-assets"):
    repo = repo or current_repo()
    ensure_branch(repo, branch)
    urls = {}
    for pair in pairs:
        name, _, path = pair.partition("=")
        if not path or not Path(path).expanduser().exists():
            raise SystemExit(f"not a file: {pair!r} (expected name=path.png)")
        path = Path(path).expanduser()
        urls[name] = upload(repo, branch, issue, f"{name}{path.suffix or '.png'}", path)
        print(f"uploaded {name}: {urls[name]}", file=sys.stderr)
    print(markdown(urls))
    return 0
