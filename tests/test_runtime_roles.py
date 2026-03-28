import asyncio
from unittest.mock import AsyncMock, Mock, patch

import main
from src.config import config
from src.server import shutdown, shutdown_scheduler_service, startup, startup_scheduler_service


def test_parse_args_defaults_to_compat_mode():
    args = main.parse_args([])
    assert args.role is None


def test_run_worker_uses_uvicorn_import_string_and_configured_workers():
    with patch("main.uvicorn.run") as uvicorn_run:
        main.run_worker()

    uvicorn_run.assert_called_once()
    assert uvicorn_run.call_args.kwargs["app"] == main.APP_IMPORT_STRING
    assert uvicorn_run.call_args.kwargs["workers"] == config.WORKER_NUM


def test_main_scheduler_mode_dispatches_to_scheduler_runtime():
    scheduler_coro = object()
    with patch("main.run_scheduler_service", new=Mock(return_value=scheduler_coro)) as run_scheduler_mock, patch(
        "main.asyncio.run"
    ) as asyncio_run:
        main.main([main.ROLE_SCHEDULER])

    run_scheduler_mock.assert_called_once_with()
    asyncio_run.assert_called_once_with(scheduler_coro)


def test_main_default_dispatches_to_compat_mode():
    with patch("main.run_compat_mode") as run_compat_mock:
        main.main([])

    run_compat_mock.assert_called_once_with()


def test_wait_for_scheduler_signal_returns_when_signal_appears():
    with patch.object(config, "SCHEDULER_SIGNAL_WAIT_TIMEOUT_SECONDS", 5, create=True), patch.object(
        config, "SCHEDULER_SIGNAL_WAIT_POLL_INTERVAL_SECONDS", 0.1, create=True
    ), patch("main.scheduler_signal_exists", side_effect=[False, True]) as signal_exists_mock, patch(
        "main.time.sleep"
    ) as sleep_mock:
        main.wait_for_scheduler_signal()

    assert signal_exists_mock.call_count == 2
    sleep_mock.assert_called_once()


def test_compat_mode_starts_scheduler_process_waits_for_signal_and_forces_single_worker():
    process = Mock()
    process.poll.return_value = 0
    with patch("main.subprocess.Popen", return_value=process) as popen_mock, patch("main.wait_for_scheduler_signal") as wait_mock, patch(
        "main.run_worker"
    ) as run_worker_mock, patch("main.terminate_process") as terminate_mock:
        main.run_compat_mode()

    popen_mock.assert_called_once_with(main.build_scheduler_command())
    wait_mock.assert_called_once_with()
    run_worker_mock.assert_called_once_with(workers=1)
    terminate_mock.assert_called_once_with(process)


def test_worker_startup_does_not_start_scheduler():
    async def scenario():
        with patch("src.server.init_setup", new=AsyncMock()) as init_setup_mock, patch(
            "src.server.VlmService.startup", new=AsyncMock()
        ) as vlm_start_mock, patch("src.server.scheduler_manager.start", new=AsyncMock()) as scheduler_start_mock:
            await startup()

        init_setup_mock.assert_awaited_once()
        vlm_start_mock.assert_awaited_once()
        scheduler_start_mock.assert_not_awaited()

    asyncio.run(scenario())


def test_scheduler_service_startup_only_starts_scheduler():
    async def scenario():
        with patch("src.server.init_setup", new=AsyncMock()) as init_setup_mock, patch(
            "src.server.scheduler_manager.start", new=AsyncMock()
        ) as scheduler_start_mock, patch("src.server.VlmService.startup", new=AsyncMock()) as vlm_start_mock:
            await startup_scheduler_service()

        init_setup_mock.assert_awaited_once()
        scheduler_start_mock.assert_awaited_once()
        vlm_start_mock.assert_not_awaited()

    asyncio.run(scenario())


def test_worker_shutdown_does_not_shutdown_scheduler():
    async def scenario():
        with patch("src.server.VlmService.shutdown", new=AsyncMock()) as vlm_shutdown_mock, patch(
            "src.server.ApiKeyService.close_shared_client", new=AsyncMock()
        ) as api_key_close_mock, patch(
            "src.server.scheduler_manager.shutdown", new=AsyncMock()
        ) as scheduler_shutdown_mock:
            await shutdown()

        vlm_shutdown_mock.assert_awaited_once()
        api_key_close_mock.assert_awaited_once()
        scheduler_shutdown_mock.assert_not_awaited()

    asyncio.run(scenario())


def test_scheduler_service_shutdown_only_shuts_down_scheduler():
    async def scenario():
        with patch("src.server.scheduler_manager.shutdown", new=AsyncMock()) as scheduler_shutdown_mock, patch(
            "src.server.ApiKeyService.close_shared_client", new=AsyncMock()
        ) as api_key_close_mock:
            await shutdown_scheduler_service()

        scheduler_shutdown_mock.assert_awaited_once()
        api_key_close_mock.assert_not_awaited()

    asyncio.run(scenario())
