"""The standing checks: what each account state is shown and allowed, verified in the database.

Each Check is a start path, ONE narrow goal in plain English with an explicit stop condition,
and a `verify` that reads the database (or the page) — never the agent's own "done".

    jevcheck audit --list
    jevcheck audit --only free_publish_blocked --headed
"""

from jevcheck import Check, has_any, result

# ─────────────────────────────────────────────── a refusal: verify the write did NOT happen ──

FREE_PUBLISH_BLOCKED = Check(
    key="free_publish_blocked",
    title="a free account cannot publish a second project",
    persona="free",
    options=("live", "draft"),            # create_account gives it one live + one draft project
    path="/projects/{draft}",
    goal=(
        "Publish this project. You are done when it is live, "
        "OR when the app refuses and tells you why."
    ),
    verify=lambda ctx, state: result(
        draft_still_unpublished=not ctx.db.value(
            f"select published from projects where id = {ctx.db.lit(ctx.var('draft'))}"),
        # A gate must refuse, not silently take the other project offline to make room.
        live_one_untouched=bool(ctx.db.value(
            f"select published from projects where id = {ctx.db.lit(ctx.var('live'))}")),
        user_was_told=has_any(state, "upgrade", "limit", "plan"),
    ),
    expect_action=True,   # a gate check that never clicked proves nothing
)

# ─────────────────────────────────────── an allowance: verify the write DID happen ──

PRO_PUBLISH_SECOND = Check(
    key="pro_publish_second",
    title="a paying account can publish a second project",
    persona="pro",
    options=("live", "draft"),
    path="/projects/{draft}",
    goal="Publish this project. You are done when the page shows it as live.",
    verify=lambda ctx, state: result(
        second_is_live=bool(ctx.db.value(
            f"select published from projects where id = {ctx.db.lit(ctx.var('draft'))}")),
    ),
    expect_action=True,
)

CHECKS = [FREE_PUBLISH_BLOCKED, PRO_PUBLISH_SECOND]
