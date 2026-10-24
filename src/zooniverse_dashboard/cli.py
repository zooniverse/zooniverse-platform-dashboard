"""Direct, argument-free refresh and durable date-limit configuration commands."""

from __future__ import annotations

import argparse

from zooniverse_dashboard.collection_config import effective_history_days, set_history_days
from zooniverse_dashboard.refresh import refresh_all, refresh_lock


def refresh_data() -> None:
    """Refresh all daily snapshots with the shared lock and cooldown."""
    argparse.ArgumentParser(description="Refresh all Zooniverse aggregate snapshots").parse_args()
    result = refresh_all()
    print("Refresh result:", result["state"], result["streams"])
    if result["state"] != "ok":
        raise SystemExit(1)


def set_date_limit() -> None:
    """Show or persist the number of historical UTC days; 0 means all-time."""
    parser = argparse.ArgumentParser(
        description="Set historical collection window in completed UTC days (0 = all)"
    )
    parser.add_argument(
        "days", type=int, nargs="?", help="0 for all history, or a positive day count"
    )
    args = parser.parse_args()
    if args.days is None:
        value = effective_history_days()
        print(f"Current history limit: {value} ({'all available' if value == 0 else 'days'})")
        return
    with refresh_lock():
        set_history_days(args.days)
    print(f"History limit set to {args.days}; run refresh-data to update snapshots")
