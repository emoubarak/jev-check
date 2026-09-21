# Launchpad: the demo app

A tiny app (Python standard library + sqlite) to watch jevcheck work before you wire it to
your own. Free accounts may have one live project. Publishing a second one opens an animated
"Upgrade to Pro" dialog. Pro accounts publish freely.

```bash
python3 server.py &                                   # http://127.0.0.1:8765
uv run --project ../.. jevcheck doctor --login free   # sign-in over CDP, no model calls
uv run --project ../.. jevcheck audit                 # 3/3 PASS

# one look, with a cropped screenshot of the paywall and a DB cross-check
uv run --project ../.. jevcheck ask --as free --with live --with draft --path "/projects/{draft}" \
  --goal "Publish this project. You are done when it is live, OR when the app refuses and says why." \
  --shot paywall.png --shot-selector "[role=dialog]" \
  --sql "select p.name, p.published from projects p join users u on u.id = p.user_id where u.email like 'jev-free-%'"
```

Now break it. `LAUNCHPAD_BUG=1 python3 server.py` ships a silent regression: over the limit,
a free account's publish "makes room" by unpublishing its other project instead of refusing.
The page looks fine and the agent says `done`. `free_publish_blocked` fails on
`draft_still_unpublished` and `live_one_untouched`, because the verdict comes from `demo.db`.

| file | what it shows |
|---|---|
| `jevcheck.toml` | a complete config: target whitelist, login selectors, sqlite datastore, two personas |
| `jevcheck_hooks.py` | throw-away accounts written exactly the way `server.py` writes them |
| `jevcheck_checks.py` | a refusal, an allowance and an anonymous check, each verified in the database |
