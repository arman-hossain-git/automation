"""
core/settings_manager.py
========================
Manages persistent application settings saved to settings.json.
"""

import os
import json

from core.paths import get_persistent_dir

SETTINGS_FILE = os.path.join(get_persistent_dir(), "settings.json")

DEFAULT_SETTINGS = {
    "headless": False,
    "auto_open_devtools": False,
    "download_timeout": 30,
    # Ceiling (seconds) for any single dynamic wait/action (element to appear,
    # become clickable, page to finish loading, etc). This is a MAXIMUM, not a
    # fixed delay -- every wait already returns the instant its condition is
    # met, so real runs are almost always faster than this. Raise it only if
    # a slow site/network needs more room; lower it to fail fast sooner.
    "max_action_wait_seconds": 20,
    "max_retries": 3,
    "retry_wait_seconds": 5,
    "log_level": "INFO",
    "theme_accent": "#4f9cf9",
    "default_download_dir": "",
    "auto_clear_logs_on_start": False,
    "confirm_on_abort": True,
    # When True, if a site has confirmed (from past run history) that it only
    # works with 'Auto-open DevTools' checked, the automation applies that
    # automatically instead of failing again because the box was left unchecked.
    "auto_apply_learned_devtools": True,
    # Waiting time / Inactivity timeout (minutes) between two tasks in queue
    "inter_task_timeout_minutes": 15,
    # Inactivity action after timeout:
    # 'close_app'        -> Safely stop automation and exit application
    # 'pause_queue'      -> Pause automation and wait for user to resume
    # 'proceed_next'     -> Automatically continue to next queued task
    # 'stop_automation'  -> Stop automation and keep program open
    "inter_task_inactivity_action": "close_app",
    # Color Scheme / Theme mode: 'system' (Same as local PC), 'dark', or 'light'
    "theme": "system",
}


class SettingsManager:
    """Load, store, and save application settings."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._data = dict(DEFAULT_SETTINGS)
            cls._instance.load()
        return cls._instance

    def load(self) -> dict:
        if os.path.isfile(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    self._data.update(saved)
            except Exception:
                pass
        return self._data

    def save(self) -> bool:
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            return True
        except Exception:
            return False

    def get(self, key: str, default=None):
        return self._data.get(key, default if default is not None else DEFAULT_SETTINGS.get(key))

    def set(self, key: str, value):
        self._data[key] = value

    def reset_to_defaults(self):
        self._data = dict(DEFAULT_SETTINGS)
        self.save()


settings = SettingsManager()
