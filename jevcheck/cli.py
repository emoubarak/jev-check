"""jevcheck — ask the real app, as a real account state.

    jevcheck init                         scaffold jevcheck.toml + hooks + checks here
    jevcheck doctor [--login PERSONA]     is everything wired? (no model calls)
    jevcheck ask --as free --goal "…"     one look, no scenario to write
    jevcheck audit [--only k1,k2]         the standing checks, each verified in the database
    jevcheck cleanup [--all]              delete throw-away accounts a --keep run left behind
    jevcheck publish-shots 42 before=a.png after=b.png
"""

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from . import accounts, agent, checks, chrome, config, layout, session, shots, store

TEMPLATES = Path(__file__).parent / "templates"


def _common(p):
    p.add_argument("--config", help="path to jevcheck.toml (default: found from the working directory up)")


def _browser_flags(p):
    p.add_argument("--base", help="target origin (default: [target] base); must be in allowed_hosts")
    p.add_argument("--headed", action="store_true", help="show the browser (default: headless)")
    p.add_argument("--record", action="store_true", help="save one JPEG per step (best-effort)")
    p.add_argument("--budget", type=float, help="wall-clock seconds per goal (default: [run] budget)")
    p.add_argument("--keep", action="store_true",
                   help="keep the accounts and the browser, print the credentials for a manual look")
    p.add_argument("--cdp-port", type=int, default=9333, help="first port tried for the dedicated Chrome")
    p.add_argument("--viewport", metavar="SIZE",
                   help="emulate a screen: phone (390x844), small-phone, tablet, laptop, or WIDTHxHEIGHT[@DPR]. "
                        "The page's layout is then checked too: off-screen popups, covered controls, sideways scroll")
    p.add_argument("--no-seed", action="store_true",
                   help="first visit: do not write [browser.local_storage]/[browser.cookies], so the consent "
                        "banner and any first-run UI are on screen, as for a new visitor")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="jevcheck", description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="\n".join(__doc__.splitlines()[2:]))
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="scaffold jevcheck.toml, jevcheck_hooks.py and jevcheck_checks.py")
    p.add_argument("--dir", default=".", help="where to write them (default: here)")

    p = sub.add_parser("doctor", help="check keys, browser, target, datastore, hooks — no model calls")
    _common(p)
    p.add_argument("--login", metavar="PERSONA",
                   help="also create that persona, sign it in over CDP, and delete it again")
    p.add_argument("--base")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--cdp-port", type=int, default=9333)

    p = sub.add_parser("ask", help="one look: a goal, a start path, an optional account state")
    _common(p)
    _browser_flags(p)
    p.add_argument("--goal", required=True, help="what the agent should do, in plain English, with its stop condition")
    p.add_argument("--path", default="/", help="where to start; {id} {email} and the persona's vars are substituted")
    p.add_argument("--as", dest="persona", help="sign in as this persona first (default: anonymous)")
    p.add_argument("--with", dest="options", action="append", default=[], metavar="OPTION",
                   help="passed to create_account (repeatable), e.g. --with draft --with live")
    p.add_argument("--sql", default="", help="a SELECT to run on the datastore afterwards")
    p.add_argument("--shot", default="", help="save a PNG of the final page here")
    p.add_argument("--shot-selector", default="", help="crop --shot to this element, e.g. \"[role=dialog]\"")
    p.add_argument("--no-layout", action="store_true", help="skip the layout probe (it only reads geometry, no model calls)")
    p.add_argument("--json", action="store_true", help="also print a machine-readable summary as the last line")

    p = sub.add_parser("audit", help="run the checks from [audit] checks, each verified in the datastore")
    _common(p)
    _browser_flags(p)
    p.add_argument("--only", default="", help="comma-separated check keys")
    p.add_argument("--list", action="store_true", help="list the checks and exit")

    p = sub.add_parser("cleanup", help="delete throw-away accounts past keep_hours (or --all) and old browser profiles")
    _common(p)
    p.add_argument("--all", action="store_true", help="every one, now — never while an audit is running")

    p = sub.add_parser("publish-shots", help="upload before/after PNGs for an issue, print the markdown")
    p.add_argument("issue", help="issue number")
    p.add_argument("pairs", nargs="+", metavar="name=file.png")
    p.add_argument("--repo", help="owner/name (default: the current gh repo)")
    p.add_argument("--branch", default="jevcheck-assets", help="orphan branch for the images")
    return parser.parse_args(argv)


# ────────────────────────────────────────────────────────────── helpers ──

def preflight():
    if not (os.environ.get("TYPESAFE_API_KEY") or "").strip():
        raise SystemExit("TYPESAFE_API_KEY is not set — jev's decision model key (https://docs.typesafe.ai). "
                         "Export it, or put it in the file named by [run] env_file.")
    if not (os.environ.get("TEXT_MODEL_API_KEY") or "").strip():
        print("note: TEXT_MODEL_API_KEY is unset — fine for click-only goals; any goal that makes the "
              "agent type a value will stop at the first field.")


def new_run_dir(cfg, prefix):
    run_dir = cfg.artifacts / "runs" / f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    latest = cfg.artifacts / "runs" / "latest"
    try:
        latest.unlink(missing_ok=True)
        latest.symlink_to(run_dir.name)
    except OSError:
        pass
    return run_dir


def expire_leftovers(cfg, db):
    gone = accounts.purge(cfg, db)
    profiles = chrome.purge_profiles(cfg.artifacts / "runs", older_than_hours=cfg.keep_hours)
    if gone or profiles:
        print(f"expired : {len(gone)} kept account(s) and {len(profiles)} browser profile(s) "
              f"older than {cfg.keep_hours:g} h")


def sign_in(cfg, base, account):
    landed = session.login(cfg, base, account.email, account.password)
    if account.local_storage:
        session.set_local_storage(base, account.local_storage, cfg.seed_path)
    return landed


def finish_browser(cfg, proc, run_dir, keep):
    if keep:
        print(f"browser : left running (pid {proc.pid}), profile in {run_dir / 'chrome-profile'}")
        return
    chrome.stop(proc)
    shutil.rmtree(run_dir / "chrome-profile", ignore_errors=True)


def teardown_all(cfg, db, made, keep, base):
    if not made:
        return
    if keep:
        for a in made:
            print(f"KEPT    : {a.persona} = {a.email} / {a.password}")
        print(f"          sign in by hand at {base}{cfg.login.path}. Throw-away ones are deleted by the "
              f"first run after {cfg.keep_hours:g} h, or now with `jevcheck cleanup --all`.")
        return
    for a in made:
        try:
            if accounts.teardown(cfg, db, a):
                print(f"cleanup : {a.persona} ({a.email}) deleted")
        except Exception as exc:
            print(f"cleanup : could NOT delete {a.email}: {exc} — it stays in the ledger for `jevcheck cleanup`")


# ─────────────────────────────────────────────────────────────── commands ──

def cmd_init(args):
    dest = Path(args.dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("jevcheck.toml", "jevcheck_hooks.py", "jevcheck_checks.py"):
        target = dest / name
        if target.exists():
            print(f"  kept     {target} (already there)")
            continue
        shutil.copy(TEMPLATES / name, target)
        print(f"  created  {target}")
    print("\nNext: fill jevcheck.toml (target, login selectors, datastore, personas), write "
          "create_account/delete_account in jevcheck_hooks.py, add `.jevcheck/` to .gitignore, "
          "then `jevcheck doctor --login <persona>`.")
    return 0


def cmd_doctor(args):
    cfg = config.load(args.config)
    base = (args.base or cfg.base).rstrip("/")
    ok = True

    def line(good, what, detail=""):
        nonlocal ok
        ok = ok and good is not False
        mark = {True: "ok ", False: "BAD", None: "-- "}[good]
        print(f"  {mark} {what}{': ' + detail if detail else ''}")

    print(f"config  : {cfg.path}")
    try:
        config.guard_target(cfg, base)
        line(True, "target allowed", base)
    except SystemExit as exc:
        line(False, "target", str(exc).splitlines()[0])
    try:
        with urllib.request.urlopen(urllib.request.Request(base + cfg.login.path, headers={"User-Agent": "jevcheck"}),
                                    timeout=15) as r:
            line(True, "login page answers", f"{r.status} {base}{cfg.login.path}")
    except urllib.error.HTTPError as exc:
        line(False, "login page", f"HTTP {exc.code}")
    except Exception as exc:
        line(False, "login page unreachable", str(exc)[:120])
    line(bool((os.environ.get("TYPESAFE_API_KEY") or "").strip()), "TYPESAFE_API_KEY set")
    line(True if (os.environ.get("TEXT_MODEL_API_KEY") or "").strip() else None,
         "TEXT_MODEL_API_KEY set", "" if os.environ.get("TEXT_MODEL_API_KEY") else "optional — only goals that type need it")
    try:
        line(True, "browser", chrome.find_chrome())
    except RuntimeError as exc:
        line(False, "browser", str(exc))
    db = store.open_store(cfg)
    if db.kind == "none":
        line(None, "datastore", "none — checks can only read the page")
    else:
        try:
            db.sql("select 1 as ok")
            line(True, "datastore", db.describe())
        except Exception as exc:
            line(False, "datastore", f"{db.describe()}: {str(exc)[:160]}")
    for name, pc in cfg.personas.items():
        kind = "static" if pc.get("email") else "throw-away"
        line(None, f"persona {name}", f"{kind}{' — ' + pc['description'] if pc.get('description') else ''}")
    if any(not pc.get("email") for pc in cfg.personas.values()):
        hooks = accounts.load_hooks(cfg, required=True)
        missing = [f for f in ("create_account", "delete_account") if not hasattr(hooks, f)]
        line(not missing, "hooks", f"{cfg.hooks.name}" + (f" is missing {', '.join(missing)}" if missing else ""))
    if cfg.checks:
        try:
            line(True, "checks", f"{len(checks.load(cfg))} in {cfg.checks.name}")
        except SystemExit as exc:
            line(False, "checks", str(exc))

    if args.login and ok:
        run_dir = new_run_dir(cfg, "doctor")
        proc = agent.start_browser(chrome, run_dir, headed=args.headed, port_start=args.cdp_port)
        made = []
        try:
            session.seed(cfg, base)
            account = accounts.provision(cfg, db, args.login, uuid.uuid4().hex[:6])
            made.append(account)
            landed = sign_in(cfg, base, account)
            line(True, f"login as {args.login}", f"{account.email} → {landed['path']}")
        except Exception as exc:
            line(False, f"login as {args.login}", str(exc)[:300])
        finally:
            teardown_all(cfg, db, made, False, base)
            finish_browser(cfg, proc, run_dir, False)
    print("\n" + ("ready." if ok else "fix the BAD lines above first."))
    return 0 if ok else 1


def cmd_ask(args):
    cfg = config.load(args.config)
    base = (args.base or cfg.base).rstrip("/")
    config.guard_target(cfg, base)
    preflight()
    db = store.open_store(cfg)
    if args.persona:
        accounts.persona_config(cfg, args.persona)  # a typo fails before a browser starts
    expire_leftovers(cfg, db)
    run_dir = new_run_dir(cfg, "ask")
    proc = agent.start_browser(chrome, run_dir, headed=args.headed, port_start=args.cdp_port)
    made, state, error = [], None, None
    viewport = layout.parse_viewport(args.viewport)
    try:
        if not args.no_seed:
            session.seed(cfg, base)
        account = None
        if args.persona:
            account = accounts.provision(cfg, db, args.persona, uuid.uuid4().hex[:6], args.options)
            made.append(account)
            landed = sign_in(cfg, base, account)
            print(f"as      : {args.persona} — {account.email} → {landed['path']}")
            if account.vars:
                print(f"vars    : {account.vars}")
        else:
            print("as      : anonymous (no account)")
        url = base + checks.fill(args.path, account)
        print(f"goal    : {args.goal}\nopening : {url}\n")

        grab = (lambda a: shots.save_shot(a.browser.session, args.shot, args.shot_selector)) if args.shot else None
        state, error = agent.run_goal(url, args.goal, budget=args.budget or cfg.budget,
                                      busy_selector=cfg.busy_selector,
                                      record_dir=run_dir / "frames" if args.record else None,
                                      before_close=grab, viewport=viewport, probe_layout=not args.no_layout)
        if error:
            print(f"  !! {error}")
        if state is None:
            print("\nthe page never loaded — nothing to report")
            return 1
        page = state["page"]
        said = checks.text_of(state)
        print(f"\nverdict : agent said {state['status']} after {len(state['history'])} actions, "
              f"{len(state['decisions']) + len(state['text_calls'])} model calls ({state['elapsed_s']} s)")
        print(f"ended at: {page.get('url')}")
        print(f"\n--- what the page said (both observations, {len(said)} chars)\n{said[:1800]}")
        print("\n--- what this account could click here")
        for kind, label in checks.offered(state):
            print(f"  {kind:<7} {label[:70]}")
        problems = layout.findings(state)
        if state.get("layout") is not None:
            size = "x".join(str(n) for n in state["layout"].get("viewport", []))
            print(f"\n--- layout at {size or 'the default window'}"
                  + ("" if viewport else " (default window; pass --viewport phone for a phone)"))
            if state["layout"].get("error"):
                print(f"  probe failed: {state['layout']['error']}")
            elif problems:
                for problem in problems:
                    print(f"  ! {problem}")
                print("  Look at --shot before reporting: a decoration meant to bleed off the edge is not a bug.")
            else:
                print("  fits: nothing off-screen, nothing covered, no sideways scroll")
        sql_rows = None
        if args.sql:
            sql_rows = db.sql(args.sql)
            print(f"\n--- sql: {args.sql}\n{json.dumps(sql_rows, indent=1, default=str)[:1500]}")
        (run_dir / "state.json").write_text(json.dumps(agent.dumpable(state), indent=2, default=str))
        if args.json:
            print(json.dumps({
                "status": state["status"], "final_url": page.get("url"), "error": error,
                "actions": checks.actions(state), "offered": checks.offered(state),
                "layout": problems, "sql": sql_rows, "state": str(run_dir / "state.json"),
            }, default=str))
    finally:
        teardown_all(cfg, db, made, args.keep, base)
        finish_browser(cfg, proc, run_dir, args.keep)
        print(f"artifacts: {run_dir}")
    return 0


def cmd_audit(args):
    cfg = config.load(args.config)
    catalog = checks.load(cfg)
    by_key = {c.key: c for c in catalog}
    if args.list:
        for c in catalog:
            print(f"  {c.key:<30} as {c.persona or 'anonymous':<12} {c.title}")
        return 0
    wanted = [k.strip() for k in args.only.split(",") if k.strip()]
    for key in wanted:
        if key not in by_key:
            raise SystemExit(f"unknown check {key!r}. Known: {', '.join(by_key)}")
    chosen = [by_key[k] for k in wanted] or catalog
    if not chosen:
        raise SystemExit(f"{cfg.checks.name} defines no CHECKS")
    base = (args.base or cfg.base).rstrip("/")
    config.guard_target(cfg, base)
    preflight()
    db = store.open_store(cfg)
    needed = {}
    for c in chosen:
        if c.persona:
            accounts.persona_config(cfg, c.persona)
            needed.setdefault(c.persona, set()).update(c.options)
    expire_leftovers(cfg, db)

    run_dir = new_run_dir(cfg, "audit")
    proc = agent.start_browser(chrome, run_dir, headed=args.headed, port_start=args.cdp_port)
    made, results = [], []
    try:
        if not args.no_seed:
            session.seed(cfg, base)
        run_id = uuid.uuid4().hex[:6]
        by_persona = {}
        for persona, options in needed.items():
            account = accounts.provision(cfg, db, persona, run_id, sorted(options))
            made.append(account)
            by_persona[persona] = account
            print(f"account : {persona:<12} {account.email}" + (f"  vars {account.vars}" if account.vars else ""))

        signed_in = False  # False = nobody yet; None = anonymous; else the persona name
        for check in chosen:
            if signed_in is False or signed_in != check.persona:
                if signed_in is not False:
                    session.logout(cfg, base, reseed=not args.no_seed)
                if check.persona:
                    landed = sign_in(cfg, base, by_persona[check.persona])
                    print(f"\n-- signed in as {check.persona} → {landed['path']}")
                else:
                    print("\n-- anonymous")
                signed_in = check.persona
            ctx = checks.Ctx(base=base, db=db, account=by_persona.get(check.persona), accounts=by_persona)
            results.append(run_check(cfg, check, ctx, run_dir, args))
    finally:
        teardown_all(cfg, db, made, args.keep, base)
        finish_browser(cfg, proc, run_dir, args.keep)
        (run_dir / "report.json").write_text(json.dumps({"base": base, "results": results}, indent=2, default=str))

    print("\n" + "=" * 92)
    for r in results:
        failed = [n for n, good in r["checks"].items() if not good]
        line = (f"{'PASS' if r['passed'] else 'FAIL'}  {r['key']:<30} as {r['persona'] or 'anonymous':<12} "
                f"{r['actions']:>2} act {r['model_calls']:>3} calls {r['elapsed_s']:>5}s")
        if failed:
            line += "  ← " + ", ".join(failed)
        if r.get("error"):
            line += f"  [{r['error'][:80]}]"
        print(line)
    failures = [r for r in results if not r["passed"]]
    print(f"\n{len(results) - len(failures)}/{len(results)} checks passed — {run_dir}")
    if failures:
        print("A single failure can be the agent, not the app: re-run it with --only before filing anything.")
    return 1 if failures else 0


def run_check(cfg, check, ctx, run_dir, args):
    started = time.monotonic()
    url = check.path(ctx) if callable(check.path) else check.path
    url = url if url.startswith(("http://", "https://")) else ctx.url(url)
    config.guard_target(cfg, url)  # a check may build an absolute URL: same whitelist
    goal = check.goal(ctx) if callable(check.goal) else check.goal
    storage = check.local_storage(ctx) if callable(check.local_storage) else check.local_storage
    if storage:
        session.set_local_storage(ctx.base, storage, cfg.seed_path)
    print(f"\n== {check.key}: {check.title}\n   {url}")
    state, error = agent.run_goal(
        url, goal,
        budget=check.budget or args.budget or cfg.budget, busy_selector=cfg.busy_selector,
        record_dir=run_dir / check.key / "frames" if args.record else None,
        viewport=layout.parse_viewport(check.viewport or args.viewport),
    )
    if state is None:
        verdict = {"passed": False, "checks": {"page_loaded": False}}
    else:
        try:
            verdict = check.verify(ctx, state)
        except Exception as exc:
            verdict = {"passed": False, "checks": {"verification_ran": False}, "error": f"{type(exc).__name__}: {exc}"}
        (run_dir / check.key).mkdir(parents=True, exist_ok=True)
        (run_dir / check.key / "state.json").write_text(json.dumps(agent.dumpable(state), indent=2, default=str))
    acted = len((state or {}).get("history", []))
    verdict_checks = dict(verdict.get("checks", {}))
    problems = layout.findings(state)
    if check.layout and state is not None:
        # An unreadable probe fails too: a silent failure must not look like a clean layout.
        verdict_checks["layout_clean"] = layout.layout_clean(state)
        for problem in problems:
            print(f"   ! {problem}")
    if check.expect_action and not acted:
        # "The write did not happen" is trivially true when nothing was attempted.
        verdict_checks = {"agent_acted": False, **verdict_checks}
    passed = bool(verdict.get("passed")) and all(verdict_checks.values())
    return {
        "key": check.key, "title": check.title, "persona": check.persona, "url": url, "goal": goal,
        "passed": passed, "checks": verdict_checks, "error": error or verdict.get("error"),
        "agent_status": (state or {}).get("status"), "final_url": ((state or {}).get("page") or {}).get("url"),
        "actions": acted, "layout_findings": problems,
        "model_calls": len((state or {}).get("decisions", [])) + len((state or {}).get("text_calls", [])),
        "elapsed_s": round(time.monotonic() - started, 1),
    }


def cmd_cleanup(args):
    cfg = config.load(args.config)
    db = store.open_store(cfg)
    gone = accounts.purge(cfg, db, everything=args.all)
    for email in gone:
        print(f"  deleted {email}")
    profiles = chrome.purge_profiles(cfg.artifacts / "runs", older_than_hours=None if args.all else cfg.keep_hours)
    print(f"{len(gone)} throw-away account(s) and {len(profiles)} kept browser profile(s) removed")
    return 0


def main(argv=None):
    args = parse_args(argv)
    handler = {
        "init": cmd_init, "doctor": cmd_doctor, "ask": cmd_ask, "audit": cmd_audit,
        "cleanup": cmd_cleanup,
        "publish-shots": lambda a: shots.publish(a.issue, a.pairs, a.repo, a.branch),
    }[args.command]
    try:
        return handler(args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
