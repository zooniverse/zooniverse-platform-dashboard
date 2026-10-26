"""Explicit offline command: python -m zooniverse_dashboard.anomalies."""

from zooniverse_dashboard.anomalies.catalog import rebuild


def main():
    report = rebuild(progress=lambda line: print(line, flush=True))
    for source, status in report["sources"].items():
        print(
            f"{source}: {status['status']} "
            f"{status.get('episode_count', 0)} episodes "
            f"{status.get('message', '')}"
        )
    print("Saved anomaly catalog to the configured aggregate data directory")


if __name__ == "__main__":
    main()
