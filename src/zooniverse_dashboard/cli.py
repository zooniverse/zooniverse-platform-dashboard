"""Explicit CLI for history backfills and manually requested one-off refreshes."""

import argparse
from datetime import date, datetime, timedelta, timezone

from zooniverse_dashboard.refresh import refresh_all, refresh_lock, refresh_source


def main():
    parser = argparse.ArgumentParser(description="Refresh aggregate-only Zooniverse snapshots")
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("refresh", help="Refresh all sources with durable cooldown")
    backfill = commands.add_parser("backfill", help="Explicitly fetch older ERAS history")
    backfill.add_argument("--source", required=True, choices=("classifications", "talks"))
    backfill.add_argument("--start", type=date.fromisoformat, required=True)
    backfill.add_argument(
        "--through",
        type=date.fromisoformat,
        default=datetime.now(timezone.utc).date() - timedelta(days=1),
    )
    args = parser.parse_args()
    if args.action == "refresh":
        result = refresh_all()
        print("Refresh result:", result["state"], result["streams"])
    else:
        with refresh_lock():
            snapshot = refresh_source(args.source, through=args.through, backfill_start=args.start)
        print(
            f"{args.source}: {snapshot.first}–{snapshot.last} "
            f"last modified {snapshot.last_modified.isoformat()}"
        )


if __name__ == "__main__":
    main()
