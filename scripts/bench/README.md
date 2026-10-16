# Latency bench

Measures tap-to-photon latency and a per-stage breakdown. Design:
`docs/superpowers/specs/2026-10-16-latency-bench-harness-design.md`.

## One-time setup

1. Windows host: create a separate LDPlayer instance for benching (keep the
   game in its own instance).
2. Start the host from source in bench mode:
   `set EMUCTRL_BENCH=1` then `uv run python src/main.py`. The engine inherits
   the variable. Never set it on a normal install.
3. In the bench instance's browser open
   `http://10.0.2.2:8080/bench/target.html` and tap once (it goes full-screen).
4. Mac: `npm install`, then `npm run bench -- --setup --host http://<host-ip>:8080`
   and pair the opened Chrome window with a code from the host.

## Run

```bash
npm run bench -- --host http://<host-ip>:8080 --serial <bench-serial> --label baseline
npm run bench -- --host http://<host-ip>:8080 --serial <bench-serial> --label intra-refresh
npm run bench -- --compare bench-results/<a>.json bench-results/<b>.json
```

No Google Chrome installed (e.g. running on Windows)? Add `--channel msedge`
to every command to drive Edge instead. Or point `--executable` at another Chromium-based
browser, e.g. `--executable "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"`.

Each run sets the bench instance to `--tier` first (default `360`; the host
otherwise falls back to 720, which a relayed Tailscale path cannot carry).

Exit codes: 0 valid run, 2 run marked failed (see the reasons in the `.md`), 1 error.
Keep the Chrome window visible during a run.
