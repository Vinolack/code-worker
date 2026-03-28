#!/usr/bin/env python3
import argparse
import sys
import time
from pathlib import Path

from redis import Redis

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=0)
    parser.add_argument("--poll-interval", type=float, default=config.SCHEDULER_SIGNAL_WAIT_POLL_INTERVAL_SECONDS)
    return parser.parse_args()


def scheduler_signal_exists() -> bool:
    client = Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        password=config.REDIS_PASSWORD,
        decode_responses=True,
    )
    try:
        return bool(client.get(config.SCHEDULER_SIGNAL_KEY))
    finally:
        client.close()


def main() -> int:
    args = parse_args()
    deadline = time.monotonic() + max(0.0, args.timeout)

    while True:
        try:
            if scheduler_signal_exists():
                return 0
        except Exception as exc:
            print(f"scheduler signal check failed: {exc}", file=sys.stderr)
        if args.timeout <= 0 or time.monotonic() >= deadline:
            return 1
        time.sleep(max(0.1, args.poll_interval))


if __name__ == "__main__":
    raise SystemExit(main())
