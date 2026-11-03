"""Sign the browser in the way the app does, because jev cannot type a password.

jev's observer excludes `password`, `file` and `hidden` inputs by design, so no goal can ever
sign in. The harness fills the real login form over CDP instead and lets the app's own client
store its session (cookie, localStorage, IndexedDB — whatever the app uses). No hand-made
token, no guessed storage shape. Every tab in the dedicated profile then shares that session,
which is also how one run switches between accounts.

Everything here talks to an explicit CDP session id, never to "the active tab".
"""

import json
import time
from urllib.parse import urlparse


def _cdp():
    from browser_harness.helpers import cdp

    return cdp


def origin_of(url):
    return "{0.scheme}://{0.netloc}".format(urlparse(url))


def evaluate(session, expression, await_promise=False):
    response = _cdp()(
        "Runtime.evaluate", session_id=session, expression=expression,
        returnByValue=True, awaitPromise=await_promise,
    )
    if response.get("exceptionDetails"):
        details = response["exceptionDetails"]
        raise RuntimeError(
            f"JS failed: {details.get('text')} "
            f"{details.get('exception', {}).get('description', '')[:200]}"
        )
    return response.get("result", {}).get("value")


def open_tab(url, timeout=45):
    """A background tab at `url`, already loaded. Returns (target_id, session_id).

    Created at about:blank and then navigated, the way jev's own Browser does: a target
    created straight at the URL reports readyState "complete" for the initial about:blank,
    whose opaque origin has no localStorage at all.
    """
    cdp = _cdp()
    target = cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
    try:
        session = cdp("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
        cdp("Page.navigate", session_id=session, url=url)
        origin = origin_of(url)
        deadline, seen = time.monotonic() + timeout, None
        while time.monotonic() < deadline:
            try:
                seen = evaluate(session, "[document.readyState, location.origin]")
            except RuntimeError:
                seen = None  # mid-navigation: the context was just replaced
            if seen and seen[0] == "complete" and seen[1] == origin:
                return target, session
            time.sleep(0.1)
        raise RuntimeError(f"{url} did not load (last state: {seen!r})")
    except Exception:
        cdp("Target.closeTarget", targetId=target)
        raise


def close_tab(target):
    try:
        _cdp()("Target.closeTarget", targetId=target)
    except Exception:
        pass


def wait_for(session, expression, what, timeout=30):
    """Poll a JS expression until it is truthy. A navigation in between is not an error."""
    deadline, seen, last_error = time.monotonic() + timeout, None, None
    while time.monotonic() < deadline:
        try:
            seen = evaluate(session, expression)
            if seen:
                return seen
        except RuntimeError as exc:
            last_error = exc
        time.sleep(0.15)
    raise RuntimeError(f"timed out waiting for {what} (last: {seen!r}{', ' + str(last_error) if last_error else ''})")


def set_local_storage(base, mapping, path="/"):
    """Write (value) or remove (None) localStorage keys on the app's origin."""
    if not mapping:
        return {}
    target, session = open_tab(f"{base}{path}")
    try:
        written = evaluate(session, (
            "(pairs => { try { for (const [k, v] of Object.entries(pairs)) {"
            " if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, String(v)); }"
            " return Object.fromEntries(Object.keys(pairs).map(k => [k, localStorage.getItem(k)]));"
            " } catch (e) { return 'FAILED: ' + e; } })(" + json.dumps(mapping) + ")"
        ))
        if not isinstance(written, dict):
            raise RuntimeError(f"could not write localStorage on {base}: {written!r}")
        return written
    finally:
        close_tab(target)


def set_cookies(base, mapping):
    if not mapping:
        return
    _cdp()("Storage.setCookies", cookies=[
        {"name": k, "value": str(v), "url": base, "path": "/"} for k, v in mapping.items()
    ])


def seed(cfg, base):
    """Consent, language, feature flags — whatever [browser] pins — before the first page.

    A cookie banner fixed at the bottom intercepts clicks on whatever sits under it, and an
    unpinned language makes an English goal read a French page.
    """
    set_cookies(base, cfg.cookies)
    return set_local_storage(base, cfg.local_storage, cfg.seed_path)


def login(cfg, base, email, password):
    """Fill the real login form over CDP and wait until the app says we are in.

    Uses the native value setter plus bubbling `input`/`change` events: React, Vue and friends
    ignore a plain `el.value = x` on a controlled input.
    """
    lg = cfg.login
    target, session = open_tab(f"{base}{lg.path}")
    try:
        user_sel, pw_sel = json.dumps(lg.username_selector), json.dumps(lg.password_selector)
        wait_for(session, f"!!document.querySelector({user_sel}) && !!document.querySelector({pw_sel})",
                 f"the login form ({lg.username_selector} / {lg.password_selector}) at {lg.path}")
        outcome = evaluate(session, """
          ((userSel, pwSel, submitSel, email, password) => {
            const set = (selector, value) => {
              const el = document.querySelector(selector);
              if (!el) return 'missing ' + selector;
              const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement : HTMLInputElement;
              Object.getOwnPropertyDescriptor(proto.prototype, 'value').set.call(el, value);
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
              return 'ok';
            };
            const filled = [set(userSel, email), set(pwSel, password)];
            if (submitSel) {
              const button = document.querySelector(submitSel);
              if (!button) return [...filled, 'missing ' + submitSel];
              button.click();
              return [...filled, 'submitted'];
            }
            const form = document.querySelector(pwSel)?.closest('form');
            if (!form) return [...filled, 'no <form> around the password field: set login.submit_selector'];
            form.requestSubmit ? form.requestSubmit() : form.submit();
            return [...filled, 'submitted'];
          })""" + "(" + ", ".join(json.dumps(v) for v in (
            lg.username_selector, lg.password_selector, lg.submit_selector, email, password)) + ")")
        if outcome != ["ok", "ok", "submitted"]:
            raise RuntimeError(f"could not submit the login form: {outcome!r}")
        landed = wait_for(session, """
          ((loginPath, prefix, selector, errorSource) => {
            const path = location.pathname;
            if (prefix && path.startsWith(prefix) && path !== loginPath) return 'ok:' + path;
            if (selector && document.querySelector(selector)) return 'ok:' + path;
            if (!prefix && !selector && path !== loginPath) return 'ok:' + path;
            const m = errorSource && (document.body?.innerText || '').match(new RegExp('[^\\n]*(' + errorSource + ')[^\\n]*', 'i'));
            return m ? 'refused:' + m[0].trim().slice(0, 200) : null;
          })""" + "(" + ", ".join(json.dumps(v) for v in (
            lg.path, lg.success_path_prefix, lg.success_selector, lg.error_pattern)) + ")",
            "the app to accept the session", timeout=lg.timeout)
        if not landed.startswith("ok:"):
            raise RuntimeError(f"login refused for {email}: {landed.split(':', 1)[1]}")
        return {"path": landed.split(":", 1)[1]}
    finally:
        close_tab(target)


def logout(cfg, base, reseed=True):
    """Forget every session the dedicated profile holds, then put the seeded state back.

    Cookies browser-wide (an API on another origin keeps its own), and every storage type of
    the app's origin — IndexedDB included, which is where Firebase keeps its session.
    """
    cdp = _cdp()
    for method, params in (("Storage.clearCookies", {}),
                           ("Storage.clearDataForOrigin", {"origin": origin_of(base), "storageTypes": "all"})):
        try:
            cdp(method, **params)
        except Exception:
            pass
    return seed(cfg, base) if reseed else {}
