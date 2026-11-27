"""
core/ollama_engine.py
=====================
Robust local Ollama AI client for privacy-compliant tabular data extraction,
adaptive schema harmonization, and autonomous selector self-healing.

Runs strictly on localhost (default: http://localhost:11434).
Zero external cloud network calls (FERPA / institutional privacy compliant).
"""

import os
import sys
import json
import logging
import re
import shutil
import subprocess
import time
from typing import Dict, Any, List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5:7b"
FALLBACK_MODELS = ["qwen2.5:7b", "qwen2.5:3b", "qwen2.5:1.5b", "llama3.1:8b", "mistral:7b", "phi3:mini"]


class OllamaEngine:
    """Singleton/Instance client managing local Ollama interactions."""

    def __init__(self, host: Optional[str] = None, model: Optional[str] = None):
        from core.settings_manager import settings
        self.host = host or settings.get("ollama_host", DEFAULT_OLLAMA_HOST).rstrip("/")
        self.model = model or settings.get("ollama_model", DEFAULT_MODEL)
        self._cached_available: Optional[bool] = None
        self._last_check_time: float = 0.0

    def _get_bundled_ollama_paths(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Checks for a project-bundled portable Ollama executable and its models directory.
        Returns (exe_path, models_dir) or (None, None) if not bundled.
        """
        exe_name = "ollama.exe" if sys.platform == "win32" else "ollama"
        here = os.path.dirname(os.path.abspath(__file__))
        candidates = []

        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, "vendor", "ollama"))

        # unified_dashboard/vendor/ollama and repo_root/vendor/ollama
        candidates.append(os.path.abspath(os.path.join(here, "..", "vendor", "ollama")))
        candidates.append(os.path.abspath(os.path.join(here, "..", "..", "vendor", "ollama")))
        candidates.append(os.path.abspath(os.path.join(os.getcwd(), "vendor", "ollama")))
        candidates.append(os.path.abspath(os.path.join(os.getcwd(), "..", "vendor", "ollama")))

        for base_dir in candidates:
            exe_path = os.path.join(base_dir, exe_name)
            if os.path.isfile(exe_path):
                models_dir = os.path.join(base_dir, "models")
                os.makedirs(models_dir, exist_ok=True)
                return exe_path, models_dir

        return None, None

    def _find_ollama_executable(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Locates Ollama executable, preferring project-bundled portable Ollama first,
        then falling back to system PATH / standard install locations.
        """
        bundled_exe, bundled_models = self._get_bundled_ollama_paths()
        if bundled_exe:
            return bundled_exe, bundled_models

        ollama_exe = shutil.which("ollama")
        if not ollama_exe and sys.platform == "win32":
            candidate = os.path.join(
                os.environ.get("LOCALAPPDATA", ""),
                "Programs", "Ollama", "ollama.exe"
            )
            if os.path.isfile(candidate):
                ollama_exe = candidate

        return ollama_exe, None

    def has_executable(self) -> bool:
        """Returns True if Ollama is already running OR a bundled/system binary is available."""
        if self.is_available(timeout=0.8):
            return True
        exe, _ = self._find_ollama_executable()
        return exe is not None

    def is_available(self, timeout: float = 2.0) -> bool:
        """Checks if the local Ollama daemon is reachable."""
        try:
            resp = requests.get(f"{self.host}/api/version", timeout=timeout)
            if resp.status_code == 200:
                self._cached_available = True
                return True
        except Exception:
            pass
        self._cached_available = False
        return False

    def ensure_running(self, wait_seconds: float = 10.0) -> bool:
        """Returns True if Ollama is running; if not, launches bundled or system `ollama serve`."""
        if self.is_available(timeout=1.5):
            return True

        ollama_exe, bundled_models_dir = self._find_ollama_executable()
        if not ollama_exe:
            return False

        try:
            env = os.environ.copy()
            if bundled_models_dir:
                env["OLLAMA_MODELS"] = bundled_models_dir
            # Strip scheme for OLLAMA_HOST (e.g. "127.0.0.1:11434")
            host_no_scheme = re.sub(r"^https?://", "", self.host).strip("/")
            if host_no_scheme:
                env["OLLAMA_HOST"] = host_no_scheme

            kwargs: Dict[str, Any] = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
                "env": env,
                "cwd": os.path.dirname(ollama_exe),
            }
            if sys.platform == "win32":
                kwargs["creationflags"] = (
                    getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                    | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                )
            else:
                kwargs["start_new_session"] = True

            subprocess.Popen([ollama_exe, "serve"], **kwargs)

            deadline = time.monotonic() + wait_seconds
            while time.monotonic() < deadline:
                if self.is_available(timeout=0.5):
                    return True
                time.sleep(0.3)
        except Exception as e:
            logger.debug(f"Could not auto-start Ollama: {e}")

        return self.is_available(timeout=1.0)

    def get_version(self) -> Optional[str]:
        """Returns the version string of the local Ollama server."""
        try:
            resp = requests.get(f"{self.host}/api/version", timeout=2.0)
            if resp.status_code == 200:
                return resp.json().get("version", "Unknown")
        except Exception:
            pass
        return None

    def list_installed_models(self) -> List[str]:
        """Returns a list of model tags currently pulled in Ollama."""
        try:
            resp = requests.get(f"{self.host}/api/tags", timeout=3.0)
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                return [m.get("name", "") for m in models if m.get("name")]
        except Exception as e:
            logger.debug(f"Failed to query Ollama tags: {e}")
        return []

    def is_model_installed(self, model_name: Optional[str] = None) -> bool:
        """Checks if the target model is installed locally."""
        target = (model_name or self.model).lower()
        target_base = target.split(":")[0]
        installed = [m.lower() for m in self.list_installed_models()]
        return any(target in m or target_base == m.split(":")[0] for m in installed)

    def pull_model(self, model_name: Optional[str] = None, stream_callback=None) -> bool:
        """Pulls a model into Ollama."""
        target = model_name or self.model
        try:
            resp = requests.post(
                f"{self.host}/api/pull",
                json={"name": target, "stream": stream_callback is not None},
                stream=stream_callback is not None,
                timeout=600,
            )
            if stream_callback and resp.status_code == 200:
                for line in resp.iter_lines():
                    if line:
                        try:
                            data = json.loads(line.decode("utf-8"))
                            stream_callback(data)
                        except Exception:
                            pass
                return True
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Failed to pull model '{target}': {e}")
            return False

    def resolve_available_model(self, preferred: Optional[str] = None) -> str:
        """
        Returns `preferred` (or `self.model`) if installed; otherwise falls back to
        the first installed model matching FALLBACK_MODELS or any installed model.
        """
        target = preferred or self.model
        installed = self.list_installed_models()
        if not installed:
            return target

        target_lower = target.lower()
        target_base = target_lower.split(":")[0]
        for m in installed:
            if m.lower() == target_lower or m.lower().split(":")[0] == target_base:
                return m

        for fb in FALLBACK_MODELS:
            fb_base = fb.lower().split(":")[0]
            for m in installed:
                if m.lower() == fb.lower() or m.lower().split(":")[0] == fb_base:
                    return m

        return installed[0]

    def warm_up_async(self, model: Optional[str] = None) -> None:
        """Pre-loads the model into GPU/RAM in a background thread so first inference is instant."""
        import threading
        target_model = self.resolve_available_model(model)

        def _bg():
            try:
                requests.post(
                    f"{self.host}/api/generate",
                    json={"model": target_model, "keep_alive": "15m"},
                    timeout=90,
                )
            except Exception:
                pass

        threading.Thread(target=_bg, daemon=True).start()

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        timeout: int = 120,
        max_tokens: int = 1024,
    ) -> Dict[str, Any]:
        """
        Executes a prompt against local Ollama, forcing JSON formatting and zero temperature.
        Returns a parsed Python dictionary or list.
        """
        target_model = self.resolve_available_model(model)
        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "format": "json",
            "stream": True,
            "keep_alive": "15m",
            "options": {
                "temperature": 0.0,  # Zero randomness for high determinism
                "num_predict": max_tokens,
            },
        }

        if schema:
            payload["format"] = schema

        last_err: Optional[Exception] = None
        content = ""
        for attempt in range(2):
            try:
                if attempt == 0 and not getattr(self, "_warmed_model", None) == target_model:
                    # Pre-load model so cold-start (~11s) doesn't trigger Ollama's ~15s HTTP idle reset
                    try:
                        requests.post(
                            f"{self.host}/api/generate",
                            json={"model": target_model, "keep_alive": "15m"},
                            headers={"Connection": "close"},
                            timeout=60,
                        )
                        self._warmed_model = target_model
                    except Exception:
                        pass

                resp = requests.post(
                    f"{self.host}/api/chat",
                    json=payload,
                    headers={"Connection": "close"},
                    stream=True,
                    timeout=timeout,
                )
                resp.raise_for_status()

                chunks: List[str] = []
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line.decode("utf-8"))
                        part = chunk.get("message", {}).get("content", "")
                        if part:
                            chunks.append(part)
                    except Exception:
                        continue

                content = "".join(chunks).strip()

                # Clean markdown code blocks if the model wrapped output
                if content.startswith("```"):
                    content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
                    content = re.sub(r"\n?```$", "", content).strip()

                return json.loads(content)
            except requests.exceptions.RequestException as e:
                last_err = e
                if attempt == 0:
                    # Recover from cold-start socket reset (WinError 10054) or stopped daemon
                    time.sleep(0.5)
                    self.ensure_running(wait_seconds=6.0)
                    try:
                        requests.post(
                            f"{self.host}/api/generate",
                            json={"model": target_model, "keep_alive": "15m"},
                            headers={"Connection": "close"},
                            timeout=60,
                        )
                        self._warmed_model = target_model
                    except Exception:
                        pass
                    continue
                logger.warning(f"Ollama request error connecting to {self.host}: {e}")
                raise ConnectionError(f"Cannot reach local Ollama at {self.host}. Ensure Ollama is running.") from e
            except json.JSONDecodeError as e:
                logger.warning(f"Ollama returned non-JSON payload: {content[:200]}...")
                raise ValueError(f"Ollama returned malformed JSON: {e}") from e

        raise ConnectionError(f"Cannot reach local Ollama at {self.host}: {last_err}")


# Global shared instance
ollama_engine = OllamaEngine()
