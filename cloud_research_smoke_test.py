"""End-to-end cloud dependency smoke test run inside GitHub Actions."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from urllib import error as urllib_error
from urllib import request as urllib_request

from youtube_strategy_engine import (
    AlpacaMarketData,
    GitHubCloudBackup,
    StrategyStore,
)

UTC = timezone.utc


def env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def require_env(*names: str) -> None:
    missing = [name for name in names if not env(name)]
    if missing:
        raise RuntimeError("Missing required Actions secret/environment value: " + ", ".join(missing))


def check_private_backup() -> str:
    repository = env("GITHUB_BACKUP_REPOSITORY")
    token = env("GITHUB_BACKUP_TOKEN")
    branch = env("GITHUB_BACKUP_BRANCH")
    library_path = env(
        "GITHUB_BACKUP_PATH",
        "trading-intelligence-lab/intelligence_library.json",
    )
    main = GitHubCloudBackup(
        repository,
        token,
        branch=branch,
        path=library_path,
    )
    main.read_library()

    health_path = env(
        "TRADING_SYSTEM_HEALTH_BACKUP_PATH",
        "trading-intelligence-lab/system_health.json",
    )
    health = GitHubCloudBackup(
        repository,
        token,
        branch=branch,
        path=health_path,
    )
    current = health.read_library()
    data = StrategyStore.normalize_library(
        current["library"] if current is not None else StrategyStore.blank()
    )
    previous_updated_at = data.get("updated_at")
    now_text = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    data["updated_at"] = now_text
    data["system_health"] = {
        "checked_at": now_text,
        "source": "github-actions-cloud-smoke-test",
        "status": "pass",
    }
    health.save_library(
        data,
        previous_updated_at=previous_updated_at,
    )
    return "Private backup read and write succeeded."


def check_alpaca() -> str:
    market = AlpacaMarketData(
        env("ALPACA_API_KEY"),
        env("ALPACA_SECRET_KEY"),
        env("ALPACA_LIVE_FEED", "iex"),
        env("ALPACA_HISTORICAL_FEED", "sip"),
    )
    snapshots = market.snapshots(["SPY"])
    if not isinstance(snapshots, dict) or "SPY" not in snapshots:
        raise RuntimeError("Alpaca authenticated but did not return a SPY snapshot.")
    return "Alpaca authenticated and returned market data."


def check_gemini() -> str:
    key = env("GEMINI_API_KEY")
    url = "https://generativelanguage.googleapis.com/v1beta/models?key=" + key
    req = urllib_request.Request(
        url,
        method="GET",
        headers={"User-Agent": "Trading-Intelligence-Lab-Health"},
    )
    try:
        with urllib_request.urlopen(req, timeout=20) as response:
            status = int(getattr(response, "status", 0) or 0)
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib_error.HTTPError as exc:
        raise RuntimeError(f"Gemini authentication check failed with HTTP {exc.code}.") from exc
    if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise RuntimeError("Gemini returned an unexpected model-list response.")
    return "Gemini authenticated successfully without running a generation."


def main() -> int:
    require_env(
        "GITHUB_BACKUP_REPOSITORY",
        "GITHUB_BACKUP_TOKEN",
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
        "GEMINI_API_KEY",
    )
    checks = [
        ("Private backup", check_private_backup),
        ("Alpaca", check_alpaca),
        ("Gemini", check_gemini),
    ]
    failures = []
    for name, func in checks:
        try:
            detail = func()
            print(f"PASS · {name} · {detail}", flush=True)
        except Exception as exc:
            message = str(exc).replace("\n", " ")[:500]
            failures.append((name, message))
            print(f"FAIL · {name} · {message}", flush=True)

    if failures:
        print(f"Cloud smoke test failed {len(failures)} check(s).", flush=True)
        return 1
    print("PASS · End-to-end cloud smoke test completed successfully.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
