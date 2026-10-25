"""AI committee for the Khosro Confluence Engine.

Each enabled provider with a valid API key votes independently.
Votes are combined by weight into a 0..1 score + majority approval.

Per-provider outcomes are logged and returned so Telegram / CSV can show
exactly what each API decided. Providers and their weights live in
config.AI_PROVIDERS (weights must sum to 100).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone

import aiohttp

from config import (
    AI_PROVIDERS, AI_SYSTEM_PROMPT, AI_TEMPERATURE, AI_MAX_TOKENS,
    AI_TIMEOUT_SECONDS, AI_MAX_CALLS_PER_RUN, AI_DAILY_BUDGET,
    AI_MIN_COMMITTEE_SCORE, AI_REJECT_DECAY,
)

logger = logging.getLogger(__name__)

_run_calls = 0


def reset_budget():
    global _run_calls
    _run_calls = 0


def _budget_path():
    from signal_store import SIGNALS_DIR, tehran_date_str
    os.makedirs(SIGNALS_DIR, exist_ok=True)
    return os.path.join(SIGNALS_DIR, f"ai_budget_{tehran_date_str()}.json")


def _daily_count() -> int:
    path = _budget_path()
    try:
        with open(path, encoding="utf-8") as f:
            return int(json.load(f).get("count", 0))
    except Exception:
        return 0


def _bump_daily(n: int = 1):
    path = _budget_path()
    data = {"date": datetime.now(timezone.utc).isoformat(), "count": _daily_count() + n}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def budget_left() -> int:
    return max(0, min(AI_MAX_CALLS_PER_RUN - _run_calls,
                      AI_DAILY_BUDGET - _daily_count()))


def providers_with_keys():
    """Return enabled providers that currently have an API key in the environment."""
    ready = []
    for p in AI_PROVIDERS:
        if not p.get("enabled", True):
            continue
        key = os.getenv(p["key_env"], "").strip()
        if not key:
            continue
        extra = p.get("extra_env") or {}
        missing_extra = [env for env in extra.values() if not os.getenv(env, "").strip()]
        if missing_extra:
            continue
        ready.append(p)
    return ready


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    matches = re.findall(r"\{[^{}]*\}", cleaned, re.S)
    for candidate in reversed(matches):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict) and "decision" in data:
                return data
        except Exception:
            continue
    return None


def _normalize_vote(data: dict) -> tuple[bool, int] | None:
    decision = str(data.get("decision", "")).strip().lower()
    try:
        conf = float(data.get("confidence", 50))
    except (TypeError, ValueError):
        return None
    conf = max(0.0, min(100.0, conf))
    if decision.startswith("app") or decision in ("yes", "long", "buy", "approve"):
        return True, conf
    if decision.startswith("rej") or decision in ("no", "short", "sell", "reject", "avoid"):
        return False, conf
    return None


def build_user_prompt(candidate: dict) -> str:
    lines = [
        f"Symbol: {candidate.get('symbol')}",
        f"Direction: {candidate.get('direction')}",
        f"Rule score (0-100, AI excluded): {candidate.get('rule_score')}",
        f"Entry: {candidate.get('price')}  SL: {candidate.get('stop_loss')}  TP: {candidate.get('take_profit')}",
        f"R:R: {candidate.get('rr')}  ATR%: {candidate.get('atr_pct')}",
        f"Trader rules: {candidate.get('custom_rules', '')}",
        "Components:",
    ]
    for c in candidate.get("components") or []:
        if c.get("name") == "ai_committee":
            continue
        lines.append(f"  - {c.get('name')}: score={c.get('score')} detail={c.get('detail')}")
    lines.append(
        'Reply ONLY JSON: {"decision":"approve"|"reject","confidence":0-100,"reason":"short reason"}'
    )
    return "\n".join(lines)


def _resolve_url(provider: dict) -> str | None:
    if provider.get("url"):
        return provider["url"]
    template = provider.get("url_template")
    if not template:
        return None
    extra = provider.get("extra_env") or {}
    mapping = {}
    for key, env_name in extra.items():
        val = os.getenv(env_name, "").strip()
        if not val:
            return None
        mapping[key] = val
    try:
        return template.format(**mapping)
    except Exception:
        return None


async def _call_provider(session: aiohttp.ClientSession, provider: dict,
                         user_prompt: str) -> dict:
    name = provider["name"]
    api_key = os.getenv(provider["key_env"], "").strip()
    if not api_key:
        return {"ok": False, "error": f"missing {provider['key_env']}", "provider": name}

    url = _resolve_url(provider)
    if not url:
        return {"ok": False, "error": "missing url/account_id", "provider": name}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if name == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/khosro-signal-bot"
        headers["X-Title"] = "KhosroSignalAnalyzerBot"

    models = [provider["model"]] + list(provider.get("fallback_models") or [])
    attempts_per_model = 2 if provider.get("retry_429") else 1
    last_error = "unknown"

    for model in models:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": AI_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": AI_TEMPERATURE,
            "max_tokens": AI_MAX_TOKENS,
        }
        for attempt in range(attempts_per_model):
            try:
                async with session.post(
                    url, headers=headers, json=payload,
                    timeout=aiohttp.ClientTimeout(total=AI_TIMEOUT_SECONDS),
                ) as resp:
                    body_text = await resp.text()
                    if resp.status == 429 and attempt + 1 < attempts_per_model:
                        await asyncio.sleep(3.0)
                        continue
                    if resp.status != 200:
                        last_error = f"{model}: HTTP {resp.status}: {body_text[:120]}"
                        break
                    try:
                        data = json.loads(body_text)
                    except Exception:
                        last_error = f"{model}: invalid JSON body"
                        break
                    choice = (data.get("choices") or [{}])[0]
                    message = choice.get("message") or {}
                    content = (
                        message.get("content")
                        or message.get("reasoning")
                        or message.get("reasoning_content")
                        or ""
                    )
                    vote = _extract_json(content)
                    if vote is None:
                        last_error = f"{model}: unparseable reply"
                        break
                    norm = _normalize_vote(vote)
                    if norm is None:
                        last_error = f"{model}: bad decision field"
                        break
                    approved, conf = norm
                    reason = str(vote.get("reason", ""))[:80]
                    logger.info(
                        "AI_VOTE provider=%s model=%s decision=%s conf=%.0f reason=%s",
                        name, model, "approve" if approved else "reject", conf, reason,
                    )
                    return {
                        "ok": True,
                        "provider": name,
                        "model": model,
                        "approved": approved,
                        "confidence": conf,
                        "reason": reason,
                    }
            except Exception as exc:
                last_error = f"{model}: {type(exc).__name__}: {str(exc)[:90]}"
                if attempt + 1 < attempts_per_model:
                    await asyncio.sleep(1.5)
    logger.warning("AI_VOTE provider=%s FAIL %s", name, last_error)
    return {"ok": False, "error": last_error, "provider": name}


def format_votes_report(votes: list) -> str:
    """Human-readable multi-line report of every provider outcome."""
    if not votes:
        return "no votes"
    lines = []
    for v in votes:
        name = v.get("provider", "?")
        if v.get("ok"):
            dec = "APPROVE" if v.get("approved") else "REJECT"
            lines.append(
                f"{name}: {dec} {float(v.get('confidence', 0)):.0f}% — {v.get('reason', '')[:50]}"
            )
        else:
            lines.append(f"{name}: FAIL — {v.get('error', '?')[:60]}")
    return " | ".join(lines)


async def consult_committee(candidate: dict,
                            session: aiohttp.ClientSession | None = None) -> dict:
    """Ask all ready providers to vote. Returns available/approved/score/detail/votes/report."""
    global _run_calls
    ready = providers_with_keys()
    if not ready:
        detail = "AI unavailable: no API keys configured"
        logger.warning(detail)
        return {
            "available": False, "approved": None, "score": 0.5,
            "detail": detail, "votes": [], "report": detail,
        }
    if budget_left() <= 0:
        detail = "AI budget exhausted (neutral)"
        logger.warning(detail)
        return {
            "available": False, "approved": None, "score": 0.5,
            "detail": detail, "votes": [], "report": detail,
        }

    _run_calls += 1
    _bump_daily()
    user_prompt = build_user_prompt(candidate)
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    votes = []
    try:
        logger.info(
            "AI_COMMITTEE start symbol=%s dir=%s providers=%s",
            candidate.get("symbol"), candidate.get("direction"),
            [p["name"] for p in ready],
        )
        results = await asyncio.gather(
            *[_call_provider(session, p, user_prompt) for p in ready]
        )
        for provider, res in zip(ready, results):
            votes.append({"weight": provider["weight"], **res})
    finally:
        if own_session:
            await session.close()

    report = format_votes_report(votes)
    ok_votes = [v for v in votes if v.get("ok")]
    if not ok_votes:
        detail = "AI unavailable: all providers failed | " + report
        logger.warning("AI_COMMITTEE %s", detail[:200])
        return {
            "available": False, "approved": None, "score": 0.5,
            "detail": detail[:240], "votes": votes, "report": report,
        }

    total_w = sum(v["weight"] for v in ok_votes) or 1
    combined = 0.0
    for v in ok_votes:
        v_score = (v["confidence"] / 100.0) if v["approved"] \
            else (100.0 - v["confidence"]) / 100.0 * AI_REJECT_DECAY
        combined += v["weight"] * v_score
    combined /= total_w
    approves = sum(1 for v in ok_votes if v["approved"])
    rejects = len(ok_votes) - approves
    approved = approves > rejects and combined >= AI_MIN_COMMITTEE_SCORE
    detail = (
        f"AI {approves} approve/{rejects} reject score={combined:.2f} | {report}"
    )
    logger.info(
        "AI_COMMITTEE done approved=%s score=%.3f %s",
        approved, combined, report[:180],
    )
    return {
        "available": True,
        "approved": approved,
        "score": round(combined, 3),
        "detail": detail[:280],
        "votes": votes,
        "report": report,
    }
