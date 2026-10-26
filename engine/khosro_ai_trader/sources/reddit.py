"""Reddit source: mention counting for candidate coins across hot posts.

NOTE: Reddit blocks many datacenter IPs (HTTP 403) — including GitHub Actions
runners — so this source is disabled by default in config.yaml and every
failure is swallowed gracefully. Enable it only if your runner can reach it.
"""

from __future__ import annotations

import re
from typing import Any

from .base import BaseSource

BASE_URL = "https://www.reddit.com"


class RedditSource(BaseSource):
    name = "reddit"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.session.headers["User-Agent"] = (
            "script:KhosroAiTrader:0.1 (research; contact @khosro)"
        )

    # -- public API ---------------------------------------------------------

    def fetch_mentions(self, candidates: list[dict[str, Any]]) -> dict[str, int]:
        """Count mentions of each candidate in hot posts of configured subreddits.

        candidates: [{symbol: "SOL", name: "Solana"}, ...]
        Returns {SYMBOL: mention_count}.
        """
        subs = self.cfg.sources.reddit_subreddits
        limit = self.cfg.sources.reddit_posts_limit
        posts: list[dict[str, Any]] = []
        for sub in subs:
            try:
                data = self.get_json(
                    f"{BASE_URL}/r/{sub}/hot.json",
                    params={"limit": limit, "raw_json": 1},
                )
                children = ((data.get("data") or {}).get("children")) or []
                posts.extend(c.get("data") or {} for c in children)
                self.log.info("r/%s: %s posts fetched", sub, len(children))
            except Exception as exc:  # noqa: BLE001 — graceful by design
                self.log.warning("r/%s unreachable: %s", sub, exc)

        if not posts:
            raise RuntimeError("no reddit posts could be fetched")

        return self._count_mentions(posts, candidates)

    # -- internals ----------------------------------------------------------

    def _count_mentions(
        self, posts: list[dict[str, Any]], candidates: list[dict[str, Any]]
    ) -> dict[str, int]:
        counts: dict[str, int] = {c["symbol"]: 0 for c in candidates}
        patterns: dict[str, re.Pattern[str]] = {}
        for cand in candidates:
            sym = cand["symbol"]
            parts = [rf"\${re.escape(sym)}\b"]
            if len(sym) >= 3:
                parts.append(rf"\b{re.escape(sym.lower())}\b")
            name = (cand.get("name") or "").strip().lower()
            if len(name) >= 4 and " " not in name:
                parts.append(rf"\b{re.escape(name)}\b")
            patterns[sym] = re.compile("|".join(parts), re.IGNORECASE)

        for post in posts:
            text = f"{post.get('title', '')} {post.get('selftext', '')[:1000]}"
            if not text.strip():
                continue
            for sym, pattern in patterns.items():
                if pattern.search(text):
                    counts[sym] += 1
        return counts
