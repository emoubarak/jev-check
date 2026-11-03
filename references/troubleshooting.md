# Troubleshooting, costs, limits

## Symptom → cause

| symptom | cause / fix |
|---|---|
| `Chrome exited with code 0 before opening port` | an orphan browser still holds that profile: `pgrep -af remote-debugging-port`, kill it |
| runs start failing in `ensure_daemon` | a browser-harness daemon from an earlier run is wedged: `pkill -f browser_harness.daemon` |
| `remote debugging is turned off` / the agent opens tabs in YOUR Chrome | something dropped `BU_CDP_URL`. Never run jev directly, always through `jevcheck` |
| `login refused for …: <text>` | the app said no: the account is not confirmed, the password hash scheme differs, or the user row is missing its identity/profile row. Fix `create_account` |
| `timed out waiting for the login form` | `username_selector` / `password_selector` do not match, or the login page needs JS it cannot load. Open it `--headed` |
| `timed out waiting for the app to accept the session` | `success_path_prefix` is wrong (where does the app land after sign-in?) or sign-in needs a second step |
| the agent says `blocked` on the first page | it decided on a loader: set `busy_selector` to the app's spinner |
| the agent says `blocked`, but a dialog is clearly open when you look | an animation the kill-stylesheet cannot reach (Web Animations API, canvas). Report which library |
| clicks do nothing at the bottom of the page | a fixed cookie banner sits on top: seed its key in `[browser.local_storage]` or `[browser.cookies]` (and if the banner covering the controls is itself the question, `--no-seed`) |
| the layout probe reports nothing on a page you can see is broken | the goal ended before the state (menu closed), or the viewport is the 1120 px default: add `--viewport phone` and end the goal on the open menu |
| `layout` flags an element that is meant to bleed off the edge | a false positive: pixels are not verdicts. Look at `--shot`, skip it |
| every typed field fails | `TEXT_MODEL_API_KEY` is missing or out of credit |
| `Page.captureScreenshot timed out` / `_IPCResponseTimeout` | per-step frames (`--record`) on a software rasteriser. Leave `--record` off. `--shot` uses a 30 s deadline |
| a check fails once and passes on re-run | a flake. The agent is non-deterministic, so re-run `--only` before filing |
| every check fails with `verification_ran` | the verify function raised: wrong column or table, or `ctx.var` of a var `create_account` did not return |

## Costs

Each agent step is one TypeSafe request (the operation and the target are decided in the same
round trip), plus one text-model call per field it types. Typical numbers:

| goal | actions | model calls | time |
|---|---|---|---|
| read a page and stop | 0 | 1 | ~1 s |
| click into a gate and read the refusal | 1–2 | 2–3 | 1–3 s |
| a short form (2–3 fields + submit) | 3–5 | 6–9 | 5–15 s |

`doctor`, `init`, `cleanup` and `publish-shots` make no model calls.

## Limits

- **Password login only.** The agent never types into a password field, and the harness fills
  the form over CDP. SSO, magic links, OTP, CAPTCHAs and two-step logins cannot be driven.
- jev's DOM reader covers common HTML and ARIA controls. Shadow roots, iframes (embedded
  checkout forms, for example), canvas, file uploads and pop-up windows are out of reach. Stop
  goals before them.
- One Chrome, one default browser context: personas are switched by clearing cookies and the
  origin's storage, not run in parallel.
- Budget: 60 actions / 120 model calls per goal, plus the wall-clock `budget`.
- Chrome profile listing uses Linux `/proc`. On macOS/Windows a kept profile is not protected
  from `cleanup --all` while its browser still runs, so close kept browsers first.

## Upgrading jev

`pyproject.toml` pins jev-ultrafast by commit on purpose (`Agent.__init__` is not a stable API
at 0.1.0). Bump the SHA, run `uv sync`, then `jevcheck doctor --login <persona>` and the demo
audit as a cheap smoke test.
