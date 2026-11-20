#!/usr/bin/env python3
"""
agent-ops-checks — alert-bot

Lightweight ops monitor for agent infrastructure. Runs three generic checks
and logs every alert as one JSON object per line (JSONL) so the output can be
tailed, parsed, or fed to a dashboard or dispatcher.

Checks:
  1. Wallet gas — ETH balance of each configured wallet, via `cast balance`
     (Foundry). Alerts when a wallet drops below the gas threshold.
  2. Process health — each configured process pattern must have exactly one
     matching process running (pgrep). Missing process = CRITICAL, more
     than one = WARNING.
  3. API keys — each configured env var must be set and non-empty.

No alert delivery is built in: this script only logs JSONL. Pair it with an
alert dispatcher (Telegram/Discord/email) that tails the log file if you
want delivery.

Configuration is entirely through environment variables (see .env.example).
Nothing in this file is tied to a specific wallet, service, or provider.

Usage:
    python3 alert-bot.py [--test] [--log-file PATH]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# Alert log: ALERT_LOG env var, or logs/alert-bot.jsonl next to this script.
ALERT_LOG = Path(os.getenv("ALERT_LOG", SCRIPT_DIR / "logs" / "alert-bot.jsonl"))

# Thresholds (overridable via env)
GAS_THRESHOLD = float(os.getenv("GAS_THRESHOLD_ETH", "0.0005"))  # ETH

# RPC endpoint for balance checks. Explicit BASE_RPC_URL wins; otherwise an
# Alchemy URL is built when ALCHEMY_API_KEY is set; otherwise the public
# Base RPC is used.
BASE_RPC_URL = os.getenv(
    "BASE_RPC_URL",
    "https://base-mainnet.g.alchemy.com/v2/" + os.getenv("ALCHEMY_API_KEY", "")
    if os.getenv("ALCHEMY_API_KEY")
    else "https://mainnet.base.org",
)

# Default API keys to check for presence. Override with REQUIRED_API_KEYS
# (comma-separated env var names, e.g. "GROQ_API_KEY,OPENAI_API_KEY").
DEFAULT_API_KEYS = ["GROQ_API_KEY", "MISTRAL_API_KEY", "OPENROUTER_API_KEY",
                    "CEREBRAS_API_KEY", "NVIDIA_API_KEY"]

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def log_alert(level, message, data=None):
    """Append one alert as a JSON line and echo it."""
    ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(),
        "level": level,
        "message": message,
        "data": data or {},
    }
    with open(ALERT_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"[{level}] {message}")


def load_wallets():
    """Wallets to watch, as {name: address}.

    Sources, in order:
      1. WATCH_WALLETS env var: "name:0x...,name:0x..."
      2. wallets.json next to this script (or WALLETS_FILE): {"name": "0x..."}
    Returns {} when nothing is configured.
    """
    wallets = {}
    env_wallets = os.getenv("WATCH_WALLETS", "").strip()
    if env_wallets:
        for item in env_wallets.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" not in item:
                log_alert("WARNING", f"Ignoring malformed WATCH_WALLETS entry: {item!r} (want name:0x...)")
                continue
            name, address = item.split(":", 1)
            wallets[name.strip()] = address.strip()
        return wallets

    wallets_file = Path(os.getenv("WALLETS_FILE", SCRIPT_DIR / "wallets.json"))
    if wallets_file.exists():
        try:
            data = json.loads(wallets_file.read_text())
            if isinstance(data, dict):
                wallets = {str(k): str(v) for k, v in data.items()}
        except (OSError, ValueError) as e:
            log_alert("ERROR", f"Could not read wallets file {wallets_file}: {e}")
    return wallets


def check_wallet_gas():
    """Check each configured wallet for low ETH (gas) balance."""
    wallets = load_wallets()
    if not wallets:
        print("No wallets configured (set WATCH_WALLETS or wallets.json) — skipping gas check.")
        return

    if shutil.which("cast") is None:
        log_alert("ERROR", "cast (Foundry CLI) not found on PATH — cannot check wallet gas. "
                           "Install Foundry or remove the gas check from your run.")
        return

    for name, address in wallets.items():
        if not ADDRESS_RE.match(address):
            log_alert("WARNING", f"Skipping wallet {name!r}: {address!r} is not a valid 0x address")
            continue
        try:
            result = subprocess.run(
                ["cast", "balance", address, "--rpc-url", BASE_RPC_URL],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                log_alert("ERROR", f"Failed to check {name} gas: {result.stderr.strip() or 'cast exited non-zero'}")
                continue
            eth = int(result.stdout.strip()) / 1e18
            if eth < GAS_THRESHOLD:
                log_alert("WARNING", f"{name} wallet low on gas: {eth:.6f} ETH",
                          {"wallet": address, "threshold": GAS_THRESHOLD})
        except Exception as e:
            log_alert("ERROR", f"Failed to check {name} gas: {e}")


def load_processes():
    """Process patterns to watch, as {name: pattern}.

    CHECK_PROCESSES env var: "name:pattern,name:pattern". A bare item with
    no colon is used as both name and pattern.
    """
    processes = {}
    env_processes = os.getenv("CHECK_PROCESSES", "").strip()
    if not env_processes:
        return processes
    for item in env_processes.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            name, pattern = item.split(":", 1)
            processes[name.strip()] = pattern.strip()
        else:
            processes[item] = item
    return processes


def check_processes():
    """Check each configured process pattern is running exactly once."""
    processes = load_processes()
    if not processes:
        print("No processes configured (set CHECK_PROCESSES) — skipping process check.")
        return

    if shutil.which("pgrep") is None:
        log_alert("ERROR", "pgrep not found on PATH — cannot check process health.")
        return

    for name, pattern in processes.items():
        try:
            result = subprocess.run(
                ["pgrep", "-f", pattern],
                capture_output=True, text=True, timeout=5,
            )
        except Exception as e:
            log_alert("ERROR", f"Failed to check process {name!r}: {e}")
            continue
        pids = result.stdout.strip().split()
        if not pids:
            log_alert("CRITICAL", f"Process {name!r} is NOT running (pattern: {pattern!r})")
        elif len(pids) > 1:
            log_alert("WARNING", f"Multiple instances of {name!r} detected: {pids}")


def check_api_keys():
    """Warn for each required API key that is missing from the environment."""
    required = [k.strip() for k in
                os.getenv("REQUIRED_API_KEYS", ",".join(DEFAULT_API_KEYS)).split(",")
                if k.strip()]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        log_alert("WARNING", f"Missing API keys: {', '.join(missing)}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check wallet gas, process health, and API keys; log alerts as JSONL."
    )
    parser.add_argument("--test", action="store_true",
                        help="also write a TEST alert to the log")
    parser.add_argument("--log-file", default=None,
                        help="override the alert log path (default: logs/alert-bot.jsonl)")
    args = parser.parse_args(argv)

    if args.log_file:
        global ALERT_LOG
        ALERT_LOG = Path(args.log_file)

    print("=== Alert Bot ===")
    print(f"Time: {datetime.now().isoformat()}")
    print(f"Log:  {ALERT_LOG}")

    check_wallet_gas()
    check_processes()
    check_api_keys()

    if args.test:
        log_alert("TEST", "Alert bot test message")

    print(f"\nDone. Alerts logged to: {ALERT_LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
