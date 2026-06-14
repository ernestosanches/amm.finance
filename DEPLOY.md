# DEPLOY.md — run instructions

Everything in this repo, how to run it. Two deliverables:

- **Research pipeline** — reconstruct a Uniswap v3 pool from on-chain data and view the figures.
- **The game** — the live multiplayer market-making app.

Plus how to expose either over the internet (ephemeral link, or **amm.finance** permanently).

---

## 0. Prerequisites

```bash
# research pipeline deps (web3, matplotlib, plotly)
pip install -r requirements.txt
# game deps (fastapi, uvicorn)
pip install -r app/requirements.txt
```

Python 3.10+. The pipeline uses a **keyless public RPC** — no accounts or API keys.

---

## 1. Research pipeline (data → figures)

```bash
python run_all.py                 # download + process + build all 8 HTML figures + PNGs
python serve.py                   # serve out/ on http://127.0.0.1:8000, prints the figure links
```

- **Date range defaults to the past 5 days** (UTC). Override: `python run_all.py --start 2026-06-09 --end 2026-06-14`.
- Other flags: `--pool <addr>`, `--slices N` (time-slider granularity), `--no-baseline-rpc` (skip
  the on-chain absolute-TVL reads), `--log` (log-scaled depth).
- Rebuild figures only (no network): `python run_all.py --figures-only`.
- The download script also runs standalone: `python uniswap_v3_pool_download_rpc.py` (same 5-day default).

View the figures: `python serve.py` (local), or share them publicly with `python serve.py --tunnel`
(see §4).

---

## 2. The game

```bash
# easiest: seed a deterministic populated game and launch it live
bash app/run_demo.sh                      # http://127.0.0.1:8000  (prints the admin login)

# or a blank game you start yourself from the Admin page
python app/run.py                         # --reset for fresh db, --host 0.0.0.0 for LAN, --port N
```

**Admin login.** The admin password is **generated fresh on each server start** and printed to the
console as `[admin] login: admin / <password>`. To pin a stable one, set it yourself:

```bash
export AMM_ADMIN_PASSWORD='choose-something'   # also: AMM_ADMIN_NAME (default "admin")
```

The Admin page (top-right nav) gates Start / parameters / monitoring; player login is name-only.

**Replay / retest.** The SQLite action log is the source of truth, so the game is fully
deterministic and crash-safe:

```bash
python app/demo_seed.py --seed 7          # write a reproducible game to the db
python app/run.py                         # loads it (replays the log) and continues live
```

A `kill -9` loses nothing — restart and it replays to the exact prior state.

---

## 3. Verify

```bash
python tests.py             # 134 — analysis pipeline
python app/tests.py         # 60  — game (engine, persistence, game core, API, durability)
python app/render_check.py  # headless-browser UI smoke (screenshots -> app/out/)
python app/chaos_check.py   # kill -9 recovery + load burst + WS reconnect storm
```

---

## 4. Expose it over the internet

### 4a. Quick share — ephemeral public link (no domain, no account)

A Cloudflare **quick tunnel**: a random `https://<words>.trycloudflare.com` URL, public and
no-auth, torn down cleanly on exit. Good for a one-off share or test.

```bash
python app/app_serve.py --tunnel --seed   # the game (seeded), public URL printed
python serve.py --tunnel                  # the research figures, public URL printed
```

Needs `cloudflared` on PATH (or at `/opt/instance-tools/bin/cloudflared`, where this instance has
it). Give the Cloudflare edge ~10 s to warm up; a first hit may show error 1033 — refresh.

### 4b. Permanent — point **amm.finance** at this instance

A quick tunnel's URL is random and changes every run, so it can't serve `amm.finance`. For a stable
domain use a **named Cloudflare Tunnel** (a persistent tunnel + a DNS record). It dials **outbound**
to Cloudflare — no inbound ports to open, which is exactly why it works on a Vast/locked-down box
where every published port is taken. **Do not** use an A record to the instance IP here (no stable
inbound port; IP can change).

**One-time setup (on the instance):**

```bash
CF=/opt/instance-tools/bin/cloudflared        # or `cloudflared` if on PATH

# 1. Authenticate to your Cloudflare account and pick the amm.finance zone (opens a browser link).
$CF tunnel login

# 2. Create a named tunnel (writes a credentials file + a tunnel UUID).
$CF tunnel create amm

# 3. Create the DNS records that point the domain at the tunnel. This adds a *proxied CNAME*
#    amm.finance -> <UUID>.cfargotunnel.com in your Cloudflare DNS for you:
$CF tunnel route dns amm amm.finance
$CF tunnel route dns amm www.amm.finance
```

**Config** — map the hostname to the local app, `~/.cloudflared/config.yml`:

```yaml
tunnel: amm
credentials-file: /root/.cloudflared/<UUID>.json
ingress:
  - hostname: amm.finance
    service: http://localhost:8000
  - hostname: www.amm.finance
    service: http://localhost:8000
  - service: http_status:404
```

**Run both** (the app bound to localhost, and the tunnel):

```bash
# terminal 1 — the app (local only; the tunnel fronts it). Pin an admin password for the event:
AMM_ADMIN_PASSWORD='your-event-pw' python app/run.py --port 8000
# terminal 2 — the named tunnel
$CF tunnel run amm
```

To keep it up across disconnects/reboots, install cloudflared as a service
(`$CF service install <token>` from the Zero Trust dashboard's tunnel page) and run the app under
`tmux`/`nohup`/systemd.

**In the Cloudflare dashboard (DNS manager), confirm:**

- The **amm.finance zone** is on Cloudflare (registrar nameservers point to the ones Cloudflare
  assigned). This is the prerequisite for any of the above.
- A **CNAME** record — `Name: amm.finance` (and `www`), `Target: <UUID>.cfargotunnel.com`,
  **Proxied (orange cloud) = ON**. Step 3 above creates these; you can also add/verify them by hand.
- TLS is automatic (Cloudflare terminates HTTPS at its edge; the tunnel carries plain HTTP to
  `localhost:8000`).

---

## Answer: "start cloudflare, then add a DNS record?"

Yes — that's the right shape, with two specifics:

1. Use a **named tunnel** (§4b: `tunnel login` → `tunnel create` → `tunnel run`), **not** the
   ephemeral `--tunnel` quick tunnel. The quick tunnel is perfect for a throwaway link but its URL
   is random each run, so it can't back `amm.finance`.
2. The DNS record is a **proxied CNAME to `<tunnel-UUID>.cfargotunnel.com`** (added for you by
   `cloudflared tunnel route dns`, or by hand in the DNS panel) — not an A record to the instance IP.

So: `cloudflared tunnel login → create amm → route dns amm amm.finance` (this *is* "start the tunnel
and add the DNS record"), point its ingress at `http://localhost:8000`, run the app there, and run
the tunnel. The `--tunnel` flag in this repo stays the zero-setup option for quick shares; the named
tunnel is the permanent amm.finance path. You can keep both — the quick tunnel for ad-hoc demos and
the named tunnel for the live domain.
