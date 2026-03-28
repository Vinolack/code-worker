#!/usr/bin/env python3
import argparse
import sys
from collections import deque
from pathlib import Path

START_LINE = "scheduler startup success"
SHUTDOWN_LINE = "scheduler shutdown"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-file", default="logs/server.log")
    parser.add_argument("--tail-lines", type=int, default=300)
    parser.add_argument("--expected-starts", type=int, default=1)
    parser.add_argument("--max-shutdowns", type=int, default=0)
    return parser.parse_args()


def load_recent_lines(log_file: Path, tail_lines: int) -> list[str]:
    if not log_file.exists():
        raise FileNotFoundError(f"log file not found: {log_file}")
    with log_file.open("r", encoding="utf-8") as handle:
        return list(deque(handle, maxlen=tail_lines))


def count_matches(lines: list[str], needle: str) -> int:
    return sum(1 for line in lines if needle in line)


def main() -> int:
    args = parse_args()
    log_file = Path(args.log_file)
    recent_lines = load_recent_lines(log_file=log_file, tail_lines=args.tail_lines)

    start_count = count_matches(recent_lines, START_LINE)
    shutdown_count = count_matches(recent_lines, SHUTDOWN_LINE)

    print(f"checked_file={log_file}")
    print(f"tail_lines={args.tail_lines}")
    print(f"start_count={start_count}")
    print(f"shutdown_count={shutdown_count}")

    if start_count != args.expected_starts:
        print(
            f"FAIL: expected {args.expected_starts} scheduler startup log(s), found {start_count}",
            file=sys.stderr,
        )
        return 1

    if shutdown_count > args.max_shutdowns:
        print(
            f"FAIL: expected at most {args.max_shutdowns} scheduler shutdown log(s), found {shutdown_count}",
            file=sys.stderr,
        )
        return 1

    print("PASS: scheduler startup log count matches expectation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
