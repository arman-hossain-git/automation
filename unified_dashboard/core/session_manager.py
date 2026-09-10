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

from core.paths import get_persistent_dir

_log = logging.getLogger(__name__)


class NonRetryableError(Exception):
    """Base class for failures that should never be retried by
    process_item_with_retry -- e.g. the target system has definitively
    rejected an item (not a transient/network hiccup), so retrying would
    just waste time and re-trigger the same rejection."""
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
    """Manages reading, writing, and clearing session states for automation tools."""

    @staticmethod
    def get_session_file(module_id: str) -> str:
        ensure_sessions_dir()
        return os.path.join(SESSIONS_DIR, f"{module_id}_session.json")

    @classmethod
    def get_unfinished_session(cls, module_id: str) -> dict | None:
        """Return unfinished session dictionary if exists and status is in_progress, else None."""
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
    def update_item_status(cls, module_id: str, item_id: str, success: bool = True):
        """Record an item as completed or failed and save session."""
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
                if item_str not in failed and item_str not in completed:
                    failed.append(item_str)

            cls._save(module_id, data)
        except Exception as e:
            _log.warning(f"Could not update session item status for {module_id}: {e}")

    @classmethod
    def clear_session(cls, module_id: str):
        """Remove or mark session as completed."""
        session_file = cls.get_session_file(module_id)
        if os.path.isfile(session_file):
            try:
                os.remove(session_file)
            except Exception as e:
                _log.warning(f"Could not remove session file {session_file}: {e}")

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
                f"[RETRY {attempt}/{max_retries}] '{item_id}' failed ({e}). "
                f"Retrying in {retry_wait_seconds}s...")
            for _ in range(int(max(1, retry_wait_seconds))):
                if abort():
                    if track_session:
                        SessionManager.update_item_status(module_id, item_id, success=False)
                    return False, None, last_error
                time.sleep(1)


session_manager = SessionManager()
