---
name: jev-check
description: Look at the real deployed staging app with a fast browser agent (jev), signed in as a chosen account state (free, paid, lapsed, team member, admin, anonymous), and verify what the app shows and allows against the database, not against the agent's own "done". Use when a ticket is about what a plan, role or account state can see or do in the UI (paywalls, plan limits, permissions, cross-account isolation), to reproduce a bug report on a real account state, to confirm a fix on staging or on a local dev server before closing it, or to take before/after screenshots for a ticket. On demand only, never in CI, never against production.
---

# jev-check: ask the real app, as a real account state

A browser agent reads your **deployed staging app** in a dedicated Chrome, signed in as a
throw-away account in exactly the state you need, and follows a plain-English goal. Then the
**database** gives the verdict: the row landed or it did not. The agent's "done" is only a
claim.

In this file, `jevcheck` means `uv run --project <this skill's base directory> jevcheck`, run
from the user's project (the folder holding `jevcheck.toml`).

## When it earns its keep

- A ticket is about **what an account state shows or allows**: plan gating, a paywall, a
  lapsed subscription, a role that should not reach a page, a team member's rights. Mocks
  cannot answer that. This can, and it cross-checks the database.
- A **bug report you cannot reproduce from the code**: put the agent on the page in that
  account's state and read what it actually sees.
- **Before closing** a ticket about permissions, isolation, limits or billing surfaces:
  confirm the fix on the running app, not only in a unit test.
- A change touches a **menu, popup, banner or any fixed element**: check it on a phone, with
  and without the consent banner (see *Does it fit the screen?*). A green build and a
  text-reading agent both miss "the list opens outside the screen".
- You need to know whether a control is even **offered** to an account. `ask` prints
  everything the page let that account click, and that list is usually the whole answer.

## When NOT to

- Logic a unit test covers. Run the unit tests.
- Payment **lifecycles** (charges, dunning, webhook ordering). Test those against the payment
  provider's test mode, not through a browser. This tool stops at the checkout page.
- **Production**, ever. Real users, real money, real emails. The host whitelist refuses it,
  and you must never widen the whitelist to include it.
- CI. It needs a real Chrome and costs model calls. It is a tool you reach for, not a gate.

## First time in a project

1. **Prerequisites**: `uv`, and Chrome, Chromium, Brave or Edge. `TYPESAFE_API_KEY` in the
   environment (jev's decision model, https://docs.typesafe.ai). `TEXT_MODEL_API_KEY` is
   optional: only goals that make the agent *type* need it (see `.env.example`).
2. **Try the bundled demo** if you have never seen it run (two minutes, nothing to
   configure). See `examples/demo-app/`: start `server.py`, then `jevcheck audit` from that
   folder. `LAUNCHPAD_BUG=1` plants a silent billing regression, and the audit catches it.
3. `jevcheck init` at the project root writes `jevcheck.toml`, `jevcheck_hooks.py` and
   `jevcheck_checks.py`. Add `.jevcheck/` to `.gitignore`.
4. **Fill `jevcheck.toml` by reading the app's code, not by guessing**
   (`references/configuration.md` has every key):
   - `[target]`: the staging origin and `allowed_hosts`. If the user will test a local dev
     server, set `[target.local_guard]` so a localhost run proves it talks to staging.
   - `[login]`: selectors from the real login component, and `success_path_prefix` (where the
     app lands after sign-in). Email + password only (see *Five things*, #1).
   - `[browser.local_storage]` / `[browser.cookies]`: find the cookie-consent banner and write
     its "accepted" key, and pin the language key if the app is multilingual. A fixed banner
     intercepts clicks.
   - `busy_selector`: the app's own loading spinner, so the agent never decides on a loader.
   - `[datastore]`: the **staging** database (sqlite, postgres, supabase, or any `command`).
5. **Personas and hooks.** One `[personas.<name>]` per account state. For throw-away ones,
   write `create_account` / `delete_account` in `jevcheck_hooks.py`. **Copy each state from the
   code that produces it in production** (the payment webhook, the admin "grant plan" action,
   the downgrade job), field for field. A state production can never reach produces findings
   that are not bugs. Use an unroutable email domain (`example.invalid`) so a cleanup sweep can
   never match a real user.
6. `jevcheck doctor --login <persona>`: creates the account, signs it in over CDP, deletes it.
   No model calls. Repeat until it prints `ready.`

## Run it

**One look, no scenario to write.** This is the everyday form:

```bash
# what does a free account get when it tries to publish a second project?
jevcheck ask --as free --with live --with draft --path "/projects/{draft}" \
  --goal "Publish this project. You are done when it is live, OR when the app refuses and says why." \
  --sql "select id, published from projects where id = …"

# an anonymous visitor on a public page
jevcheck ask --path "/p/some-handle" --goal "Confirm the page shows a Contact button, then stop."
```

`--as` provisions one account in that state and deletes it at the end, even on failure.
`--with` passes options to `create_account` (extra rows it should own). Use it for whatever
the screen under test needs to be non-trivial: a second member in a team, or dated rows
(`--with views` backdating events over 90 days) so a chart has an axis and a period filter
has something to exclude. `{id}`, `{email}` and
every var `create_account` returns are substituted in `--path`. It prints what the agent did,
where it ended, what the page said (two observations), **what that account could click**, and
the `--sql` rows. `--headed` to watch. `--keep` leaves the browser on the result and prints
the credentials for a manual look (kept accounts expire after `keep_hours`; `jevcheck cleanup
--all` removes them now, but never while an audit is running). `--json` adds a
machine-readable last line.

**Write every goal with a stop condition that includes refusal**: "You are done when X, OR
when the app refuses and says why." Keep it to one flow. The budget is 60 actions per goal, and
a goal that chains four flows exhausts the budget instead of failing honestly.

**The standing audit** runs every check in `jevcheck_checks.py`, each verified in the
database:

```bash
jevcheck audit --list
jevcheck audit                          # all of them
jevcheck audit --only free_publish_blocked --headed
```

**Verify a fix you have not deployed yet**: start the dev server (pointed at staging), then
`jevcheck ask --base http://localhost:5173 …`. With `[target.local_guard]` set, the run
refuses a local server whose env points anywhere but staging.

## Screenshots for a ticket (before / after)

When a bug is **visible**, a picture in the ticket lets the owner check it at a glance.

```bash
# 1. the AFTER, cropped to the one element that matters
jevcheck ask --as free … --shot after.png --shot-selector "[role=dialog]" --goal "…"

# 2. the BEFORE of a fix already merged: serve the commit before it (throw-away worktree)
<skill dir>/scripts/serve_commit.sh <fix-commit>~1 8081        # in the background
jevcheck ask --base http://localhost:8081 … --shot before.png --goal "…"

# 3. publish to an orphan branch through the GitHub API, print the markdown table to paste
jevcheck publish-shots <issue> before=before.png after=after.png
```

**Look at every PNG before publishing it**, since it is a claim about the product. Take the
before and the after from the same account state and the same goal, or the pair proves
nothing. Skip screenshots for backend-only or timing bugs. `publish-shots` writes to GitHub,
so confirm with the user before running it.

## Does it fit the screen? (phone, first visit)

The agent reads text; it cannot see that a menu opens half outside a phone, or that a fixed
cookie banner sits on the last button. After every `ask` the harness reads the geometry of the
page the agent **ended on** (open menus and pickers included) and prints a `layout` section:

- `offscreen`: a control or popup sticking out of the screen (a language list clipped by its
  container counts; a carousel item scrolling into view does not).
- `covered`: a visible control whose centre is painted over by something else (cookie banner,
  chat button, sticky bar), unless a modal dialog covers what is outside it.
- `hscroll`: the page scrolls sideways, with the widest elements.

```bash
# the menu's language picker on a phone, as a NEW visitor (consent banner on screen)
jevcheck ask --viewport phone --no-seed --path / --shot menu.png \
  --goal "Open the menu, then open the language picker, then stop."
```

- `--viewport phone|small-phone|tablet|laptop|WxH[@dpr]` emulates the screen. Without it the
  probe still runs, at jev's 1120 px window, which hides every phone bug.
- `--no-seed` skips `[browser.local_storage]` / `[browser.cookies]`, so the consent banner and
  first-run UI are shown. Seeding them (the default) is right for goals that must click
  through, and wrong for this question: a real first-time visitor has the banner.
- End the goal **on the state to judge** ("open the picker, then stop"): the probe sees only
  the final page, so a closed menu proves nothing.
- Findings are pixels, not verdicts. Read the `--shot` before reporting; a decoration meant to
  bleed off the edge is a false positive. The probe makes no model calls.

For a standing guard, add `viewport="phone", layout=True` to a `Check`: it then fails on
`layout_clean` (an unreadable probe fails too). In an audit, `--viewport` applies to every check.

## Adding a durable check

When a ticket deserves a regression guard, add a `Check` to `jevcheck_checks.py`
(`references/checks.md` has the API and patterns):

```python
FREE_PUBLISH_BLOCKED = Check(
    key="free_publish_blocked", title="a free account cannot publish a second project",
    persona="free", options=("live", "draft"), path="/projects/{draft}",
    goal="Publish this project. You are done when it is live, OR when the app refuses and says why.",
    verify=lambda ctx, state: result(
        db_says_no=not ctx.db.value(f"select published from projects where id = {ctx.db.lit(ctx.var('draft'))}"),
        other_untouched=bool(ctx.db.value(f"select published from projects where id = {ctx.db.lit(ctx.var('live'))}")),
        user_was_told=has_any(state, "upgrade", "limit"),
    ),
    expect_action=True,
)
```

Name each boolean after what it proves. For a refusal, check that **the write did not
happen AND nothing else was silently changed to make room**. That second check is where the
real regressions hide.

In an audit, all checks of one persona share **one account and its rows** for the whole run.
A check that writes (publishes, deletes, upgrades) needs its own rows: give it its own option
name (`options=("draft_b",)`) or its own persona. Otherwise the next check starts from a state
it never set up.

## Five things that will bite you

1. **jev can never type a password.** Its observer excludes `password`, `file` and `hidden`
   inputs by design, so no goal can sign in. The harness fills the real login form over CDP
   and the app stores its own session. Never write a goal that types credentials. SSO, magic
   links and CAPTCHAs cannot be driven, so give staging a password login for test accounts.
2. **Animated dialogs are invisible unless animations are killed.** In an owned background
   tab, an enter animation from `opacity: 0` never advances, and the agent sees a blank page
   where the paywall is. The harness zeroes every animation on every page. Keep it that way in
   any loop you write yourself.
3. **A check that ends with 0 actions proves nothing.** "The write did not happen" is
   trivially true when nothing was attempted. That is what `expect_action=True` is for. The
   one exception is a check whose pass condition is "there was nothing to click" (a read-only
   UI). Assert that explicitly with `status(state) == "blocked"`.
4. **One observation lies.** A refusal toast is gone seconds later, and an app that hydrates
   late makes an early read an empty shell. Every run keeps both (`has_any` reads both).
5. **Screenshots are best-effort.** Per-step frames (`--record`) go through a 5 s CDP
   deadline that a software rasteriser misses. `--shot` asks for 30 s and brings the tab to
   the front first. A timeout there is not a product failure.

## Reading a result honestly

- The agent is non-deterministic. **A single odd run is a flake.** Re-run that one check
  (`--only`) before reporting anything.
- **The database is the verdict**, never the agent's `done`. A `done` with a failed DB check
  is the most valuable output this tool has: the app *looked* fine and was not.
- **Before reporting a finding, trace it to a file and a line** in the app's source. A
  finding with a cause is actionable. A screenshot alone is not.
- If a check fails because the fixture state is one production could not produce, the fixture
  is the bug. Fix the hook, not the app.

## Safety rules (non-negotiable)

- Staging only. Never add a production host to `allowed_hosts`, and never point `[datastore]`
  at a production database. Hooks **write** to it.
- The agent runs in its own Chrome with a throw-away profile, never the user's browser. Its
  cookies and sessions would be production sessions.
- Throw-away accounts only, on an unroutable domain, deleted at the end of every run. Static
  personas are never deleted, so use them only for accounts that exist for testing.
- Keys stay in the environment or a gitignored env file. Never write them into
  `jevcheck.toml`, a check or a commit.

References: `references/configuration.md` (every config key, hooks API, datastores),
`references/checks.md` (Check API and patterns), `references/troubleshooting.md` (symptom →
cause, costs, limits).
