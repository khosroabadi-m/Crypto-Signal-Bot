"""OpenAI-compatible chat-completions client (Z.ai GLM, OpenAI, any compatible)."""

from __future__ import annotations

import os
import time
from typing import Any

import requests

from ..config import Config
from ..logger import get_logger


class LLMError(RuntimeError):
    """Raised when the LLM API permanently fails."""


class OpenAICompatibleClient:
    """Minimal chat client against {base_url}/chat/completions with retries."""

    def __init__(self, config: Config) -> None:
        self.cfg = config.ai
        self.log = get_logger("ai.client")

    def chat(self, system: str, user: str) -> str:
        url = f"{self.cfg.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_output_tokens,
        }

        last_err: Exception | None = None
        for attempt in range(1, 4):
            try:
                resp = requests.post(url, json=payload, headers=headers,
                                     timeout=self.cfg.timeout)
                if resp.status_code == 200:
                    content = (resp.json().get("choices") or [{}])[0] \
                        .get("message", {}).get("content", "")
                    if not content.strip():
                        raise LLMError("empty completion content")
                    return content
                last_err = LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                self.log.warning("LLM HTTP %s (attempt %s)", resp.status_code, attempt)
            except requests.RequestException as exc:
                last_err = exc
                self.log.warning("LLM request error (attempt %s): %s", attempt, exc)
            if attempt < 3:
                time.sleep(min(2 ** attempt * 2, 15))
        raise LLMError(str(last_err))


def _api_key() -> str:
    """API key from env (AI_API_KEY, or ZAI_API_KEY alias)."""
    return os.getenv("AI_API_KEY", "").strip() or os.getenv("ZAI_API_KEY", "").strip()
