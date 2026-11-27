"""Provider-agnostic LLM client. Every framework uses the same client, model and config.

``GeminiClient`` talks to Google Gemini (key from ``GOOGLE_API_KEY``). ``FakeLLMClient`` returns
scripted responses so tests and CI run offline, free and deterministically.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

# USD per 1M tokens (input, output). Used only for *estimated* cost; verify against current
# provider pricing before quoting numbers. Unknown models are costed at 0 and flagged.
COST_PER_1M: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-pro": (1.25, 10.00),
}


@dataclass
class LLMUsage:
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    latency_ms: float = 0.0

    @property
    def cost_usd_estimate(self) -> float:
        cin, cout = COST_PER_1M.get(self.model, (0.0, 0.0))
        return (self.input_tokens * cin + self.output_tokens * cout) / 1_000_000

    def add(self, other: LLMUsage) -> None:
        self.model = other.model or self.model
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.calls += other.calls
        self.latency_ms += other.latency_ms


class LLMError(Exception):
    pass


class LLMClient(Protocol):
    model: str

    async def complete(self, system: str, prompt: str) -> tuple[str, LLMUsage]: ...


def _extract_json(text: str) -> Any:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    return json.loads(text)


async def structured[T: BaseModel](
    llm: LLMClient, system: str, prompt: str, schema: type[T], *, max_attempts: int = 2
) -> tuple[T, LLMUsage]:
    """Ask for JSON matching ``schema``; on malformed output retry once with the error."""
    usage = LLMUsage(model=llm.model)
    last_error = ""
    for _ in range(max_attempts):
        p = (
            prompt
            if not last_error
            else (
                f"{prompt}\n\nYour previous answer was invalid: {last_error}\n"
                "Return ONLY corrected JSON."
            )
        )
        text, u = await llm.complete(system, p)
        usage.add(u)
        try:
            return schema.model_validate(_extract_json(text)), usage
        except (json.JSONDecodeError, ValidationError) as e:
            last_error = str(e)[:800]
    raise LLMError(f"model output failed schema {schema.__name__}: {last_error[:300]}")


class GeminiClient:
    def __init__(
        self, api_key: str, model: str, temperature: float = 0.0, max_output_tokens: int = 4096
    ) -> None:
        from google import genai

        if not api_key:
            raise LLMError("GOOGLE_API_KEY is not set")
        self._client = genai.Client(api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.max_attempts = 4

    async def complete(self, system: str, prompt: str) -> tuple[str, LLMUsage]:
        from google.genai import types

        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            response_mime_type="application/json",
        )
        if "2.5-flash" in self.model:  # keep cost/latency low; reasoning not needed here
            cfg.thinking_config = types.ThinkingConfig(thinking_budget=0)
        t0 = time.perf_counter()
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = await self._client.aio.models.generate_content(
                    model=self.model, contents=prompt, config=cfg
                )
                break
            except Exception as e:  # SDK raises several error types; normalize
                code = getattr(e, "code", None)
                # Daily quotas will not recover within a retry window; per-minute limits will.
                daily_quota = "PerDay" in str(e)
                network = isinstance(e, httpx.TransportError | TimeoutError)
                transient = network or code in {500, 503, 504} or (code == 429 and not daily_quota)
                if transient and attempt < self.max_attempts:
                    # Bounded exponential backoff for overload / rate limits.
                    await asyncio.sleep(2**attempt)
                    continue
                raise LLMError(f"gemini call failed: {type(e).__name__}: {str(e)[:200]}") from e
        meta = resp.usage_metadata
        usage = LLMUsage(
            model=self.model,
            input_tokens=(meta.prompt_token_count or 0) if meta else 0,
            output_tokens=((meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0))
            if meta
            else 0,
            calls=1,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
        return resp.text or "", usage


@dataclass
class FakeLLMClient:
    """Deterministic scripted LLM. ``responder(system, prompt) -> json text``."""

    responder: Callable[[str, str], str]
    model: str = "fake-llm"
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def complete(self, system: str, prompt: str) -> tuple[str, LLMUsage]:
        self.calls.append((system, prompt))
        out = self.responder(system, prompt)
        return out, LLMUsage(
            model=self.model, input_tokens=len(prompt) // 4, output_tokens=len(out) // 4, calls=1
        )
