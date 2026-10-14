"""
core/session_manager.py
========================
Session Persistence & Recovery Manager for Automation Modules.

Stores and restores state for interrupted or aborted automation tasks.
Saves user-selected file paths (credentials, CSVs, directories) and row completion progress
so sessions can be seamlessly resumed if the system shuts down or encounters accidental disruptions.
Also provides network disruption checking utilities.
"""

import os
import json
import time
import socket
import datetime
import logging
import threading

from core.utils import describe_exception

from core.paths import get_persistent_dir

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Selenium dead-session detection
# ---------------------------------------------------------------------------

def is_dead_session_error(exc: BaseException) -> bool:
    """Return True if *exc* indicates that the Chrome/ChromeDriver session is
    unrecoverably gone (crashed, closed, handle invalidated, etc.).

    These errors are *not* transient network hiccups; retrying on the same
    driver instance will always fail in the same way, so they should be
    treated as NonRetryable and the worker should restart its driver instead.

    Recognised patterns:
    - selenium.common.exceptions.InvalidSessionIdException
    - selenium.common.exceptions.NoSuchWindowException
    - selenium.common.exceptions.WebDriverException whose message contains
      telltale ChromeDriver crash strings ('GetHandleVerifier', 'invalid session',
      'no such window', 'disconnected', 'chrome not reachable',
      'target window already closed').
    """
    try:
        from selenium.common.exceptions import (
            InvalidSessionIdException, NoSuchWindowException, WebDriverException
        )
        if isinstance(exc, (InvalidSessionIdException, NoSuchWindowException)):
            return True
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return True
        msg = str(exc).lower()
        dead_patterns = (
            "gethandleverifier",
            "invalid session id",
            "no such window",
            "disconnected: ",
            "chrome not reachable",
            "target window already closed",
            "session deleted",
            "no such execution context",
            "read timed out",
            "httpconnectionpool",
            "max retries exceeded",
            "connection refused",
            "connection reset",
            "remote disconnected",
        )
        if any(p in msg for p in dead_patterns):
            return True
    except ImportError:
        pass
    return False


class NonRetryableError(Exception):
    """Base class for failures that should never be retried by
    process_item_with_retry -- e.g. the target system has definitively
    rejected an item (not a transient/network hiccup), so retrying would
    just waste time and re-trigger the same rejection."""
    pass


class BrowserSessionError(NonRetryableError):
    """Raised when the Chrome/ChromeDriver session is unrecoverably dead
    (crashed window, invalid session handle, etc.).
    The caller should restart the driver and resume from the next item."""
    pass


class BrowserCrashError(BrowserSessionError):
    """Alias for BrowserSessionError, used when ChromeDriver itself crashes
    (vs. just a closed tab)."""
    pass


class UnsupportedFormatError(NonRetryableError):
    """Raised when the target system rejects a file as an unsupported
    format. This is a permanent outcome for that file -- it must be
    skipped, logged distinctly, and never retried (this run or any future
    resumed run)."""
    pass


class MalformedFileError(NonRetryableError):
    """Raised when the target system rejects a file via its 'The following
    files are malformed or has invalid file name: <name>' dialog. Like
    UnsupportedFormatError, this is a permanent, per-file rejection (the
    file itself is bad, not a transient/network hiccup) -- it must be
    dismissed (its 'Ok' button clicked), skipped, logged distinctly, and
    never retried (this run or any future resumed run)."""
    pass


SESSIONS_DIR = os.path.join(get_persistent_dir(), "sessions")


def ensure_sessions_dir() -> str:
    """Ensure the sessions directory exists."""
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    return SESSIONS_DIR


def check_network_connection(timeout: float = 3.0) -> bool:
    """Check if internet/network connection is currently active via DNS socket and HTTPS fallback."""
    for host, port in [("8.8.8.8", 53), ("1.1.1.1", 53), ("8.8.4.4", 53)]:
        try:
            socket.setdefaulttimeout(timeout)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((host, port))
            s.close()
            return True
        except Exception:
            pass

    # Fallback HTTP attempts
    for url in ["https://www.google.com", "https://1.1.1.1", "https://www.cloudflare.com"]:
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if response.status in (200, 301, 302):
                    return True
        except Exception:
            pass

    return False


def wait_for_network_connection(retry_wait_seconds: int = 5, abort_flag=None, log_func=None) -> bool:
    """Wait in a loop until network connection is restored or abort requested."""
    log = log_func or (lambda l, m: None)
    if check_network_connection():
        return True

    log("warning", "[NETWORK DISRUPTION] Internet connection lost or unstable. Pausing automation and waiting for connection to recover...")
    elapsed = 0
    poll_sec = max(1, retry_wait_seconds)
    while not check_network_connection():
        if abort_flag and abort_flag():
            log("warning", "[NETWORK DISRUPTION] Abort requested while waiting for network connection.")
            return False
        time.sleep(poll_sec)
        elapsed += poll_sec
        if elapsed % 30 == 0:
            log("info", f"[NETWORK DISRUPTION] Still waiting for internet connection to restore... (elapsed: {elapsed}s)")

    log("info", f"[NETWORK DISRUPTION] Internet connection restored after {elapsed}s! Resuming automation task...")
    time.sleep(1.5)  # allow socket stack & DNS cache to stabilize
    return True


class SessionManager:
    """Manages reading, writing, and clearing session states for automation tools.
    Supports thread-safe multi-worker parallel execution and progress aggregation.
    """

    _lock = threading.RLock()

    @staticmethod
    def get_session_file(module_id: str) -> str:
        ensure_sessions_dir()
        return os.path.join(SESSIONS_DIR, f"{module_id}_session.json")

    @classmethod
    def get_unfinished_session(cls, module_id: str) -> dict | None:
        """Return unfinished session dictionary if exists and status is in_progress, else None."""
        with cls._lock:
            session_file = cls.get_session_file(module_id)
            if not os.path.isfile(session_file):
                return None
            try:
                with open(session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("status") == "in_progress":
                    return data
            except Exception as e:
                _log.warning(f"Could not read session file {session_file}: {e}")
            return None

    @classmethod
    def start_session(cls, module_id: str, user_inputs: dict, total_items: int) -> dict:
        """Initialize or overwrite session state when a new automation run starts."""
        with cls._lock:
            existing = cls.get_unfinished_session(module_id)
            if existing and existing.get("user_inputs") == user_inputs:
                # Continue existing session
                return existing

            session_data = {
                "module_id": module_id,
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "in_progress",
                "user_inputs": user_inputs,
                "total_items": total_items,
                "completed_item_ids": [],
                "failed_item_ids": [],
            }
            cls._save(module_id, session_data)
            return session_data

    @classmethod
    def get_all_completed_item_ids(cls, base_module_id: str) -> set[str]:
        """Collect all completed item IDs across base_module_id and any worker sessions
        (e.g., base_module_id, base_module_id_w0, base_module_id_w1, ...).
        """
        with cls._lock:
            ensure_sessions_dir()
            completed = set()
            # 1. Base session
            base_sess = cls.get_unfinished_session(base_module_id)
            if base_sess:
                for it in base_sess.get("completed_item_ids", []):
                    completed.add(str(it).strip())
            # 2. Worker sessions (base_module_id_w0, etc.)
            prefix = f"{base_module_id}_w"
            for fname in os.listdir(SESSIONS_DIR):
                if fname.startswith(prefix) and fname.endswith("_session.json"):
                    fpath = os.path.join(SESSIONS_DIR, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if isinstance(data, dict):
                            for it in data.get("completed_item_ids", []):
                                completed.add(str(it).strip())
                    except Exception:
                        pass
            return completed

    @classmethod
    def update_item_status(cls, module_id: str, item_id: str, success: bool = True):
        """Record an item as completed or failed and save session.
        If called with a worker module ID (e.g. file_exporter_w0), automatically
        synchronizes the item status with the parent session as well.
        """
        with cls._lock:
            cls._update_single(module_id, item_id, success)
            if "_w" in module_id:
                base_module_id = module_id.split("_w")[0]
                cls._update_single(base_module_id, item_id, success)

    @classmethod
    def _update_single(cls, module_id: str, item_id: str, success: bool = True):
        session_file = cls.get_session_file(module_id)
        if not os.path.isfile(session_file):
            return
        try:
            with open(session_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            item_str = str(item_id).strip()
            completed = data.setdefault("completed_item_ids", [])
            failed = data.setdefault("failed_item_ids", [])

            if success:
                if item_str not in completed:
                    completed.append(item_str)
                if item_str in failed:
                    failed.remove(item_str)
            else:
                if item_str in completed:
                    completed.remove(item_str)
                if item_str not in failed:
                    failed.append(item_str)

            cls._save(module_id, data)
        except Exception as e:
            _log.warning(f"Could not update session item status for {module_id}: {e}")

    @classmethod
    def clear_session(cls, module_id: str):
        """Remove or mark session as completed, including any worker sessions if base module."""
        with cls._lock:
            session_file = cls.get_session_file(module_id)
            if os.path.isfile(session_file):
                try:
                    os.remove(session_file)
                except Exception as e:
                    _log.warning(f"Could not remove session file {session_file}: {e}")

            # Also clear worker sessions if clearing base module
            if "_w" not in module_id:
                ensure_sessions_dir()
                prefix = f"{module_id}_w"
                try:
                    for fname in os.listdir(SESSIONS_DIR):
                        if fname.startswith(prefix) and fname.endswith("_session.json"):
                            try:
                                os.remove(os.path.join(SESSIONS_DIR, fname))
                            except Exception:
                                pass
                except Exception:
                    pass

    @classmethod
    def _save(cls, module_id: str, data: dict):
        session_file = cls.get_session_file(module_id)
        try:
            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            _log.warning(f"Could not save session file {session_file}: {e}")


def process_item_with_retry(
    item_id: str,
    process_fn,
    module_id: str,
    log_func=None,
    abort_flag=None,
    max_retries: int | None = None,
    retry_wait_seconds: int | None = None,
    track_session: bool = True,
) -> tuple[bool, object, Exception | None]:
    """Run *process_fn* (a zero-arg callable) for a single CSV row / file / item,
    automatically retrying on failure so a transient hiccup (or a full internet
    black-out) doesn't silently skip the row and leave a gap in the assigned task.

    Behaviour:
      - If the failure coincides with the network being down, this waits
        (indefinitely, checking the abort flag) for the connection to come
        back and then retries the SAME item -- this does not consume one of
        the configured retry attempts, since it isn't really a "failure",
        just a delay.
      - Otherwise, the item is retried up to ``max_retries`` times (from
        System Settings, unless overridden here) with ``retry_wait_seconds``
        between attempts.
      - On final success or final failure, ``SessionManager.update_item_status``
        is called automatically so the session file always reflects the true
        state of every item that has been attempted.

    Returns ``(success, result, last_error)``. ``result`` is whatever
    *process_fn* returned on success (``None`` on failure).
    """
    from core.settings_manager import settings

    log = log_func or (lambda l, m: None)
    abort = abort_flag or (lambda: False)
    max_retries = settings.get("max_retries", 3) if max_retries is None else max_retries
    retry_wait_seconds = settings.get("retry_wait_seconds", 5) if retry_wait_seconds is None else retry_wait_seconds

    attempt = 0
    last_error: Exception | None = None

    while True:
        if abort():
            return False, None, last_error

        try:
            result = process_fn()
            if track_session:
                SessionManager.update_item_status(module_id, item_id, success=True)
            return True, result, None
        except NonRetryableError as e:
            # Definitive, permanent rejection (e.g. an unsupported file
            # format) -- retrying would just waste time reproducing the
            # same rejection. Fail immediately, once, no retry loop.
            last_error = e
            if track_session:
                SessionManager.update_item_status(module_id, item_id, success=False)
            return False, None, last_error
        except Exception as e:
            last_error = e

            # Dead browser session — not a transient hiccup, retrying on
            # the same dead driver will always fail. Escalate immediately.
            if is_dead_session_error(e):
                log("warning",
                    f"[BROWSER SESSION LOST] '{item_id}' — Chrome session is gone "
                    f"(crashed/closed). Skipping retry; caller should restart the driver.")
                last_error = BrowserSessionError(str(e))
                last_error.__cause__ = e
                if track_session:
                    SessionManager.update_item_status(module_id, item_id, success=False)
                return False, None, last_error

            if not check_network_connection():
                log("warning",
                    f"[NETWORK DISRUPTION] Lost connection while processing '{item_id}'. "
                    f"This item will be revisited automatically once the connection is back.")
                recovered = wait_for_network_connection(retry_wait_seconds, abort_flag=abort, log_func=log)
                if not recovered:
                    if track_session:
                        SessionManager.update_item_status(module_id, item_id, success=False)
                    return False, None, last_error
                # Network is back -- retry this same item without burning a retry attempt.
                continue

            attempt += 1
            if attempt > max_retries:
                if track_session:
                    SessionManager.update_item_status(module_id, item_id, success=False)
                return False, None, last_error

            log("warning",
                f"[RETRY {attempt}/{max_retries}] '{item_id}' failed "
                f"({describe_exception(e)}). "
                f"Retrying in {retry_wait_seconds}s...")
            for _ in range(int(max(1, retry_wait_seconds))):
                if abort():
                    if track_session:
                        SessionManager.update_item_status(module_id, item_id, success=False)
                    return False, None, last_error
                time.sleep(1)


session_manager = SessionManager()
