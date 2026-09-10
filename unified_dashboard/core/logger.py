"""
core/logger.py
==============
Shared logging setup used by all automation modules.

Provides:
  - setup_file_logger()    – creates a file-based logger
  - TkTextHandler          – a logging.Handler that forwards records to a
                             Tkinter ScrolledText or CTkTextbox widget
"""

import logging
import threading


def setup_file_logger(
    name: str,
    log_path: str,
    mode: str = 'a',
) -> logging.Logger:
    """Create (or reconfigure) a named logger that writes to *log_path*.

    Calling this multiple times with the same *name* clears existing handlers
    first so that log records are not duplicated across runs.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fh = logging.FileHandler(log_path, mode=mode, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    ))
    logger.addHandler(fh)
    return logger


class TkTextHandler(logging.Handler):
    """A logging.Handler that appends formatted records to a Tkinter text
    widget.

    All widget mutations are marshalled back to the Tk main thread via
    ``root.after(0, ...)`` so this handler is safe to call from background
    automation threads.

    The widget must support ``configure(state=...)``, ``insert(index, text,
    tag)``, and ``see(index)`` — compatible with both ``tkinter.scrolledtext.
    ScrolledText`` and ``customtkinter.CTkTextbox``.

    Tags configured on the widget:
      INFO    → cyan-ish
      WARNING → yellow
      ERROR   → red/salmon
      DEBUG   → grey
    """

    def __init__(self, text_widget, root):
        super().__init__()
        self.text_widget = text_widget
        self.root = root

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            return
        level = record.levelname

        def _append() -> None:
            try:
                self.text_widget.configure(state='normal')
                self.text_widget.insert('end', msg + '\n', level)
                self.text_widget.see('end')
                self.text_widget.configure(state='disabled')
            except Exception:
                pass

        try:
            self.root.after(0, _append)
        except Exception:
            pass


def attach_tk_handler(
    logger: logging.Logger,
    text_widget,
    root,
    level: int = logging.DEBUG,
    fmt: str = '%(asctime)s  %(levelname)-7s  %(message)s',
    datefmt: str = '%H:%M:%S',
) -> TkTextHandler:
    """Attach a ``TkTextHandler`` to *logger* and return it."""
    handler = TkTextHandler(text_widget, root)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    logger.addHandler(handler)
    return handler
