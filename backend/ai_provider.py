"""Constrained LLM reranking. Business rules stay in domain.py."""

import asyncio
import json
import logging

import httpx

from config import (
    AI_PROVIDER,
    AI_TIMEOUT_SECONDS,
    NVIDIA_API_KEY,
    NVIDIA_MODEL,
    OPENAI_API_KEY,
    OPENAI_MODEL,
)


LOGGER = logging.getLogger(__name__)
REASON_CODES = ["target", "skill_gap", "history", "availability"]
CHOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "choices": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "reason_codes": {
                        "type": "array",
                        "items": {"type": "string", "enum": REASON_CODES},
                    },
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["event_id", "reason_codes", "evidence_ids"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["choices"],
    "additionalProperties": False,
}


def _request_payload(state, candidates):
    return {
        "target": state["target"],
        "gaps": [
            {"skill_id": gap["skill_id"], "gap": gap["gap"], "critical": gap["critical"]}
            for gap in state["gaps"] if gap["gap"] > 0
        ],
        "candidates": [
            {
                "event_id": candidate["event_id"],
                "title": candidate["title"],
                "description": candidate["description"],
                "factors": candidate["factors"],
                "impacts": [
                    {
                        "skill_id": impact["skill_id"],
                        "closes_gap": impact["closes_gap"],
                        "critical": impact["critical"],
                    }
                    for impact in candidate["impacts"]
                ],
                "evidence_ids": [f"{candidate['event_id']}:{code}" for code in REASON_CODES],
            }
            for candidate in candidates[:8]
        ],
    }


def _validate(response, candidates):
    if not isinstance(response, dict) or not isinstance(response.get("choices"), list):
        raise ValueError("AI response has no choices")
    if not 1 <= len(response["choices"]) <= 3:
        raise ValueError("AI must choose 1–3 events")
    allowed = {item["event_id"]: item for item in candidates[:8]}
    seen = set()
    result = []
    for choice in response["choices"]:
        if not isinstance(choice, dict):
            raise ValueError("Invalid AI choice")
        event_id = choice.get("event_id")
        codes = choice.get("reason_codes")
        evidence = choice.get("evidence_ids")
        if event_id not in allowed or event_id in seen:
            raise ValueError("AI chose an unknown or repeated event")
        if (
            not isinstance(codes, list)
            or len(codes) < 3
            or len(codes) != len(set(codes))
            or any(code not in REASON_CODES for code in codes)
        ):
            raise ValueError("AI returned unsupported reason codes")
        if not isinstance(evidence, list) or not evidence or any(
            fact not in {f"{event_id}:{code}" for code in REASON_CODES} for fact in evidence
        ):
            raise ValueError("AI returned unsupported evidence IDs")
        if any(f"{event_id}:{code}" not in evidence for code in codes):
            raise ValueError("AI reason has no matching evidence")
        seen.add(event_id)
        result.append((allowed[event_id], codes))
    return result


async def _openai(client, payload, timeout):
    response = await client.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={
            "model": OPENAI_MODEL,
            "store": False,
            "instructions": (
                "Ты ранжируешь только допустимые учебные активности. Выбери 1–3 разных event_id. "
                "Учитывай критичные разрывы, реальный прирост, историю и доступность. "
                "Для каждого выбора укажи минимум три разных reason_codes и соответствующие evidence_ids. "
                "Не добавляй событий и фактов. Верни только структуру по схеме."
            ),
            "input": json.dumps(payload, ensure_ascii=False),
            "text": {"format": {"type": "json_schema", "name": "career_recommendations", "strict": True, "schema": CHOICE_SCHEMA}},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    texts = [part.get("text") for item in body.get("output", []) for part in item.get("content", []) if part.get("type") == "output_text"]
    if not texts:
        raise ValueError("OpenAI returned no output_text")
    return json.loads("".join(texts))


async def _nvidia(client, payload, timeout):
    response = await client.post(
        "https://integrate.api.nvidia.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {NVIDIA_API_KEY}"},
        json={
            "model": NVIDIA_MODEL,
            "temperature": 0.1,
            "max_tokens": 500,
            "messages": [
                {"role": "system", "content": (
                    "Choose 1 to 3 valid event_id values. Return ONLY JSON object with choices array; "
                    "each choice contains event_id, reason_codes, evidence_ids. "
                    "Reason codes: target, skill_gap, history, availability. "
                    "Use at least three distinct reason_codes per choice. "
                    "Each evidence ID must be event_id:reason_code. Never invent facts or IDs."
                )},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:].strip()
    return json.loads(content)


async def rerank(state, candidates):
    """Return (selected candidate/code tuples, provider name), or (None, fallback)."""
    if not candidates:
        return None, "no_candidates"
    if AI_PROVIDER == "off":
        return None, "fallback"
    order = []
    if AI_PROVIDER in ("auto", "openai") and OPENAI_API_KEY:
        order.append(("openai", _openai))
    if AI_PROVIDER in ("auto", "nvidia") and NVIDIA_API_KEY:
        order.append(("nvidia", _nvidia))
    if not order:
        return None, "fallback"
    payload = _request_payload(state, candidates)
    async with httpx.AsyncClient() as client:
        for name, call in order:
            timeout = min(AI_TIMEOUT_SECONDS / len(order), 6.5) if len(order) > 1 else AI_TIMEOUT_SECONDS
            try:
                raw = await asyncio.wait_for(call(client, payload, timeout), timeout=timeout)
                return _validate(raw, candidates), name
            except (asyncio.TimeoutError, httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
                LOGGER.warning("AI provider %s failed: %s: %s", name, type(exc).__name__, str(exc)[:200])
                continue
    return None, "fallback"
