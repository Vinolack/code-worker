#!/usr/bin/python3
# -*- coding: utf-8 -*-
# @Desc: { 主入口模块 }
# @Date: 2023/08/29 12:13
import argparse
import asyncio
import signal
import subprocess
import sys
import time
from pathlib import Path

from redis import Redis
import uvicorn
from src.base.logging import logger

from src.config import config

APP_IMPORT_STRING = "src.server:app"
ROLE_WORKER = "worker"
ROLE_SCHEDULER = "scheduler"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("role", nargs="?", choices=[ROLE_WORKER, ROLE_SCHEDULER])
    return parser.parse_args(argv)


def run_worker(*, workers: int | None = None) -> None:
    logger.info(f"project run {config.SERVER_HOST}:{config.SERVER_PORT}")
    uvicorn.run(
        app=APP_IMPORT_STRING,
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        log_level=config.SERVER_LOG_LEVEL,
        access_log=config.SERVER_ACCESS_LOG,
        timeout_keep_alive=120,
        backlog=8192,
        workers=workers if workers is not None else config.WORKER_NUM,
    )


async def run_scheduler_service() -> None:
    from src.server import shutdown_scheduler_service, startup_scheduler_service

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            continue

    await startup_scheduler_service()
    try:
        await stop_event.wait()
    finally:
        await shutdown_scheduler_service()


def build_scheduler_command() -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), ROLE_SCHEDULER]


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


def wait_for_scheduler_signal() -> None:
    deadline = time.monotonic() + config.SCHEDULER_SIGNAL_WAIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            if scheduler_signal_exists():
                return
        except Exception as exc:
            logger.warning(f"scheduler signal check failed: {exc}")
        time.sleep(max(0.1, config.SCHEDULER_SIGNAL_WAIT_POLL_INTERVAL_SECONDS))
    raise RuntimeError("scheduler did not become healthy before worker startup")


def terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def run_compat_mode() -> None:
    logger.info("project run compatibility mode: single worker + scheduler")
    scheduler_process = subprocess.Popen(build_scheduler_command())
    try:
        wait_for_scheduler_signal()
        run_worker(workers=1)
    finally:
        terminate_process(scheduler_process)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.role == ROLE_SCHEDULER:
        logger.info("project run scheduler")
        asyncio.run(run_scheduler_service())
        return
    if args.role == ROLE_WORKER:
        run_worker()
        return
    run_compat_mode()


if __name__ == "__main__":
    main()
