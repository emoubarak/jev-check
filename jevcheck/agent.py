"""One goal, one agent, two observations — and a page the agent can actually see.

Three things here are why a naive "point jev at the app" run reports ghosts:

* Animated dialogs are invisible. Radix/Headless UI/MUI animate a dialog in from
  `opacity: 0`, and in an owned background tab that enter animation never advances: the
  paywall sits at opacity 0, `checkVisibility({checkOpacity: true})` stays false, and the
  agent sees a page with no modal on it. `disable_animations` lands every animation on its
  end state at once, for the current document and every later one.
* The first observation lands on the loader. `settle` waits (bounded) for the spinners to go
  before the agent reads the page, the same refresh jev does itself when a page changes.
* One observation misses something. The page the model decided on carries what was on screen
  at that instant (a toast saying why you were refused is gone seconds later); a fresh one
  carries what survived (an app that hydrates late leaves the first read as an empty shell).
  `run_goal` keeps both: `state["page_at_decision"]` and `state["page"]`.
"""

import os
import time


def prepare_env():
    """Before browser_harness is imported anywhere: it reads these at import time."""
    os.environ.setdefault("BU_NAME", "jevcheck")       # our own daemon, not anyone else's
    os.environ.setdefault("BH_TELEMETRY", "0")         # staging URLs stay on this machine


def start_browser(chrome, run_dir, *, headed, port_start=9333):
    """Launch the dedicated Chrome and point browser-harness at it. Returns the process."""
    prepare_env()
    port = chrome.free_port(port_start)
    proc = chrome.launch(run_dir / "chrome-profile", port, headless=not headed,
                         log_path=run_dir / "chrome.log")
    try:
        chrome.wait_ready(port, proc)
    except Exception:
        chrome.stop(proc)  # never leave a browser behind that nothing will attach to
        raise
    os.environ["BU_CDP_URL"] = f"http://127.0.0.1:{port}"
    ensure_daemon()
    return proc


def ensure_daemon():
    from browser_harness import admin

    # A daemon left over from an earlier run still points at that run's dead port.
    if admin.daemon_alive() and not admin.daemon_browser_ready():
        admin.restart_daemon()
    admin.ensure_daemon()


KILL_ANIMATIONS = (
    "(() => { const s = document.createElement('style');"
    " s.textContent = '*,*::before,*::after{animation-duration:0s !important;"
    "animation-delay:0s !important;transition-duration:0s !important;"
    "transition-delay:0s !important}';"
    " (document.head || document.documentElement).appendChild(s); return true; })()"
)


def disable_animations(agent):
    try:
        from browser_harness.helpers import cdp

        cdp("Page.addScriptToEvaluateOnNewDocument", session_id=agent.browser.session,
            source=f"try {{ {KILL_ANIMATIONS} }} catch (e) {{}}")
        cdp("Runtime.evaluate", session_id=agent.browser.session,
            expression=KILL_ANIMATIONS, returnByValue=True)
    except Exception:
        pass  # a visibility aid, never a reason to fail a run


def settle(agent, busy_selector, timeout=8.0):
    if not busy_selector:
        return
    try:
        import json

        from browser_harness.helpers import cdp

        probe = f"!!document.querySelector({json.dumps(busy_selector)})"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            busy = cdp("Runtime.evaluate", session_id=agent.browser.session, returnByValue=True,
                       expression=probe).get("result", {}).get("value")
            if not busy:
                break
            time.sleep(0.15)
        agent.state["page"] = agent.browser.observe(screenshot=agent.screenshots)
    except Exception:
        pass


# jev reads what is on screen: on a phone, the target of a novice goal often sits below the fold, and the agent answered
# `blocked` after zero or one scroll (issue #2). Spelling it out in every goal fixed the runs where it was tried.
SCROLL_HINT = ("\n\n(The page can continue below the visible screen: before deciding that something is missing, "
               "scroll down until the end of the page.)")


def run_goal(url, goal, *, budget, busy_selector="", record_dir=None, before_close=None, echo=print,
             viewport=None, probe_layout=True):
    """Drive jev from `url` toward `goal`. Returns (state, error) — state is None only if the
    page never loaded. `before_close(agent)` runs while the tab still exists (screenshots).

    `viewport` is (width, height, dpr) to emulate a screen. `probe_layout` reads the geometry
    of the page the agent ended on into `state["layout"]` (see layout.py)."""
    from jev_ultrafast import Agent

    from . import layout

    started = time.monotonic()
    agent, state, error = None, None, None
    try:
        agent = Agent(url, goal + SCROLL_HINT, record_dir=str(record_dir) if record_dir else None)
        disable_animations(agent)
        if viewport:
            layout.apply_viewport(agent.browser.session, viewport)
        settle(agent, busy_selector)
        deadline = started + budget
        repeats, previous, resumed = 0, None, 0
        while True:
            for state in agent.run():
                last = (state["history"] or [{}])[-1]
                echo(f"  {state['elapsed_ms']:>7} ms  {state['status']:<9} {(last.get('action') or '')[:64]}")
                if time.monotonic() > deadline:
                    error = f"wall-clock budget of {budget:g}s exhausted"
                    break
                # the same action on the same URL again and again (an element gone after a navigation, issue #7): stop and
                # say it looped, instead of burning the whole model-call budget and answering "ready"
                key = (last.get("action"), (state.get("page") or {}).get("url"))
                repeats = repeats + 1 if key == previous and key[0] and key[0] != "Scroll down" else 0
                previous = key
                if repeats >= 5:
                    error = f"loop: the agent repeated « {key[0][:60]} » 6 times without the page changing"
                    break
            # « blocked » while the page can still scroll down: jev's model gave up before the end of a long page (issue #2).
            # Scroll one screen ourselves and let the agent look again, up to 8 times; a blocked at the bottom stands.
            if error or (state or {}).get("status") != "blocked" or resumed >= 8 or not can_scroll_down(agent):
                break
            resumed += 1
            scroll_down(agent)
            echo("          resumed   (blocked before the end of the page: scrolled one screen)")
            agent.state["status"], agent.state["decision"] = "ready", None
            agent.state["page"] = agent.browser.observe(screenshot=False)
    except Exception as exc:  # jev's own action/call budget raises ValueError: keep the partial state
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if agent is not None:
            state = agent.snapshot()
            state["page_at_decision"] = state["page"]
            for _ in range(4):  # a fresh read can land mid-transition and come back blank
                try:
                    fresh = agent.browser.observe(screenshot=False)
                except Exception:
                    break
                if (fresh.get("text") or "").strip():
                    state["page"] = fresh
                    break
                time.sleep(0.3)
            if probe_layout:
                state["layout"] = layout.probe(agent.browser.session)
            if before_close:
                try:
                    before_close(agent)
                except Exception as exc:
                    echo(f"  !! {type(exc).__name__}: {exc}")
            try:
                agent.close()
            except Exception as exc:  # the browser connection already dropped: keep the partial result (issue #6)
                echo(f"  !! closing the browser failed ({type(exc).__name__}: {exc}); partial result kept")
    if state is not None:
        state["elapsed_s"] = round(time.monotonic() - started, 1)
    return state, error



def can_scroll_down(agent):
    """The page (not an inner panel) still has content below the screen."""
    from browser_harness.helpers import cdp
    try:
        r = cdp("Runtime.evaluate", session_id=agent.browser.session, returnByValue=True,
                expression="(()=>{const e=document.scrollingElement||document.documentElement;return e.scrollTop+innerHeight<e.scrollHeight-4})()")
        return bool(r.get("result", {}).get("value"))
    except Exception:
        return False


def scroll_down(agent):
    from browser_harness.helpers import cdp
    try:
        cdp("Runtime.evaluate", session_id=agent.browser.session, expression="window.scrollBy(0, Math.round(innerHeight*0.85))")
        time.sleep(0.4)
    except Exception:
        pass

def dumpable(state):
    """The state without the heavy parts (element tables, screenshots), for state.json."""
    if state is None:
        return None
    out = {k: v for k, v in state.items() if k != "elements"}
    for key in ("page", "page_at_decision"):
        if key in state:
            out[key] = {k: v for k, v in state[key].items() if k != "screenshot"}
    return out
