# Writing checks

A check is **one narrow goal** plus a **verdict that does not trust the agent**.

```python
from jevcheck import Check, result, has_any, text_of, final_url, actions, offered, status

Check(
    key="unique_snake_case",       # --only uses it
    title="what it proves, as a sentence",
    persona="free",                # a [personas.*] name, or None for an anonymous visitor
    options=("live", "draft"),     # passed to create_account for this persona
    path="/projects/{draft}",      # {id} {email} + the persona's vars; or a callable(ctx) -> str
    goal="… You are done when X, OR when the app refuses and says why.",   # or callable(ctx)
    verify=lambda ctx, state: result(named_boolean=..., another=...),
    local_storage=None,            # dict (None value = remove key) or callable(ctx), set before the run
    expect_action=False,           # True: 0 actions = FAIL
    budget=None,                   # seconds, when the default is wasteful
)

CHECKS = [...]
```

## `ctx`

| | |
|---|---|
| `ctx.db` | the datastore: `.sql()`, `.one()`, `.value()`, `.lit()` |
| `ctx.var(name)` | a var of the acting account (`ctx.var(name, persona="pro")` for another one) |
| `ctx.account` | the acting account (`.id`, `.email`, `.vars`), `None` when anonymous |
| `ctx.accounts` | every persona's account in this run, by name |
| `ctx.url(path)` | absolute URL with placeholders filled |

In an audit, every persona the selected checks need is created once, up front, with the
union of their `options`. Checks run in file order, and the harness signs out and back in
only when the persona changes.

**So every check of a persona acts on the same account and the same rows for the whole run.**
A check that changes state changes what the later checks of that persona start from: if one
check publishes the `draft`, the next check that opens `{draft}` finds it already live and
fails on a state it never set up. Give each check that writes its own option name
(`options=("draft_a",)`, `options=("draft_b",)`, both created by `create_account`) or its own
persona. Read-only checks can share freely.

## `state` helpers

| helper | returns |
|---|---|
| `has_any(state, *needles)` | case-insensitive substring match over **both** observations |
| `text_of(state)` | the text of both observations |
| `final_url(state)` | where the agent ended |
| `actions(state)` | labels of what it clicked or typed, in order |
| `offered(state)` | `(kind, label)` of every control the page offered at the end |
| `status(state)` | jev's last word: `done`, `blocked`, or `ready` (stopped by a budget) |

`result(**checks)` passes only if every named boolean is true. The failure line prints the
names that were false, so **name them after what they prove**: `draft_still_unpublished`,
not `c1`.

## Patterns

**A refusal.** Verify that the write did not happen, **and** that nothing else changed to
make room. The second assertion catches the gate that "helpfully" unpublishes something else,
a regression the UI hides completely.

```python
verify=lambda ctx, state: result(
    target_unchanged=not ctx.db.value(f"select published from projects where id = {ctx.db.lit(ctx.var('draft'))}"),
    nothing_else_touched=ctx.db.value(f"select count(*) from projects where user_id = {ctx.db.lit(ctx.account.id)} and published") == 1,
    user_was_told=has_any(state, "upgrade", "limit"),
),
expect_action=True,
```

**An allowance.** The mirror image: the paid state must be *able* to do it. A gate that
refuses everyone passes every refusal check. Pair each refusal with an allowance.

**Isolation.** Account A opens account B's resource by URL and tries to change it:

```python
Check(
    key="stranger_cannot_edit", persona="outsider",
    path=lambda ctx: f"/projects/{ctx.var('live', persona='pro')}/edit",
    goal="Unpublish this project. You are done when it is offline, OR when the app refuses.",
    verify=lambda ctx, state: result(
        other_users_project_still_live=bool(ctx.db.value(f"select published from projects where id = {ctx.db.lit(ctx.var('live', persona='pro'))}")),
        no_editor_shown=not has_any(state, "Live project"),
    ),
    budget=30,
)
```

Also check the **allow** path of the same gate (the owner, a team admin, a shared editor still
get in). A gate that only lets owners through passes the deny check and silently locks out
everyone else.

**"There was nothing to click"** (a read-only UI removes the control): the agent stops as
`blocked` with 0 actions, and that is the pass condition. Leave `expect_action` off and assert
`status(state) == "blocked"` together with the database check.

**Anonymous.** `persona=None`. The harness signs out (cookies, storage, IndexedDB) before it runs.

## Writing goals

- One flow per goal. The per-goal budget is 60 actions / 120 model calls.
- Always give the stop condition, and include refusal: "…OR when the app refuses and says why."
  Without it the agent keeps looking for another way in.
- Say what not to do when a wrong click is costly: "Do not start a purchase."
- Values the agent must type come from the goal, copied by the text model: keep them short,
  plain ASCII. Better still, have `create_account` pre-create what the goal would type.
- Read-only goals ("Read this page and stop once X is visible") cost one or two model calls.
