"""Kimodo server lifecycle management — start, stop, health check.

The managed subprocess and its log handle are owned by a single ``_ServerManager``
instance instead of module globals; the module-level functions are thin wrappers
over that singleton, so callers keep the same API.
"""

import os
import signal
import subprocess
import sys
import tempfile
import time
from typing import Optional

from .client import KimodoClient

SERVER_LOG_PATH = os.path.join(tempfile.gettempdir(), "kimodo_server.log")


def get_server_app_dir() -> str:
    """Return the path to server_app/ inside the addon directory."""
    addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(addon_dir, "server_app")


def get_venv_python(venv_path: str) -> str:
    """Return the python executable inside the Kimodo venv (cross-platform)."""
    if sys.platform == "win32":
        return os.path.join(venv_path, "Scripts", "python.exe")
    return os.path.join(venv_path, "bin", "python")


def is_server_running(base_url: str) -> bool:
    return KimodoClient(base_url).health(timeout=2.0)


class _ServerManager:
    """Owns the managed server subprocess and its log file handle."""

    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen] = None
        self._log_fp = None  # keep alive so the subprocess can keep writing

    def pid(self) -> Optional[int]:
        if self._process is not None and self._process.poll() is None:
            return self._process.pid
        return None

    def start(self, venv_path: str, host: str, port: int) -> bool:
        """Start the FastAPI server as a background process; True if it came up."""
        base_url = f"http://{host}:{port}"
        if is_server_running(base_url):
            return True

        python_exe = get_venv_python(venv_path)
        if not os.path.isfile(python_exe):
            raise FileNotFoundError(f"Kimodo venv not found at {python_exe}. Run the setup script first.")
        server_app = os.path.join(get_server_app_dir(), "main.py")
        if not os.path.isfile(server_app):
            raise FileNotFoundError(f"Server app not found at {server_app}")

        env = os.environ.copy()
        env["KIMODO_HOST"] = host
        env["KIMODO_PORT"] = str(port)

        # Keep the large HuggingFace model cache (~17GB for LLaMA-3-8B) next to the venv
        # so the whole runtime lives in one place and never lands in ~/.cache/huggingface.
        # Set KIMODO_HF_HOME to override (e.g. to share a cache across projects).
        hf_home = os.environ.get("KIMODO_HF_HOME") or os.path.join(
            os.path.dirname(os.path.abspath(venv_path)), "hf-cache"
        )
        os.makedirs(hf_home, exist_ok=True)
        env["HF_HOME"] = hf_home
        env.setdefault("HF_HUB_CACHE", os.path.join(hf_home, "hub"))

        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

        # CRITICAL: never use subprocess.PIPE without a reader — on Windows the pipe
        # buffer is ~4KB and once full the child's write() blocks forever, freezing
        # uvicorn's event loop. Redirect to a log file instead (the kernel buffers).
        try:
            self._log_fp = open(SERVER_LOG_PATH, "a", encoding="utf-8", buffering=1)
            self._log_fp.write(f"\n\n===== server start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            self._log_fp.flush()
        except Exception:
            self._log_fp = None  # fall back to DEVNULL if the log file is unavailable

        self._process = subprocess.Popen(
            [python_exe, server_app],
            env=env,
            stdout=self._log_fp if self._log_fp else subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )

        # Wait for it to respond (max ~60s for model loading).
        for _ in range(120):
            if self._process.poll() is not None:
                tail = ""
                try:
                    with open(SERVER_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                        tail = f.read()[-1000:]
                except OSError:
                    pass
                raise RuntimeError(f"Server exited prematurely. Log tail:\n{tail}")
            if is_server_running(base_url):
                return True
            time.sleep(0.5)
        raise TimeoutError("Server did not respond within 60 seconds")

    def stop(self) -> None:
        if self._process is None:
            return
        try:
            if sys.platform == "win32":
                self._process.terminate()
            else:
                self._process.send_signal(signal.SIGTERM)
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._process.kill()
        finally:
            self._process = None
            if self._log_fp is not None:
                try:
                    self._log_fp.close()
                except Exception:
                    pass
                self._log_fp = None


_server = _ServerManager()


def start_server(venv_path: str, host: str, port: int) -> bool:
    return _server.start(venv_path, host, port)


def stop_server() -> None:
    _server.stop()


def get_server_pid() -> Optional[int]:
    return _server.pid()
