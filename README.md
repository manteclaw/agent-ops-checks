# agent-ops-checks — alert-bot

Lightweight ops monitor for agent infrastructure. Runs three generic checks
and logs every alert as one JSON object per line (JSONL), so the output can
be tailed, parsed, or fed to a dashboard or dispatcher.

## What it checks

| Check | Severity | Condition |
|-------|----------|-----------|
| Wallet gas | WARNING | ETH balance of a watched wallet < threshold (default 0.0005 ETH) |
| Wallet gas | ERROR | `cast` missing, bad address, or RPC call failed |
| Process health | CRITICAL | A watched process pattern has no matching process |
| Process health | WARNING | More than one process matches a pattern |
| Missing API keys | WARNING | Any required API key env var is unset/empty |

**What it does not do:** deliver alerts. It only logs JSONL. Pair it with an
alert dispatcher (Telegram/Discord/email) that tails the log file if you want
delivery — for example the `agent-alerts` dispatcher pattern: read
`logs/alert-bot.jsonl` and forward new lines.

**What was removed from the original:** the source this was adapted from
also tracked a miner process and a token-earnings dashboard. Both were tied
to services that no longer exist, so they were removed rather than
generalized — the process check above is the generic replacement for the
miner check, and there is no earnings check here.

## Requirements

- Python 3.8+ (stdlib only — no pip dependencies)
- `cast` (Foundry CLI) for balance checks — only needed if you watch wallets
- `pgrep` (procps) for process checks — only needed if you watch processes

## Quick start

```bash
# 1. Copy the example config
cp .env.example .env
#    then edit .env: add your wallets, processes, and keys

# 2. Load it and run
set -a; . ./.env; set +a
python3 alert-bot.py

# 3. Test mode (also writes a TEST alert to the log)
python3 alert-bot.py --test
```

Or pass config directly as env vars without a `.env` file:

```bash
WATCH_WALLETS="hot:0xYourAddressHere,cold:0xAnotherAddressHere" \
CHECK_PROCESSES="worker:venv/bin/python.*worker.py" \
python3 alert-bot.py --log-file /tmp/alerts.jsonl
```

## Configuration

All config is env vars (see `.env.example`). Nothing is hardcoded.

| Variable | Default | Meaning |
|----------|---------|---------|
| `WATCH_WALLETS` | — (skipped) | Wallets to watch: `name:0x...,name:0x...` |
| `WALLETS_FILE` | `wallets.json` next to the script | JSON `{"name": "0x..."}` alternative to `WATCH_WALLETS` |
| `BASE_RPC_URL` | `https://mainnet.base.org` | RPC endpoint for balance checks |
| `ALCHEMY_API_KEY` | — | If set (and no `BASE_RPC_URL`), the Alchemy Base URL is used |
| `GAS_THRESHOLD_ETH` | `0.0005` | ETH balance below this triggers a WARNING |
| `CHECK_PROCESSES` | — (skipped) | Processes to watch: `name:pattern,name:pattern` (matched with `pgrep -f`) |
| `REQUIRED_API_KEYS` | `GROQ_API_KEY,MISTRAL_API_KEY,OPENROUTER_API_KEY,CEREBRAS_API_KEY,NVIDIA_API_KEY` | Comma-separated env var names that must be set |
| `ALERT_LOG` | `logs/alert-bot.jsonl` next to the script | Where JSONL alerts are written |

Notes:

- An address that isn't a valid `0x` + 40 hex chars is skipped with a
  WARNING — it never makes an RPC call.
- A check with nothing configured prints a one-line note and is skipped;
  the script never fails just because you only use one of the three checks.
- Thresholds are per-run constants; edit the env var, not the script.

## Output

One JSON object per line in the alert log:

```json
{"timestamp": "2026-11-20T12:00:00", "level": "WARNING", "message": "hot wallet low on gas: 0.000300 ETH", "data": {"wallet": "0x...", "threshold": 0.0005}}
{"timestamp": "2026-11-20T12:00:00", "level": "CRITICAL", "message": "Process 'worker' is NOT running (pattern: 'venv/bin/python.*worker.py')", "data": {}}
{"timestamp": "2026-11-20T12:00:00", "level": "WARNING", "message": "Missing API keys: GROQ_API_KEY, MISTRAL_API_KEY", "data": {}}
```

## Scheduling

Run it on a cron (every 5–15 minutes is a reasonable cadence):

```
*/10 * * * * cd /path/to/agent-ops-checks && set -a; . ./.env; set +a; python3 alert-bot.py >> logs/cron.log 2>&1
```

## Extending

To add delivery, tail the log instead of modifying the checks — e.g. a small
loop that reads new lines from `logs/alert-bot.jsonl` and POSTs them to a
webhook. Keeping delivery separate means the checks stay testable without
any credentials.

## License

MIT — see `LICENSE`.
