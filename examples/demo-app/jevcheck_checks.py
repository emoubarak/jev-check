"""The Launchpad audit: what each account state is shown and allowed, verified in demo.db."""

from jevcheck import Check, final_url, has_any, result


def published(ctx, key):
    return bool(ctx.db.value(f"select published from projects where id = {ctx.db.lit(ctx.var(key))}"))


def live_count(ctx):
    return ctx.db.value(f"select count(*) from projects where user_id = {ctx.db.lit(ctx.account.id)} and published = 1")


FREE_PUBLISH_BLOCKED = Check(
    key="free_publish_blocked",
    title="a free account cannot publish a second project",
    persona="free",
    options=("live", "draft"),
    path="/projects/{draft}",
    goal=("Publish this project. You are done when it is live, "
          "OR when the app refuses and tells you why."),
    verify=lambda ctx, state: result(
        draft_still_unpublished=not published(ctx, "draft"),
        # The gate must refuse, not silently unpublish the other project to make room.
        live_one_untouched=published(ctx, "live"),
        exactly_one_live=live_count(ctx) == 1,
        user_saw_the_paywall=has_any(state, "upgrade to pro"),
        paywall_states_the_reason=has_any(state, "1 live project"),
    ),
    expect_action=True,   # "nothing was published" proves nothing if nothing was clicked
)

PRO_PUBLISH_SECOND = Check(
    key="pro_publish_second",
    title="a Pro account can publish a second project",
    persona="pro",
    options=("live", "draft"),
    path="/projects/{draft}",
    goal="Publish this project. You are done when the page shows it as live.",
    verify=lambda ctx, state: result(
        second_is_live=published(ctx, "draft"),
        two_live=live_count(ctx) == 2,
    ),
    expect_action=True,
)

ANONYMOUS_SENT_TO_LOGIN = Check(
    key="anonymous_sent_to_login",
    title="an anonymous visitor cannot reach the dashboard",
    persona=None,
    path="/app",
    goal="Read this page and stop. You are done once you can tell whether you are signed in.",
    verify=lambda ctx, state: result(
        landed_on_login=final_url(state).rstrip("/").endswith("/login"),
        no_project_leaked=not has_any(state, "Live project", "Draft project"),
    ),
    budget=30,
)

CHECKS = [FREE_PUBLISH_BLOCKED, PRO_PUBLISH_SECOND, ANONYMOUS_SENT_TO_LOGIN]
