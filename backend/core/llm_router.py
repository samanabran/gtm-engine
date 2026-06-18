from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any

from .audit_logger import AuditLogger, build_audit_logger
from .exceptions import LLMError
from .metrics import record_llm_call
from .prompt_manager import PromptManager, build_prompt_manager

try:  # pragma: no cover - optional provider path
    from litellm import acompletion  # type: ignore
except Exception:  # pragma: no cover
    acompletion = None  # type: ignore[assignment]


@dataclass(slots=True)
class LLMResponse:
    content: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


@dataclass(slots=True)
class LLMCandidate:
    provider: str
    model: str
    api_key: str | None
    api_base: str | None = None


def _fallback_chain_from_env() -> list[LLMCandidate]:
    """Build the ordered fallback chain from env vars.

    Each entry only joins the chain if its API key env var is set. The primary
    provider (LLM_PROVIDER/LLM_MODEL/LLM_API_KEY/LLM_API_BASE) is NOT included
    here -- callers prepend it themselves so existing single-provider configs
    keep working unchanged.
    """
    candidates: list[LLMCandidate] = []
    specs = [
        ("groq", "GROQ_API_KEY", os.getenv("GROQ_MODEL", "groq/llama-3.3-70b-versatile"), None),
        ("mistral", "MISTRAL_API_KEY", os.getenv("MISTRAL_MODEL", "mistral/mistral-large-latest"), None),
        ("openrouter", "OPENROUTER_API_KEY", os.getenv("OPENROUTER_MODEL", "openrouter/openai/gpt-4o-mini"), None),
        ("cohere", "COHERE_API_KEY", os.getenv("COHERE_MODEL", "cohere/command-r-plus"), None),
        ("gemini", "GEMINI_API_KEY", os.getenv("GEMINI_MODEL", "gemini/gemini-1.5-flash"), None),
    ]
    for provider_name, key_env, model, api_base in specs:
        api_key = os.getenv(key_env)
        if api_key:
            candidates.append(LLMCandidate(provider=provider_name, model=model, api_key=api_key, api_base=api_base))
    return candidates


@dataclass(slots=True)
class LLMRouter:
    provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "mock"))
    model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "mock-model"))
    api_key: str | None = field(default_factory=lambda: os.getenv("LLM_API_KEY"))
    api_base: str | None = field(default_factory=lambda: os.getenv("LLM_API_BASE") or None)
    enable_fallback_chain: bool = True
    prompt_manager: PromptManager = field(default_factory=build_prompt_manager)
    audit_logger: AuditLogger = field(default_factory=build_audit_logger)

    def _candidates(self) -> list[LLMCandidate]:
        primary = LLMCandidate(provider=self.provider, model=self.model, api_key=self.api_key, api_base=self.api_base)
        if not self.enable_fallback_chain:
            return [primary]
        return [primary, *_fallback_chain_from_env()]

    async def complete(
        self,
        *,
        system: str,
        user: str,
        format: str = "text",
        temperature: float = 0.2,
        metadata: dict[str, Any] | None = None,
    ) -> LLMResponse:
        last_error: Exception | None = None
        if acompletion is not None:
            for candidate in self._candidates():
                if candidate.provider == "mock" or not candidate.api_key:
                    continue
                try:
                    kwargs: dict[str, Any] = {
                        "model": candidate.model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "temperature": temperature,
                        "api_key": candidate.api_key,
                    }
                    if candidate.api_base:
                        kwargs["api_base"] = candidate.api_base
                    response = await acompletion(**kwargs)
                    content = response.choices[0].message.content or ""
                    usage = getattr(response, "usage", None)
                    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
                    cost_usd = float(getattr(response, "cost", 0.0) or 0.0)
                    record_llm_call(candidate.provider, candidate.model, prompt_tokens, completion_tokens, cost_usd)
                    self.audit_logger.log_agent_run(
                        org_id=str(metadata.get("org_id")) if metadata else "unknown",
                        agent_name=str(metadata.get("agent_name")) if metadata else "llm",
                        prompt=system + "\n" + user,
                        response=content,
                        metadata={**(metadata or {}), "fallback_used": candidate.provider != self.provider},
                    )
                    return LLMResponse(
                        content=content,
                        provider=candidate.provider,
                        model=candidate.model,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        cost_usd=cost_usd,
                    )
                except Exception as exc:  # pragma: no cover - external path
                    last_error = exc
                    continue
            if last_error is not None:
                # All configured providers in the chain failed -- fall through to the
                # deterministic local scaffold rather than hard-failing the caller.
                pass

        content = self._fallback_complete(system=system, user=user, format=format)
        record_llm_call(self.provider, self.model, 0, 0, 0.0)
        self.audit_logger.log_agent_run(
            org_id=str(metadata.get("org_id")) if metadata else "unknown",
            agent_name=str(metadata.get("agent_name")) if metadata else "llm",
            prompt=system + "\n" + user,
            response=content,
            metadata=metadata or {},
        )
        return LLMResponse(content=content, provider=self.provider, model=self.model)

    async def embed(self, text: str) -> list[float]:
        if self.provider != "mock" and acompletion is not None and self.api_key:
            # This scaffold keeps embeddings deterministic until a real provider is wired in.
            pass
        return self._fallback_embedding(text)

    def _fallback_complete(self, *, system: str, user: str, format: str) -> str:
        digest = hashlib.sha256((system + "\n" + user).encode("utf-8")).hexdigest()
        if format == "json":
            variations = []
            for index in range(3):
                variations.append(
                    {
                        "subject": f"Scaffold follow-up {index + 1}",
                        "body": "Thanks for the context. I would love to keep the conversation going.",
                        "hook_type": "contextual",
                        "confidence": round(0.9 - index * 0.1, 2),
                    }
                )
            payload = {
                "score": round((int(digest[:4], 16) % 1000) / 1000, 3),
                "explanation": "Deterministic scaffold response generated locally.",
                "fit_signals": ["scaffold", "deterministic"],
                "gap_signals": ["llm_provider_not_configured"],
                "variations": variations,
            }
            return json.dumps(payload)
        return f"scaffold-response::{digest[:32]}"

    def _fallback_embedding(self, text: str, dimensions: int = 1536) -> list[float]:
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        while len(values) < dimensions:
            for byte in seed:
                values.append(round(byte / 255.0, 6))
                if len(values) >= dimensions:
                    break
        return values


def build_llm_router() -> LLMRouter:
    return LLMRouter()
