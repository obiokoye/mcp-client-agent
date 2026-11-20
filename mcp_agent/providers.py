"""LLM provider configuration.

Every provider below exposes an OpenAI-compatible Chat Completions API,
so one client library works for all of them. Switch providers by changing
LLM_PROVIDER in your .env file — no code changes needed.
With no key (or DEMO_MODE=true) a scripted fake LLM is used instead.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from openai import OpenAI


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str | None
    key_env: str
    default_model: str


PROVIDERS: dict[str, Provider] = {
    # Free tier: https://aistudio.google.com/apikey
    "gemini": Provider(
        "gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY",
        "gemini-2.5-flash",
    ),
    # Free tier: https://console.groq.com/keys
    "groq": Provider(
        "groq",
        "https://api.groq.com/openai/v1",
        "GROQ_API_KEY",
        "llama-3.3-70b-versatile",
    ),
    "openai": Provider("openai", None, "OPENAI_API_KEY", "gpt-4o-mini"),
    "anthropic": Provider(
        "anthropic",
        "https://api.anthropic.com/v1/",
        "ANTHROPIC_API_KEY",
        "claude-haiku-4-5",
    ),
}


@dataclass(frozen=True)
class LLMSetup:
    client: Any
    model: str
    demo: bool
    notice: str = ""


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def make_client() -> LLMSetup:
    """Build a client for LLM_PROVIDER, or the scripted demo client when there's no key."""
    from .demo import ScriptedLLM  # local import: demo.py is only needed in demo mode

    if _truthy(os.getenv("DEMO_MODE")):
        return LLMSetup(ScriptedLLM(), "demo-scripted", demo=True, notice="DEMO_MODE=true")

    name = os.getenv("LLM_PROVIDER", "gemini").lower()
    if name not in PROVIDERS:
        raise SystemExit(f"Unknown LLM_PROVIDER '{name}'. Choose one of: {', '.join(PROVIDERS)}")

    provider = PROVIDERS[name]
    api_key = os.getenv(provider.key_env)
    if not api_key:
        return LLMSetup(ScriptedLLM(), "demo-scripted", demo=True, notice=f"no {provider.key_env} set")

    client = OpenAI(api_key=api_key, base_url=provider.base_url)
    return LLMSetup(client, os.getenv("LLM_MODEL", provider.default_model), demo=False)
