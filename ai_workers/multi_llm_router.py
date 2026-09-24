"""Instantiates the configured LLM vendor client and routes prompts to it.

Ported from OSMU_admin/ai_workers/multi_llm_router.py. Two substantive
changes for this build:

1. Configuration reads from SQLite (`core.repo`) instead of Supabase.
2. Every call reports back real token usage from the provider response
   (OpenAI `usage`, Anthropic `usage`, Gemini `usage_metadata`) into
   `usage_log`, so the token-minimization work in ai_workers/vision.py is
   measured rather than assumed. Providers that omit usage fall back to a
   conservative character-based estimate.
"""
from __future__ import annotations

from typing import Optional, Tuple

from core import repo
from core.crypto_utils import decrypt_api_key

VENDORS = {
    "google": {
        "label": "Google Gemini",
        "icon": "✨",
        "default_model": "gemini-2.5-flash",
        "supports_vision": True,
        "note": "이미지 분석 기본 벤더 — 이미지 토큰 단가가 가장 낮습니다.",
    },
    "openai": {
        "label": "OpenAI",
        "icon": "🤖",
        "default_model": "gpt-5-mini",
        "supports_vision": True,
    },
    "anthropic": {
        "label": "Anthropic",
        "icon": "🧠",
        "default_model": "claude-sonnet-5",
        "supports_vision": True,
    },
}

# Korean averages roughly 2 characters per token across these tokenizers —
# only used when a provider doesn't return usage metadata.
_CHARS_PER_TOKEN = 2.0


def vision_capable_vendors() -> list:
    return [k for k, spec in VENDORS.items() if spec.get("supports_vision")]


def get_configured_vendor() -> str:
    """The single vendor the admin chose for writing/auditing content
    (Settings page -> brand_kit.default_generation_vendor). Every pipeline
    calls this instead of hardcoding a vendor, so switching models is a
    one-click admin action with no silent fallback to an unintended model."""
    brand_kit = repo.get_brand_kit()
    vendor = brand_kit.get("default_generation_vendor")
    if not vendor:
        raise RuntimeError("기본 생성 모델이 지정되지 않았습니다. ⚙️ 설정 페이지에서 먼저 지정하세요.")

    setting = repo.get_llm_setting(vendor)
    if not setting or not setting.get("is_active"):
        raise RuntimeError(f"지정된 기본 생성 모델('{vendor}')이 비활성 상태이거나 등록되어 있지 않습니다.")
    return vendor


def get_vision_vendor() -> str:
    """Vision can run on a different (cheaper) vendor than the writing model —
    that separation is itself a cost lever, since captioning is a small,
    high-volume task while drafting is a large, low-volume one. Falls back to
    the generation vendor when the admin hasn't picked one."""
    brand_kit = repo.get_brand_kit()
    vendor = brand_kit.get("vision_vendor")
    if vendor:
        setting = repo.get_llm_setting(vendor)
        if setting and setting.get("is_active"):
            return vendor
    return get_configured_vendor()


def test_connection(vendor: str, model_name: str, api_key: str) -> Tuple[bool, str]:
    """Sends a minimal 'Hello' prompt to verify the key/model combo works."""
    try:
        if vendor == "openai":
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
            client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": "Hello"}],
                # max_completion_tokens: reasoning models reject max_tokens.
                max_completion_tokens=16,
            )
        elif vendor == "anthropic":
            from anthropic import Anthropic

            client = Anthropic(api_key=api_key)
            client.messages.create(
                model=model_name,
                max_tokens=5,
                messages=[{"role": "user", "content": "Hello"}],
            )
        elif vendor == "google":
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            client.models.generate_content(
                model=model_name,
                contents="Hello",
                config=types.GenerateContentConfig(max_output_tokens=5),
            )
        else:
            return False, f"Unsupported vendor: {vendor}"
        return True, "연결 정상"
    except Exception as exc:  # surfaced directly to the admin in the UI
        return False, str(exc)


def load_vendor_config(vendor: str) -> Tuple[str, str]:
    setting = repo.get_llm_setting(vendor)
    if not setting:
        raise RuntimeError(f"'{vendor}' 벤더의 설정이 없습니다. ⚙️ 설정 페이지에서 먼저 등록하세요.")
    api_key = decrypt_api_key(setting["encrypted_api_key"])
    return setting["model_name"], api_key


def _estimate_tokens(text: str) -> int:
    return int(len(text or "") / _CHARS_PER_TOKEN)


class OutputTruncatedError(RuntimeError):
    """The provider stopped at the output ceiling even after one enlarged retry.

    Raised instead of returning the partial text, because almost every caller
    here asks the model to reproduce a whole post (draft, revision, SEO
    rebalance) or a JSON object (audits): a cut-off post saved as if it were
    finished, or a cut-off JSON object parsed as "no issues", is a worse
    outcome than a visible failure. `partial` is kept for diagnosis only.
    """

    def __init__(self, message: str, partial: str = ""):
        super().__init__(message)
        self.partial = partial


# One retry at this multiple of the caller's ceiling before giving up. The
# callers' ceilings are sized for typical Korean output (~2 chars/token), and
# the tokenizers disagree by more than that factor on Korean — a single larger
# attempt absorbs the variance without letting a runaway response cost more
# than twice what the caller budgeted.
_TRUNCATION_RETRY_FACTOR = 2

# Gemini 2.5+/3.x and OpenAI reasoning models spend hidden reasoning tokens out
# of the same output ceiling as the visible answer. When reasoning can't be
# switched off (some models reject a zero budget), this much headroom is added
# on top so the caller's ceiling still bounds the *visible* text.
_REASONING_HEADROOM = 8192


def _is_openai_reasoning_model(model_name: str) -> bool:
    return (model_name or "").lower().startswith(("o1", "o3", "o4", "gpt-5"))


def _call_openai(model_name, api_key, prompt, system, max_tokens):
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    # `max_completion_tokens`, not `max_tokens`: reasoning models (o-series,
    # gpt-5) reject the latter outright, and every current chat model accepts
    # the former. Reasoning models count hidden reasoning tokens against it,
    # hence the headroom for them only — for ordinary chat models the
    # caller's ceiling stays the real cost ceiling.
    headroom = _REASONING_HEADROOM if _is_openai_reasoning_model(model_name) else 0
    res = client.chat.completions.create(
        model=model_name, messages=messages, max_completion_tokens=max_tokens + headroom
    )
    choice = res.choices[0]
    text = choice.message.content or ""
    usage_in = usage_out = 0
    if getattr(res, "usage", None):
        usage_in, usage_out = res.usage.prompt_tokens, res.usage.completion_tokens
    return text, usage_in, usage_out, choice.finish_reason == "length"


def _call_anthropic(model_name, api_key, prompt, system, max_tokens):
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    kwargs = {"model": model_name, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    if system:
        # The same system prompt (brand kit + audit rules) goes out several
        # times per generation — the compliance audit alone runs for the body,
        # Instagram, X and Shorts. Marking it cacheable bills the repeats at
        # the cache-read rate; prompts under the model's minimum cacheable
        # length are simply sent uncached.
        kwargs["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    res = client.messages.create(**kwargs)
    text = "".join(block.text for block in res.content if getattr(block, "type", "") == "text")
    usage_in = usage_out = 0
    usage = getattr(res, "usage", None)
    if usage:
        usage_in = (
            (usage.input_tokens or 0)
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
        )
        usage_out = usage.output_tokens or 0
    return text, usage_in, usage_out, res.stop_reason == "max_tokens"


def _call_google(model_name, api_key, prompt, system, max_tokens):
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    def _config(thinking_off: bool):
        # Without an explicit config, generate_content ignores max_tokens
        # entirely and falls back to the model's own (much larger) default —
        # every caller's cost ceiling would silently be a no-op for Gemini.
        #
        # `max_output_tokens` is a budget for thinking **plus** visible text.
        # ai_workers/vision.py already turns thinking off for this reason; the
        # text path never did, so a post-length response could come back cut
        # off with most of the budget spent on thoughts.
        kwargs = {"max_output_tokens": max_tokens}
        if thinking_off:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        else:
            kwargs["max_output_tokens"] = max_tokens + _REASONING_HEADROOM
        if system:
            kwargs["system_instruction"] = system
        return types.GenerateContentConfig(**kwargs)

    try:
        res = client.models.generate_content(model=model_name, contents=prompt, config=_config(True))
    except Exception as exc:  # noqa: BLE001
        # Some models (e.g. 2.5 Pro) refuse a zero thinking budget. Fall back
        # to thinking-on with headroom rather than failing the call — but only
        # for that specific rejection, not for auth/quota errors.
        if "thinking" not in str(exc).lower():
            raise
        res = client.models.generate_content(model=model_name, contents=prompt, config=_config(False))

    text = res.text or ""
    usage_in = usage_out = 0
    meta = getattr(res, "usage_metadata", None)
    if meta:
        usage_in = getattr(meta, "prompt_token_count", 0) or 0
        usage_out = (getattr(meta, "candidates_token_count", 0) or 0) + (
            getattr(meta, "thoughts_token_count", 0) or 0
        )
    truncated = False
    candidates = getattr(res, "candidates", None) or []
    if candidates:
        truncated = "MAX_TOKENS" in str(getattr(candidates[0], "finish_reason", "") or "")
    return text, usage_in, usage_out, truncated


_CALLERS = {"openai": _call_openai, "anthropic": _call_anthropic, "google": _call_google}


def generate_text(
    vendor: str,
    prompt: str,
    system: Optional[str] = None,
    max_tokens: int = 2000,
    note: Optional[str] = None,
) -> str:
    """Routes a prompt to the configured vendor and returns the text response.
    Records real token usage against `usage_log`.

    Never returns a response the provider cut off at the output ceiling: it
    retries once with a larger ceiling, then raises OutputTruncatedError. An
    empty response (e.g. a safety block) raises RuntimeError. Callers that are
    best-effort already catch exceptions and keep their input unchanged.
    """
    caller = _CALLERS.get(vendor)
    if caller is None:
        raise ValueError(f"Unsupported vendor: {vendor}")
    model_name, api_key = load_vendor_config(vendor)

    budget = max_tokens
    for attempt in range(2):
        text, usage_in, usage_out, truncated = caller(model_name, api_key, prompt, system, budget)

        if not usage_in:
            usage_in = _estimate_tokens((system or "") + prompt)
        if not usage_out:
            usage_out = _estimate_tokens(text)
        repo.log_usage(
            kind="text",
            vendor=vendor,
            model=model_name,
            est_input_tokens=usage_in,
            est_output_tokens=usage_out,
            note=f"{note or 'text'}{' (truncated)' if truncated else ''}",
        )

        if not truncated:
            break
        if attempt == 0:
            print(f"[multi_llm_router] {note or 'text'}: output hit {budget} tokens — retrying with a larger ceiling")
            budget = max_tokens * _TRUNCATION_RETRY_FACTOR
    else:
        raise OutputTruncatedError(
            f"모델 응답이 출력 한도({budget} 토큰)에서 잘렸습니다 ({note or 'text'}). "
            "잘린 결과를 저장하지 않고 중단했습니다. 다시 시도하거나 분량을 줄여주세요.",
            partial=text,
        )

    if not text.strip():
        raise RuntimeError(
            f"모델이 빈 응답을 반환했습니다 ({note or 'text'}). 안전 필터에 걸렸거나 일시적 오류일 수 있습니다."
        )
    return text


_EMBEDDING_DIMENSIONS = 1536


def generate_embedding(vendor: str, text: str) -> list:
    """Vector embedding, kept for parity with the original RAG path.

    Embeddings are a distinct endpoint, not a capability every chat model
    exposes (Anthropic has no embedding API at all), so this routes per
    vendor rather than assuming OpenAI regardless of what's configured.
    """
    if vendor == "openai":
        _model, api_key = load_vendor_config("openai")
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        res = client.embeddings.create(model="text-embedding-3-small", input=text)
        return res.data[0].embedding

    if vendor == "google":
        _model, api_key = load_vendor_config("google")
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        res = client.models.embed_content(
            model="gemini-embedding-001",
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=_EMBEDDING_DIMENSIONS),
        )
        return res.embeddings[0].values

    raise ValueError(f"Embeddings are not supported for vendor: {vendor}")
