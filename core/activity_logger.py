"""
core/activity_logger.py
========================
Thread-safe text log writer that mirrors the Live Activity Console output
line-for-line.

Every line written to the UI console via BaseToolWidget.log() is also
appended to a timestamped .log file in the same folder as the run's
execution-results CSV (or a configurable output directory).

Log format (identical to what the console renders):
    [HH:MM:SS] [LEVEL]   message text

One file per run, named:
    activity_YYYYMMDD_HHMMSS.log

Usage (managed automatically by BaseToolWidget):
    al = ActivityLogger(output_dir, run_id)
    al.write("INFO",  "Starting automation…")
    al.write("ERROR", "Student 10002464 failed: …")
    al.close()   # flush + close at end of run
"""

import os
import threading
import datetime


class ActivityLogger:
    """Writes console-mirror log lines to a UTF-8 text file, thread-safely."""

    def __init__(self, output_dir: str, run_id: str = "") -> None:
        """
        Parameters
        ----------
        output_dir:
            Directory where the log file will be created.  The directory is
            created automatically if it does not already exist.
        run_id:
            An optional timestamp string (e.g. ``'20261014_115529'``) baked
            into the filename so each run gets its own file.  When omitted a
            fresh timestamp is generated.
        """
        self._lock = threading.Lock()
        self._fh = None

        if not run_id:
            run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        try:
            os.makedirs(output_dir, exist_ok=True)
            log_path = os.path.join(output_dir, f"activity_{run_id}.log")
            self._fh = open(log_path, "a", encoding="utf-8", buffering=1)  # line-buffered
            self._path = log_path
            # Write a session header so the file is self-contained
            started = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._fh.write(
                f"# ── Automation Activity Log ─────────────────────────────────────\n"
                f"# Started : {started}\n"
                f"# Log file: {log_path}\n"
                f"# ─────────────────────────────────────────────────────────────────\n"
            )
        except Exception:
            # If we cannot open the file (permissions, bad path, etc.)
            # we silently degrade rather than crashing the UI.
            self._fh = None
            self._path = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def path(self) -> str:
        """Absolute path of the log file being written (empty if failed)."""
        return self._path

    def write(self, level: str, message: str) -> None:
        """Append one log line in the same format as the Live Activity Console.

        ``[HH:MM:SS] [LEVEL]   message``
        """
        if not self._fh:
            return
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] [{level.upper():7s}] {message}\n"
        with self._lock:
            try:
                self._fh.write(line)
            except Exception:
                pass  # never raise from inside a UI callback

    def close(self) -> None:
        """Flush and close the log file.  Safe to call multiple times."""
        if not self._fh:
            return
        with self._lock:
            try:
                ended = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._fh.write(
                    f"# ─────────────────────────────────────────────────────────────────\n"
                    f"# Ended : {ended}\n"
                )
                self._fh.flush()
                self._fh.close()
            except Exception:
                pass
            finally:
                self._fh = None
