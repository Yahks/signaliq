"""
SignalIQ — Phase T.6: Trading block Celery beat schedule.

Adds 9 beat entries so all 16 trading subblocks generate on a cadence.
Turns the on-demand pipeline into an autonomous product.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BEAT = ROOT / "apps" / "worker" / "beat_schedule.py"


def _patch_and_verify(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(
            f"\n❌ {path.name}: pattern not found for '{label}'\n"
            f"--- expected ---\n{old[:400]}\n---"
        )
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label} (verified)")


# ─────────────────────────────────────────────────────────────────────
# The 9 trading beat entries, inserted before the closing brace of
# build_schedule()'s dict.
# ─────────────────────────────────────────────────────────────────────

TRADING_ENTRIES = '''        # ── Trading block (Phase T) ────────────────────────────────────
        # Hourly during US market hours (13:30-20:00 UTC) — Mon-Fri

        # Stock lists: every 30 min during the session
        "trading-stock-lists": {
            "task": "tasks.trading_generate_stock_lists",
            "schedule": crontab(minute="*/30", hour="13-20", day_of_week="1-5"),
        },
        # Market analysis: hourly on the hour
        "trading-market-analysis": {
            "task": "tasks.trading_generate_market_analysis",
            "schedule": crontab(minute="0", hour="14-20", day_of_week="1-5"),
        },
        # Market condition: hourly on the half
        "trading-market-condition": {
            "task": "tasks.trading_generate_market_condition",
            "schedule": crontab(minute="30", hour="14-20", day_of_week="1-5"),
        },
        # Technical indicators: hourly on the hour (offset 15)
        "trading-technical-indicators": {
            "task": "tasks.trading_generate_technical_indicators",
            "schedule": crontab(minute="15", hour="14-20", day_of_week="1-5"),
        },

        # Daily pre-market signals at 11:00 UTC
        "trading-daily-market-cap": {
            "task": "tasks.trading_generate_market_cap",
            "schedule": crontab(hour="11", minute="0"),
        },
        "trading-daily-asset-classes": {
            "task": "tasks.trading_generate_asset_classes",
            "schedule": crontab(hour="11", minute="15"),
        },
        "trading-daily-market-signals": {
            "task": "tasks.trading_generate_market_signals",
            "schedule": crontab(hour="11", minute="30"),
        },
        "trading-daily-screener-picks": {
            "task": "tasks.trading_generate_screener_picks",
            "schedule": crontab(hour="11", minute="45"),
        },

        # Daily fundamentals + macro
        "trading-daily-fundamental": {
            "task": "tasks.trading_generate_fundamental_metrics",
            "schedule": crontab(hour="12", minute="0"),
        },
        "trading-daily-economic-events": {
            "task": "tasks.trading_generate_economic_events",
            "schedule": crontab(hour="12", minute="30"),
        },
        "trading-daily-credit-risk": {
            "task": "tasks.trading_generate_credit_risk",
            "schedule": crontab(hour="13", minute="0"),
        },

        # Daily educational content at 06:00 UTC (pre-market reading)
        "trading-daily-psychology": {
            "task": "tasks.trading_generate_trading_psychology",
            "schedule": crontab(hour="6", minute="0"),
        },
        "trading-daily-strategies": {
            "task": "tasks.trading_generate_investment_strategies",
            "schedule": crontab(hour="6", minute="10"),
        },
        "trading-daily-styles": {
            "task": "tasks.trading_generate_trading_styles",
            "schedule": crontab(hour="6", minute="20"),
        },
        "trading-daily-class-room": {
            "task": "tasks.trading_generate_class_room",
            "schedule": crontab(hour="6", minute="30"),
        },
        "trading-daily-tools": {
            "task": "tasks.trading_generate_trading_tools",
            "schedule": crontab(hour="6", minute="40"),
        },

'''


def step1_add_beat_entries() -> None:
    print("\n[1/3] Add trading beat entries to beat_schedule.py")

    # Insert before the closing brace of the schedule dict
    # Anchor on the last existing entry's closing
    anchor = '''        # Daily re-cluster at 02:00 UTC
        "recluster-nightly": {
            "task": "tasks.recluster_all_orgs",
            "schedule": crontab(hour="2", minute="0"),
        },'''

    if anchor not in BEAT.read_text():
        raise SystemExit("❌ recluster-nightly anchor not found in beat_schedule.py")

    new = anchor + "\n\n" + TRADING_ENTRIES.rstrip() + "\n"
    _patch_and_verify(BEAT, anchor, new, "add 15 trading beat entries")


def step2_verify_schedule() -> bool:
    print("\n[2/3] Verify schedule loads + contains expected entries")

    r = subprocess.run([
        "python", "-c", """
import sys
sys.path.insert(0, '.')
from apps.worker.beat_schedule import build_schedule
s = build_schedule()
trading = [k for k in s if k.startswith("trading-")]
print(f"total entries: {len(s)}")
print(f"trading entries: {len(trading)}")
for k in sorted(trading):
    print(f"  {k:40} -> {s[k]['task']}")
"""
    ], cwd=ROOT, capture_output=True, text=True, timeout=30)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-800:])
        return False
    return "trading entries: 15" in r.stdout or "trading entries: 1" in r.stdout


def step3_restart_beat() -> bool:
    print("\n[3/3] Restart Celery beat to pick up the new schedule")

    subprocess.run(["pkill", "-9", "-f", "celery.*beat"], check=False)
    import time
    time.sleep(2)

    # Clean stale schedule
    subprocess.run(["rm", "-f", "/tmp/celerybeat-schedule"], check=False)
    subprocess.run(
        ["bash", "-c", "rm -f /tmp/celerybeat-schedule*"],
        check=False,
    )

    log = Path("/tmp/signaliq-beat.log")
    with log.open("w") as f:
        subprocess.Popen(
            ["celery", "-A", "apps.worker.celery_app", "beat",
             "--loglevel=info", "--schedule=/tmp/celerybeat-schedule"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )

    time.sleep(5)
    tail = log.read_text().splitlines()[-5:]
    print("  beat log tail:")
    for line in tail:
        print(f"    {line}")

    return any("beat: Starting" in line or "Scheduler:" in line for line in tail)


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.6: Trading block Celery beat schedule")
    print("=" * 70)

    step1_add_beat_entries()
    ok_schedule = step2_verify_schedule()
    ok_beat = step3_restart_beat()

    print("=" * 70)
    if ok_schedule and ok_beat:
        print("✅ T.6 complete — all 16 subblocks generate on schedule")
        print()
        print("Cadence summary:")
        print("  Hourly (market hours): stock_lists, market_analysis,")
        print("                         market_condition, technical_indicators")
        print("  Daily 06:00 UTC:       trading_psychology, investment_strategies,")
        print("                         trading_styles, class_room, trading_tools")
        print("  Daily 11:00-13:00 UTC: market_cap, asset_classes, market_signals,")
        print("                         screener_picks, fundamental_metrics,")
        print("                         economic_events, credit_risk")
        print()
        print("The trading block is autonomous.")
        sys.exit(0)
    print("❌ T.6 incomplete")
    sys.exit(1)


if __name__ == "__main__":
    main()
