"""Does the page fit the screen? Things a model reading text cannot see.

The agent reads what the page says and offers; it cannot tell that a language menu opens
half outside a 390 px phone, or that a fixed cookie banner sits on top of the menu's last
button. Both are real, both ship in green builds. After the agent has finished, this module
reads the geometry of the page it ended on (open popups included, since that is the state a
user is in) and reports:

* offscreen: a visible control or popup that sticks out of the viewport (left or right; also
  top or bottom when it is fixed to the screen). A popup that a container clips counts (that
  is the bug: half a menu). An in-flow control inside a carousel or a scrolling panel does not.
* covered: a visible control whose centre is painted over by something else that is not a
  modal dialog the control sits outside of: a cookie banner, a floating chat button, a
  sticky bar.
* hscroll: the page itself scrolls sideways, with the widest elements responsible.

    jevcheck ask --viewport phone --goal "Open the menu, then the language picker, then stop."
    jevcheck ask --viewport phone --no-seed …        # first visit: the consent banner is shown

Findings are facts about pixels at one viewport and one moment. Look at the `--shot` before
reporting one: a decorative element that is meant to bleed off the edge is a false positive.
"""

VIEWPORTS = {
    "phone": (390, 844),
    "small-phone": (360, 740),
    "tablet": (768, 1024),
    "laptop": (1280, 800),
}


def parse_viewport(value):
    """'phone', '390x844' or '390x844@3' -> (width, height, device_scale_factor)."""
    if not value:
        return None
    text = value.strip().lower()
    scale = 2
    if "@" in text:
        text, _, dpr = text.partition("@")
        scale = float(dpr)
    if text in VIEWPORTS:
        width, height = VIEWPORTS[text]
    else:
        try:
            width, height = (int(n) for n in text.split("x"))
        except ValueError:
            raise SystemExit(f"bad viewport {value!r}: use {', '.join(VIEWPORTS)}, or WIDTHxHEIGHT[@DPR] like 390x844@3")
    return width, height, scale


def apply_viewport(session, viewport):
    """Emulate a phone-sized screen on the agent's tab. jev sets its own 1120x780 window when
    it starts, so this runs after the Agent exists and is repeated on every navigation by Chrome."""
    from browser_harness.helpers import cdp

    width, height, scale = viewport
    cdp("Emulation.setDeviceMetricsOverride", session_id=session, width=width, height=height,
        deviceScaleFactor=scale, mobile=width < 800, screenWidth=width, screenHeight=height)


PROBE = r"""
(() => {
  const vw = document.documentElement.clientWidth, vh = innerHeight, TOL = 1.5;
  const INTERACTIVE = 'a[href],button,input:not([type=hidden]),select,textarea,summary,'
    + '[role=button],[role=link],[role=option],[role=menuitem],[role=tab],[role=checkbox],'
    + '[role=switch],[tabindex]:not([tabindex="-1"])';
  const POPUP = '[role=listbox],[role=menu],[role=dialog],[role=alertdialog],[role=tooltip],[popover]';
  const visible = el => el.checkVisibility
    ? el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})
    : !!(el.offsetWidth || el.offsetHeight);
  const describe = el => {
    const role = el.getAttribute('role');
    const text = (el.getAttribute('aria-label') || el.innerText || el.value || el.title || '')
      .replace(/\s+/g, ' ').trim().slice(0, 40);
    const id = el.id ? '#' + el.id : '';
    return el.tagName.toLowerCase() + id + (role ? '[role=' + role + ']' : '') + (text ? ' "' + text + '"' : '');
  };
  // The part of the element a user could see: its box cut by every scrolling or clipping ancestor.
  const shown = el => {
    const r = el.getBoundingClientRect();
    let l = r.left, t = r.top, rr = r.right, b = r.bottom, fixed = false;
    for (let a = el; a && a.nodeType === 1; a = a.parentElement) {
      const cs = getComputedStyle(a);
      if (cs.position === 'fixed') fixed = true;
      if (a === el || a === document.documentElement || a === document.body) continue;
      if (cs.overflowX !== 'visible' || cs.overflowY !== 'visible') {
        const ar = a.getBoundingClientRect();
        if (cs.overflowX !== 'visible') { l = Math.max(l, ar.left); rr = Math.min(rr, ar.right); }
        if (cs.overflowY !== 'visible') { t = Math.max(t, ar.top); b = Math.min(b, ar.bottom); }
      }
    }
    return {l, t, r: rr, b, fixed, w: rr - l, h: b - t};
  };
  const inert = el => !!el.closest('[inert],[aria-hidden=true]');
  const out = {viewport: [vw, vh], offscreen: [], covered: [], hscroll: null};
  const seen = new Set();
  const nodes = [...document.querySelectorAll(INTERACTIVE + ',' + POPUP)];

  for (const el of nodes) {
    if (!visible(el) || inert(el)) continue;
    const s = shown(el);
    if (s.w < 2 || s.h < 2) continue;
    // A popup (menu, listbox, dialog, anything absolutely positioned) is not content that scrolls
    // into view: a container that clips it sideways hides part of it, which is the bug. So its
    // raw box is judged against the screen. In-flow controls in a carousel use the clipped box.
    const raw = el.getBoundingClientRect(), pos = getComputedStyle(el).position;
    const popupLike = el.matches(POPUP) || pos === 'absolute' || pos === 'fixed';
    const L = popupLike ? raw.left : s.l, R = popupLike ? raw.right : s.r;
    const sides = [];
    if (L < -TOL) sides.push('left by ' + Math.round(-L) + 'px');
    if (R > vw + TOL) sides.push('right by ' + Math.round(R - vw) + 'px');
    if (s.fixed && s.t < -TOL) sides.push('top by ' + Math.round(-s.t) + 'px');
    if (s.fixed && s.b > vh + TOL) sides.push('bottom by ' + Math.round(s.b - vh) + 'px');
    if (sides.length && !seen.has(el)) {
      seen.add(el);
      out.offscreen.push({what: describe(el), where: sides.join(', ')});
    }
  }

  for (const el of nodes.filter(n => n.matches(INTERACTIVE))) {
    if (!visible(el) || inert(el) || getComputedStyle(el).pointerEvents === 'none') continue;
    const s = shown(el);
    const l = Math.max(s.l, 0), r = Math.min(s.r, vw), t = Math.max(s.t, 0), b = Math.min(s.b, vh);
    if (r - l < 2 || b - t < 2) continue;   // not on screen: scrolled away, not covered
    const top = document.elementFromPoint((l + r) / 2, (t + b) / 2);
    if (!top || el.contains(top) || top.contains(el)) continue;
    if (el.labels && [...el.labels].some(x => x.contains(top))) continue;
    if (top.closest('nextjs-portal,vite-error-overlay,#webpack-dev-server-client-overlay')) continue;   // dev-server tooling, not the app
    const modal = top.closest('[aria-modal=true],dialog[open]');
    if (modal && !modal.contains(el)) continue;   // a modal covering the page is its job
    let cover = top;
    for (let a = top; a && a !== document.body; a = a.parentElement) {
      const p = getComputedStyle(a).position;
      if (p === 'fixed' || p === 'sticky') { cover = a; break; }
    }
    out.covered.push({what: describe(el), by: describe(cover)});
  }

  const doc = document.documentElement;
  if (doc.scrollWidth > vw + TOL) {
    const wide = [];
    for (const el of document.body.querySelectorAll('*')) {
      if (!visible(el)) continue;
      const s = shown(el);
      if (s.w >= 2 && s.r > vw + TOL) wide.push({what: describe(el), by: Math.round(s.r - vw)});
    }
    wide.sort((x, y) => y.by - x.by);
    out.hscroll = {scrollWidth: doc.scrollWidth, widest: wide.slice(0, 5)};
  }
  out.offscreen = out.offscreen.slice(0, 20);
  out.covered = out.covered.slice(0, 20);
  return out;
})()
"""


def probe(session):
    """Run the geometry probe on the agent's tab. Never raises: a probe that fails is a note."""
    try:
        from browser_harness.helpers import cdp

        response = cdp("Runtime.evaluate", session_id=session, expression=PROBE, returnByValue=True)
        if response.get("exceptionDetails"):
            return {"error": response["exceptionDetails"].get("text", "probe failed")}
        return response.get("result", {}).get("value") or {"error": "probe returned nothing"}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def findings(state):
    """Every layout problem found, one readable line each. Empty when the page fits."""
    found = (state or {}).get("layout") or {}
    lines = [f"offscreen: {o['what']} sticks out {o['where']}" for o in found.get("offscreen", [])]
    lines += [f"covered: {c['what']} is under {c['by']}" for c in found.get("covered", [])]
    if found.get("hscroll"):
        wide = "; ".join(f"{w['what']} (+{w['by']}px)" for w in found["hscroll"]["widest"])
        lines.append(f"hscroll: the page scrolls sideways (scrollWidth {found['hscroll']['scrollWidth']}"
                     f" > {found['viewport'][0]}px). Widest: {wide or 'none found'}")
    return lines


def layout_clean(state):
    """For a Check: True when the page the agent ended on fits the screen and nothing covers a control.

    A probe that could not run is not a pass: it returns False, so a silent failure never
    reads as a clean layout."""
    found = (state or {}).get("layout")
    if not found or found.get("error"):
        return False
    return not findings(state)
