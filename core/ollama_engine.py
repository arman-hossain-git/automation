"""
core/ollama_engine.py
=====================
Robust local Ollama AI client for privacy-compliant tabular data extraction,
adaptive schema harmonization, and autonomous selector self-healing.

Runs strictly on localhost (default: http://localhost:11434).
Zero external cloud network calls (FERPA / institutional privacy compliant).
"""

import os
import json
import logging
import re
from typing import Dict, Any, List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5:7b"
FALLBACK_MODELS = ["qwen2.5:7b", "llama3.1:8b", "mistral:7b", "phi3:mini"]


class OllamaEngine:
    """Singleton/Instance client managing local Ollama interactions."""

    def __init__(self, host: Optional[str] = None, model: Optional[str] = None):
        from core.settings_manager import settings
        self.host = host or settings.get("ollama_host", DEFAULT_OLLAMA_HOST).rstrip("/")
        self.model = model or settings.get("ollama_model", DEFAULT_MODEL)
        self._cached_available: Optional[bool] = None
        self._last_check_time: float = 0.0

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

    def warm_up_async(self, model: Optional[str] = None) -> None:
        """Pre-loads the model into GPU/RAM in a background thread so first inference is instant."""
        import threading
        target_model = model or self.model

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
        target_model = model or self.model
        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "format": "json",
            "stream": False,
            "keep_alive": "15m",
            "options": {
                "temperature": 0.0,  # Zero randomness for high determinism
                "num_predict": max_tokens,
            },
        }

        if schema:
            payload["format"] = schema

        try:
            resp = requests.post(
                f"{self.host}/api/chat",
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            res_json = resp.json()
            content = res_json.get("message", {}).get("content", "").strip()

            # Clean markdown code blocks if the model wrapped output
            if content.startswith("```"):
                content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
                content = re.sub(r"\n?```$", "", content).strip()

            return json.loads(content)
        except requests.exceptions.RequestException as e:
            logger.warning(f"Ollama request error connecting to {self.host}: {e}")
            raise ConnectionError(f"Cannot reach local Ollama at {self.host}. Ensure Ollama is running.") from e
        except json.JSONDecodeError as e:
            logger.warning(f"Ollama returned non-JSON payload: {content[:200]}...")
            raise ValueError(f"Ollama returned malformed JSON: {e}") from e


# Global shared instance
ollama_engine = OllamaEngine()
