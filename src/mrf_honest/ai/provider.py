"""Model access through the public ``anthropic`` SDK, credentials from the environment."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_BEDROCK_MODEL = "global.anthropic.claude-sonnet-5"
DEFAULT_BEDROCK_REGION = "us-west-2"
PROVIDERS = ("anthropic", "bedrock")


class ProviderError(RuntimeError):
    """The model call did not produce a usable completion."""


@dataclass(frozen=True)
class Completion:
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int


class Provider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    def complete_json(
        self, *, system: str, user: str, schema: Mapping[str, Any], max_tokens: int
    ) -> Completion: ...


class SDKProvider:
    """Adapter over an ``anthropic`` client (first-party or ``AnthropicBedrock``)."""

    def __init__(self, client: Any, *, model: str, name: str) -> None:
        self._client = client
        self._model = model
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    def complete_json(
        self, *, system: str, user: str, schema: Mapping[str, Any], max_tokens: int
    ) -> Completion:
        import anthropic

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": dict(schema)}},
            )
        except anthropic.APIStatusError as exc:
            raise ProviderError(
                f"{self._name} request failed with status {exc.status_code}"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"{self._name} is unreachable") from exc
        stop = str(getattr(response, "stop_reason", "") or "")
        if stop == "refusal":
            raise ProviderError("the model declined this request")
        if stop == "max_tokens":
            raise ProviderError("the model response was truncated")
        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        if not text.strip():
            raise ProviderError("the model returned no text")
        usage = getattr(response, "usage", None)
        return Completion(
            text=text,
            provider=self._name,
            model=str(getattr(response, "model", self._model)),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        )


@dataclass
class ScriptedCall:
    system: str
    user: str
    schema: dict[str, Any]


class ScriptedProvider:
    """Canned JSON replies in order; records calls. Tests and offline replay only."""

    def __init__(self, responses: Sequence[str], *, model: str = "scripted-model") -> None:
        self._responses = list(responses)
        self._model = model
        self.calls: list[ScriptedCall] = []

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def model(self) -> str:
        return self._model

    def complete_json(
        self, *, system: str, user: str, schema: Mapping[str, Any], max_tokens: int
    ) -> Completion:
        self.calls.append(ScriptedCall(system, user, dict(schema)))
        if not self._responses:
            raise ProviderError("scripted provider has no response left")
        return Completion(self._responses.pop(0), "scripted", self._model, 0, 0)


@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    region: str | None

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ
        provider = env.get("MRF_AI_PROVIDER", "anthropic").strip().lower()
        if provider not in PROVIDERS:
            raise ProviderError(f"MRF_AI_PROVIDER must be one of {', '.join(PROVIDERS)}")
        default = DEFAULT_ANTHROPIC_MODEL if provider == "anthropic" else DEFAULT_BEDROCK_MODEL
        model = env.get("MRF_AI_MODEL", "").strip() or default
        region = (
            env.get("MRF_AI_AWS_REGION", "").strip()
            or env.get("AWS_REGION", "").strip()
            or DEFAULT_BEDROCK_REGION
        )
        return cls(provider, model, region if provider == "bedrock" else None)


def provider_from_settings(settings: Settings) -> Provider:
    try:
        import anthropic
    except ImportError as exc:
        raise ProviderError(
            f"the `anthropic` SDK could not be imported ({exc}); install the `ai` extra"
        ) from exc
    try:
        client: Any
        if settings.provider == "bedrock":
            client = anthropic.AnthropicBedrock(aws_region=settings.region)
        else:
            client = anthropic.Anthropic()
    except anthropic.AnthropicError as exc:
        raise ProviderError(
            f"could not configure the {settings.provider} client: {exc.__class__.__name__}"
        ) from exc
    return SDKProvider(client, model=settings.model, name=settings.provider)


def provider_from_env(environ: Mapping[str, str] | None = None) -> Provider:
    return provider_from_settings(Settings.from_environ(environ))
