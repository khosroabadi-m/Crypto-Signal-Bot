"""Base class for all data sources: shared HTTP session with retries/backoff."""

from __future__ import annotations

import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..config import Config
from ..logger import get_logger


class SourceError(RuntimeError):
    """Raised when a source permanently fails after retries."""


class BaseSource:
    """Common plumbing for HTTP-based sources."""

    name: str = "base"

    def __init__(self, config: Config) -> None:
        self.cfg = config
        self.log = get_logger(f"source.{self.name}")
        self.session = requests.Session()
        retry = Retry(
            total=config.run.max_retries,
            connect=config.run.max_retries,
            read=config.run.max_retries,
            backoff_factor=2.0,               # 2s, 4s, 8s ...
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update(
            {
                "User-Agent": config.run.user_agent,
                "Accept": "application/json",
            }
        )
        self.timeout = config.run.request_timeout

    def get_json(self, url: str, params: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None,
                 retries: int | None = None) -> Any:
        """GET with retry/backoff; raises SourceError after final failure.

        `retries` overrides the global retry count — used by optional
        enrichment sources so a blocked endpoint never slows a run much.
        """
        max_retries = self.cfg.run.max_retries if retries is None else retries
        last_err: Exception | None = None
        for attempt in range(1, max_retries + 2):
            try:
                resp = self.session.get(
                    url, params=params, timeout=self.timeout, headers=headers
                )
                if resp.status_code == 200:
                    return resp.json()
                last_err = SourceError(f"HTTP {resp.status_code} from {url}")
                # 451/403 = مسدود جغرافیایی/قانونی — retry بی‌فایده و فقط لاگ را شلوغ می‌کند
                if resp.status_code in (451, 403):
                    self.log.debug("HTTP %s on %s — skip retries", resp.status_code, url)
                    break
                self.log.warning("HTTP %s on %s (attempt %s)", resp.status_code, url, attempt)
            except requests.RequestException as exc:
                last_err = exc
                self.log.warning("Request error on %s (attempt %s): %s", url, attempt, exc)
            if attempt <= max_retries:
                sleep_s = min(2 ** attempt, 20)
                time.sleep(sleep_s)
        raise SourceError(str(last_err))
