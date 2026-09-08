import asyncio
import signal
import sys
from pathlib import Path

import uvicorn

# FastAPI app is optional; Telethon bot always starts.
from app.routers import api_app as fastapi_app
from app.telegram import run_telethon
from config import ENABLE_FASTAPI, FAST_API_PORT

# Ensure project root is on sys.path (uv run / direct execution)
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from app.logger import LogTag, get_logger, init_logging
except ModuleNotFoundError as exc:
    if "app.logger" in str(exc):
        raise SystemExit(
            "Cannot import app.logger — ensure app/logger/ exists (git pull).\n"
            "  ls app/logger/   # expect __init__.py setup.py config.py ...\n"
            "  rm -f app/logger.py   # remove old single-file module if present"
        ) from exc
    raise

init_logging()
logger = get_logger("main")

if sys.platform != "win32":
    try:
        import uvloop  # pyright: ignore[reportMissingImports]

        uvloop.install()
        logger.info("%s uvloop enabled", LogTag.BOOT)
    except ImportError:
        logger.warning("%s uvloop not installed — using default asyncio", LogTag.BOOT)
else:
    logger.debug("%s uvloop not supported on Windows — using default asyncio", LogTag.BOOT)


async def main():
    stop_event = asyncio.Event()

    def _handle(sig):
        logger.info("%s Received shutdown signal: %s — shutting down", LogTag.BOOT, sig)
        stop_event.set()

    loop = asyncio.get_running_loop()
    if sys.platform != "win32":

        def _signal_handler(signum, _frame):
            loop.call_soon_threadsafe(_handle, signum)

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, _signal_handler)

    from app.db.crud.secrets import ensure_secrets

    await ensure_secrets()
    logger.info("%s Secrets loaded from database", LogTag.BOOT)

    api_task = None
    server = None

    if ENABLE_FASTAPI:
        config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=FAST_API_PORT, log_level="info")
        server = uvicorn.Server(config)
        api_task = asyncio.create_task(server.serve())
        logger.info("%s FastAPI listening on port %s", LogTag.API, FAST_API_PORT)
    else:
        logger.info("%s FastAPI disabled (FASTAPI_PORT not configured)", LogTag.API)

    bot_task = asyncio.create_task(run_telethon(stop_event=stop_event))
    logger.info("%s Starting Telegram bot", LogTag.BOOT)

    stop_task = asyncio.create_task(stop_event.wait())
    tasks = [bot_task] + ([api_task] if api_task else [])
    failure = None
    try:
        done, _pending = await asyncio.wait([stop_task, *tasks], return_when=asyncio.FIRST_COMPLETED)
        if not stop_event.is_set():
            for task in done:
                if task is not stop_task:
                    failure = task.exception() if not task.cancelled() else RuntimeError("Critical task cancelled")
                    failure = failure or RuntimeError("Critical service exited unexpectedly")
                    break
    finally:
        stop_event.set()
        if server:
            server.should_exit = True
        stop_task.cancel()
        _done, pending = await asyncio.wait(tasks, timeout=10)
        for task in pending:
            task.cancel()
        await asyncio.gather(stop_task, *tasks, return_exceptions=True)
        from app.db.base import engine
        from app.db.redis import close_redis
        from app.jobs.scheduler import scheduler

        if scheduler.running:
            scheduler.shutdown(wait=False)
        await close_redis()
        await engine.dispose()
        logger.info("%s Shutdown complete", LogTag.BOOT)
    if failure:
        raise failure


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt, asyncio.CancelledError:
        pass
    except RuntimeError as e:
        if "event loop stopped" not in str(e).lower():
            raise
