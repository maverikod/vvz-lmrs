"""LMRS server process manager.

A systemd-like manager for the LMRS adapter server: ``start``, ``stop``,
``status`` and ``restart``, with reliable liveness detection combining
PID-file liveness with an HTTP ``/health`` probe over TLS. Uses only the
standard library so it works on a base install (without the server extra).

Production paths (config ``/etc/lmrs``, runtime/pid ``/var/lmrs``, logs
``/var/log/lmrs``) are the defaults and can be overridden via the
``LMRS_CONFIG``, ``LMRS_RUN_DIR`` and ``LMRS_LOG_DIR`` environment
variables.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_CONFIG_PATH = "/etc/lmrs/config.json"
DEFAULT_RUN_DIR = "/var/lmrs"
DEFAULT_LOG_DIR = "/var/log/lmrs"
PID_FILENAME = "adapter.pid"
LOG_FILENAME = "adapter.log"
STOP_GRACE_SECONDS = 15.0
HEALTH_TIMEOUT_SECONDS = 45.0
PORT_FREE_TIMEOUT_SECONDS = 30.0


def config_path() -> str:
    """Return the configuration file path.

    Returns:
        The path from ``LMRS_CONFIG`` or the production default.
    """
    return os.environ.get("LMRS_CONFIG", DEFAULT_CONFIG_PATH)


def run_dir() -> Path:
    """Return the runtime/state directory.

    Returns:
        The directory from ``LMRS_RUN_DIR`` or the production default.
    """
    return Path(os.environ.get("LMRS_RUN_DIR", DEFAULT_RUN_DIR))


def log_dir() -> Path:
    """Return the log directory.

    Returns:
        The directory from ``LMRS_LOG_DIR`` or the production default.
    """
    return Path(os.environ.get("LMRS_LOG_DIR", DEFAULT_LOG_DIR))


def pid_file() -> Path:
    """Return the PID file path.

    Returns:
        Path to the PID file under the runtime directory.
    """
    return run_dir() / PID_FILENAME


def _read_state() -> Tuple[Optional[int], Optional[str]]:
    """Read the recorded PID and config path from the PID file.

    Returns:
        A ``(pid, config_path)`` tuple; entries are ``None`` when absent or
        unreadable.
    """
    path = pid_file()
    if not path.is_file():
        return None, None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, None
    if not lines:
        return None, None
    try:
        pid = int(lines[0].strip())
    except ValueError:
        return None, None
    cfg = lines[1].strip() if len(lines) > 1 else None
    return pid, cfg


def _write_state(pid: int, cfg: str) -> None:
    """Write the PID and config path to the PID file.

    Args:
        pid: Process id of the started server.
        cfg: Absolute configuration path used to start it.

    Returns:
        None.
    """
    run_dir().mkdir(parents=True, exist_ok=True)
    pid_file().write_text(f"{pid}\n{cfg}\n", encoding="utf-8")


def _clear_state() -> None:
    """Remove the PID file if present.

    Returns:
        None.
    """
    try:
        pid_file().unlink()
    except FileNotFoundError:
        pass


def _pid_alive(pid: int) -> bool:
    """Report whether a process with ``pid`` exists.

    Args:
        pid: Process id to probe.

    Returns:
        True if the process exists, False otherwise.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _load_config(cfg: str) -> Dict[str, Any]:
    """Load a configuration document.

    Args:
        cfg: Path to the JSON configuration file.

    Returns:
        The parsed document, or an empty dict on failure.
    """
    try:
        with open(cfg, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _server_section(cfg: str) -> Dict[str, Any]:
    """Return the ``server`` section of a configuration file.

    Args:
        cfg: Path to the JSON configuration file.

    Returns:
        The server section, or an empty dict when absent.
    """
    server = _load_config(cfg).get("server")
    return server if isinstance(server, dict) else {}


def _listen_endpoint(cfg: str) -> Tuple[str, int]:
    """Return the configured listen host and port.

    Args:
        cfg: Path to the JSON configuration file.

    Returns:
        A ``(host, port)`` tuple, with wildcard hosts mapped to loopback.
    """
    server = _server_section(cfg)
    host = str(server.get("host", "127.0.0.1"))
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return host, int(server.get("port", 8012))


def _health_url(cfg: str) -> str:
    """Build the ``/health`` URL from a configuration file.

    Args:
        cfg: Path to the JSON configuration file.

    Returns:
        The fully qualified health URL.
    """
    server = _server_section(cfg)
    protocol = str(server.get("protocol", "http"))
    scheme = "https" if protocol in ("https", "mtls") else "http"
    host, port = _listen_endpoint(cfg)
    return f"{scheme}://{host}:{port}/health"


def _health_ok(cfg: str, timeout: float = 2.0) -> bool:
    """Probe the server ``/health`` endpoint.

    Args:
        cfg: Path to the JSON configuration file.
        timeout: Per-request timeout in seconds.

    Returns:
        True when ``/health`` returns HTTP 200, False otherwise.
    """
    url = _health_url(cfg)
    context: Optional[ssl.SSLContext] = None
    if url.startswith("https"):
        ssl_section = _server_section(cfg).get("ssl") or {}
        ca = ssl_section.get("ca")
        try:
            context = ssl.create_default_context(cafile=ca)
        except (OSError, ssl.SSLError):
            return False
    try:
        with urllib.request.urlopen(
            url, timeout=timeout, context=context
        ) as response:
            return bool(response.status == 200)
    except Exception:
        return False


def _wait_health(cfg: str, timeout: float) -> bool:
    """Wait until ``/health`` responds or a timeout elapses.

    Args:
        cfg: Path to the JSON configuration file.
        timeout: Maximum seconds to wait.

    Returns:
        True if the server became healthy in time, False otherwise.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _health_ok(cfg):
            return True
        time.sleep(0.3)
    return False


def _port_free(host: str, port: int) -> bool:
    """Report whether a TCP port is free to bind.

    Args:
        host: Host address to test.
        port: TCP port to test.

    Returns:
        True if nothing is listening, False if a connection succeeds.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) != 0


def _kill(pid: int, grace: float = STOP_GRACE_SECONDS) -> None:
    """Terminate a process, escalating to SIGKILL after a grace period.

    Args:
        pid: Process id to terminate.
        grace: Seconds to wait for graceful exit before SIGKILL.

    Returns:
        None.
    """
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def start(cfg: Optional[str] = None, *, wait: bool = False) -> int:
    """Start the LMRS server if it is not already running.

    Args:
        cfg: Configuration path (defaults to the resolved config path).
        wait: Whether to wait for ``/health`` before returning.

    Returns:
        ``0`` on success, ``1`` on failure.
    """
    resolved = cfg or config_path()
    pid, _ = _read_state()
    if pid is not None and _pid_alive(pid):
        print(f"lmrsmgr: already running (pid {pid})", file=sys.stderr)
        return 1
    if pid is not None:
        _clear_state()
    run_dir().mkdir(parents=True, exist_ok=True)
    log_dir().mkdir(parents=True, exist_ok=True)
    log_path = log_dir() / LOG_FILENAME
    with open(log_path, "ab", buffering=0) as log_handle:
        proc = subprocess.Popen(
            [sys.executable, "-m", "lmrs", "--config", resolved],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    time.sleep(0.4)
    if proc.poll() is not None:
        print(
            f"lmrsmgr: process exited immediately (code {proc.returncode}); "
            f"see {log_path}",
            file=sys.stderr,
        )
        return 1
    _write_state(proc.pid, resolved)
    print(f"lmrsmgr: started pid {proc.pid} config={resolved}")
    if wait and not _wait_health(resolved, HEALTH_TIMEOUT_SECONDS):
        print(
            "lmrsmgr: health check did not pass; stopping",
            file=sys.stderr,
        )
        _kill(proc.pid)
        _clear_state()
        return 1
    return 0


def stop() -> int:
    """Stop the running LMRS server.

    Returns:
        ``0`` on success or when the server is not running.
    """
    pid, _ = _read_state()
    if pid is None:
        print("lmrsmgr: not running (no pid file)")
        return 0
    if not _pid_alive(pid):
        print(f"lmrsmgr: stale pid {pid}, cleaning up")
        _clear_state()
        return 0
    _kill(pid)
    _clear_state()
    print(f"lmrsmgr: stopped pid {pid}")
    return 0


def status() -> int:
    """Report whether the server is running and healthy.

    Returns:
        ``0`` when running and healthy, ``1`` when the process is alive but
        ``/health`` fails, ``3`` when the server is stopped.
    """
    pid, cfg = _read_state()
    resolved = cfg or config_path()
    if pid is None or not _pid_alive(pid):
        if pid is not None:
            _clear_state()
        print("lmrsmgr: stopped")
        return 3
    if _health_ok(resolved):
        print(f"lmrsmgr: running (pid {pid}), health OK")
        return 0
    print(f"lmrsmgr: running (pid {pid}) but health FAILED", file=sys.stderr)
    return 1


def restart(cfg: Optional[str] = None) -> int:
    """Restart the LMRS server, waiting for health on startup.

    Args:
        cfg: Configuration path (defaults to the resolved config path).

    Returns:
        ``0`` on success, ``1`` on failure.
    """
    resolved = cfg or config_path()
    stop()
    host, port = _listen_endpoint(resolved)
    deadline = time.monotonic() + PORT_FREE_TIMEOUT_SECONDS
    while time.monotonic() < deadline and not _port_free(host, port):
        time.sleep(0.3)
    return start(resolved, wait=True)


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point for the ``lmrsmgr`` command.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        A process exit status code.
    """
    parser = argparse.ArgumentParser(
        prog="lmrsmgr",
        description="Manage the LMRS adapter server (start/stop/status).",
    )
    parser.add_argument(
        "action",
        choices=("start", "stop", "status", "restart"),
        help="Management action to perform.",
    )
    parser.add_argument(
        "--config",
        dest="config",
        default=None,
        help="Path to the server configuration file.",
    )
    args = parser.parse_args(argv)
    if args.action == "start":
        return start(args.config)
    if args.action == "stop":
        return stop()
    if args.action == "restart":
        return restart(args.config)
    return status()


if __name__ == "__main__":
    sys.exit(main())
