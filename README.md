# jev-check

**Ask the real app, as a real account state.**

An agent skill (Claude Code, Codex, OpenClaw, and any agent that reads `SKILL.md`) that puts a
fast browser agent on your **staging** app, signed in as a throw-away account in exactly the
state you need (free, paid, lapsed, team member, stranger, anonymous), and has it follow a
plain-English goal. Then the **database** says whether it worked. The agent's "done" is only a
claim.

```text
$ jevcheck audit
== free_publish_blocked: a free account cannot publish a second project
       995 ms  ready     Publish
      1412 ms  done      Publish
…
FAIL  free_publish_blocked   as free   1 act   2 calls   1.3s  ← draft_still_unpublished,
      live_one_untouched, user_saw_the_paywall, paywall_states_the_reason
```

The agent clicked Publish and said **done**, and the UI looked fine. The database says the
free-plan gate let the second project through and silently took the first one offline. That
is the class of bug mocks and unit tests miss: the deployed front, the backend and the
permissions disagreeing with each other.

## What you get

- **`jevcheck ask`**: one look, no test to write. "What does a free account get when it
  tries X?" It prints what the agent did, what the page said, **every control that account
  could click**, and the SQL rows you asked for.
- **`jevcheck audit`**: standing checks (a path, a goal, a verdict read from the database),
  for plan gates, permissions and cross-account isolation.
- **Throw-away accounts** in real states, created by your own hooks in the shape your
  production code writes them, deleted at the end of every run (on failure too), with a TTL
  sweep for anything a killed run left behind.
- **Before/after screenshots** for tickets, cropped to one element, published to an orphan
  branch, pasted as a markdown table.
- **Safety by construction**: a host whitelist that fails closed, a guard that refuses a
  localhost front wired to anything but staging, and a dedicated Chrome with a throw-away
  profile, so it never touches your real browser or its production sessions.
- The **hard-won fixes** that make an LLM browser agent usable on a real app: sign-in over
  CDP (the agent can never type a password), animations killed so dialogs are visible in a
  background tab, a settle on the app's spinner, two observations per run (the toast that
  explained a refusal is gone a second later), and "0 actions proves nothing".

The `SKILL.md` teaches your coding agent when to reach for it, how to set it up for your app
**by reading your code**, how to write goals and checks, and how to read a result honestly.

## Try it in two minutes (bundled demo, nothing to configure)

Requirements: [uv](https://docs.astral.sh/uv/), Chrome/Chromium/Brave/Edge, and a
`TYPESAFE_API_KEY` (jev's decision model, https://docs.typesafe.ai).

```bash
cd examples/demo-app
python3 server.py &                                   # "Launchpad", a tiny app with a free-plan gate
uv run --project ../.. jevcheck doctor --login free   # no model calls
uv run --project ../.. jevcheck audit                 # 3 checks, ~5 s, PASS

kill %1; LAUNCHPAD_BUG=1 python3 server.py &          # the same app with a silent billing regression
uv run --project ../.. jevcheck audit                 # free_publish_blocked FAILS, and says why
```

## Install as a skill

Copy (or clone) this folder where your agent loads skills, for example
`~/.claude/skills/jev-check/` for Claude Code (all projects) or `.claude/skills/jev-check/` in
one repository. Then, in your project:

```bash
uv run --project ~/.claude/skills/jev-check jevcheck init     # jevcheck.toml + hooks + checks
```

and ask your agent to "set up jev-check for this app". The skill walks it through the rest.

## Requirements

| | |
|---|---|
| Python | 3.12+ (managed by `uv`) |
| Browser | Chrome, Chromium, Brave or Edge. Linux and macOS (Windows untested) |
| `TYPESAFE_API_KEY` | required, [TypeSafe](https://docs.typesafe.ai) (jev's decision model, billed by them) |
| `TEXT_MODEL_API_KEY` | optional, any OpenAI-compatible endpoint. Only goals that make the agent type need it |
| Your app | a **staging** environment with a password login, and SQL access to its database (sqlite, Postgres, Supabase, or any command that returns JSON rows) |

Built on [jev-ultrafast](https://github.com/browser-use/jev-ultrafast) and
[browser-harness](https://github.com/browser-use/browser-harness) by Browser Use (MIT), installed
from their own repositories and pinned by commit. browser-harness telemetry is switched off by
default.

## Not for

Production (refused by design), CI gates (it needs a real browser and costs model calls),
payment lifecycles (test those against your payment provider's test mode), SSO, magic-link
or CAPTCHA logins.

## License

Commercial, per purchaser. See `LICENSE`.
