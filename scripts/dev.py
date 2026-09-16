"""Run the local API and frontend together, stopping both on exit."""

import os
import signal
import subprocess
import time
from contextlib import suppress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    processes: list[subprocess.Popen] = []

    def stop(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        for command in (
            [
                "uv",
                "run",
                "--frozen",
                "uvicorn",
                "trend_sift.api.app:app",
                "--reload",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            ["pnpm", "--dir", "web", "run", "dev"],
        ):
            processes.append(
                subprocess.Popen(
                    command,
                    cwd=ROOT,
                    start_new_session=True,
                    env={**os.environ, "SCHEDULE_ENABLED": "false"},
                )
            )
        print("本地预览：http://127.0.0.1:8111（Ctrl+C 停止前后端）", flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(0.2)
        return next(process.returncode for process in processes if process.returncode is not None)
    except KeyboardInterrupt:
        return 0
    finally:
        for process in processes:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
