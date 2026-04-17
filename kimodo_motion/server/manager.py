"""Kimodo server lifecycle management — start, stop, health check."""

import subprocess
import os
import sys
import signal
import tempfile
import time
from typing import Optional

from .client import KimodoClient


_server_process: Optional[subprocess.Popen] = None
_server_log_fp = None  # keep file handle alive so subprocess can keep writing

SERVER_LOG_PATH = os.path.join(tempfile.gettempdir(), "kimodo_server.log")


def get_server_app_dir() -> str:
    """Return path to server_app/ inside the addon directory."""
    addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(addon_dir, "server_app")


def get_venv_python(venv_path: str) -> str:
    """Return path to python executable inside the Kimodo venv."""
    if sys.platform == "win32":
        return os.path.join(venv_path, "Scripts", "python.exe")
    return os.path.join(venv_path, "bin", "python")


def is_server_running(base_url: str) -> bool:
    client = KimodoClient(base_url)
    return client.health(timeout=2.0)


def start_server(venv_path: str, host: str, port: int) -> bool:
    """Start the Kimodo FastAPI server as a background process.

    Returns True if server started successfully.
    """
    global _server_process

    base_url = f"http://{host}:{port}"

    if is_server_running(base_url):
        return True

    python_exe = get_venv_python(venv_path)
    if not os.path.isfile(python_exe):
        raise FileNotFoundError(
            f"Kimodo venv not found at {python_exe}. " f"Run the setup script first."
        )

    server_app = os.path.join(get_server_app_dir(), "main.py")
    if not os.path.isfile(server_app):
        raise FileNotFoundError(f"Server app not found at {server_app}")

    env = os.environ.copy()
    env["KIMODO_HOST"] = host
    env["KIMODO_PORT"] = str(port)

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW

    # CRITICAL: never use subprocess.PIPE without a reader — on Windows, pipe
    # buffer is ~4KB and once full the child's write() blocks forever, freezing
    # uvicorn's event loop. Redirect to a log file instead — user can tail it
    # for debugging, and the kernel handles buffering.
    global _server_log_fp
    try:
        _server_log_fp = open(SERVER_LOG_PATH, "a", encoding="utf-8", buffering=1)
        _server_log_fp.write(
            f"\n\n===== server start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n"
        )
        _server_log_fp.flush()
    except Exception:
        _server_log_fp = None  # fall back to DEVNULL if log file unavailable

    stdout_target = _server_log_fp if _server_log_fp else subprocess.DEVNULL

    _server_process = subprocess.Popen(
        [python_exe, server_app],
        env=env,
        stdout=stdout_target,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )

    # Wait for server to come up (max 60s for model loading)
    for _ in range(120):
        if _server_process.poll() is not None:
            log_tail = ""
            if os.path.isfile(SERVER_LOG_PATH):
                try:
                    with open(
                        SERVER_LOG_PATH, "r", encoding="utf-8", errors="replace"
                    ) as f:
                        log_tail = f.read()[-1000:]
                except Exception:
                    pass
            raise RuntimeError(f"Server exited prematurely. Log tail:\n{log_tail}")
        if is_server_running(base_url):
            return True
        time.sleep(0.5)

    raise TimeoutError("Server did not respond within 60 seconds")


def stop_server():
    """Stop the managed server process."""
    global _server_process, _server_log_fp
    if _server_process is None:
        return

    try:
        if sys.platform == "win32":
            _server_process.terminate()
        else:
            _server_process.send_signal(signal.SIGTERM)
        _server_process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _server_process.kill()
    finally:
        _server_process = None
        if _server_log_fp is not None:
            try:
                _server_log_fp.close()
            except Exception:
                pass
            _server_log_fp = None


def get_server_pid() -> Optional[int]:
    if _server_process and _server_process.poll() is None:
        return _server_process.pid
    return None
