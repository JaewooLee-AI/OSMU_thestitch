"""Token-minimized multimodal photo analysis.

OSMU_admin's version made **one vision call per photo**, sending a full-size
(1024px) image alongside a ~350-token Korean instruction prompt that embedded
the marketer's memo — and it re-ran from scratch on every campaign, even for
a product shot that had already been analyzed a dozen times. For 더스티치,
whose posts reuse the same 행복인형 / 짱리본핀 studio photos constantly, that
was the single largest avoidable cost in the pipeline.

Five layers cut it down here, in descending order of impact:

1. **Content-hash caption cache.** Captions are keyed by the sha256 of the
   stored image bytes, so the same photo is ever analyzed once — a reuse
   costs zero tokens, not "fewer" tokens.

2. **Memo-independent prompts.** The old prompt asked the model to *both*
   describe the photo *and* judge where it belonged in a specific memo's
   article, which made every caption unique to one campaign and therefore
   uncacheable. Here the vision call only answers "what is literally in this
   picture", and the placement judgment moves to the text-only drafting call,
   which already has the memo and the full article context anyway. Cheaper
   *and* a better division of labor.

3. **Provider-minimum image sizing.** Each provider bills images by area, but
   with a floor: Gemini charges a flat 258 tokens for anything ≤384px, OpenAI
   a flat 85 tokens at `detail="low"`, Anthropic ~(w×h)/750. The "economy"
   preset resizes to exactly that floor, which is plenty for "identify the
   objects, colors and setting" and costs ~4x less than the old 1024px send.

4. **Batching.** All uncached photos for a draft go in a single request, so
   the instruction prompt is paid once instead of once per photo.

5. **Hard output caps.** Captions are budgeted at ~40 tokens each; the model
   is told the length limit and `max_output_tokens` enforces it.

Every call writes an estimate of both what it spent and what the cache/resize
saved into `usage_log`, so ⚙️ Settings can show the actual effect.
"""
from __future__ import annotations

import base64
import io
import json
import math
import re
from typing import Dict, List, Optional, Tuple

from PIL import Image

from ai_workers.multi_llm_router import get_vision_vendor, google_client, load_vendor_config
from core import repo, storage

# Bump when CAPTION_PROMPT changes semantically — old cache entries then stop
# being served instead of silently mixing two prompt generations.
PROMPT_VERSION = "v1-objective"

# `caption_max_tokens` is the ceiling for ONE caption. It has to cover the
# length CAPTION_PROMPT actually asks for (40~70 Korean characters), and
# Korean costs roughly 1.3~1.5 characters per token — so a 70-character
# caption is ~50 tokens, not 45.
#
# The original 45 was set from the character count as if it were a token
# count, which put every batch a few tokens under its own ceiling: observed
# usage was 214/220, 171/175, 171/175, 126/130 — every single call truncated
# mid-JSON. A truncated response is not a cheaper response, it is a wasted
# one: the image tokens (3,386 in for a 3-photo batch) are spent either way
# and the output is unusable. Output is ~5% of a vision call's cost here, so
# headroom is close to free; truncation costs 100%.
QUALITY_PRESETS = {
    "economy": {
        "label": "절약 (권장)",
        "max_edge": 384,
        "jpeg_quality": 70,
        "caption_max_tokens": 80,
        "hint": "Gemini 258토큰 / OpenAI 85토큰 고정 구간. 사물·색상·장소 식별에는 충분합니다.",
    },
    "balanced": {
        "label": "균형",
        "max_edge": 768,
        "jpeg_quality": 78,
        "caption_max_tokens": 110,
        "hint": "작은 글씨(라벨, 안내판)까지 읽어야 할 때.",
    },
    "quality": {
        "label": "고화질",
        "max_edge": 1024,
        "jpeg_quality": 85,
        "caption_max_tokens": 150,
        "hint": "자수·금박 디테일처럼 질감 묘사가 중요한 촬영본용. 토큰 비용이 가장 큽니다.",
    },
}


def _output_budget(preset: dict, count: int) -> int:
    """Token ceiling for a `count`-image batch, including the JSON wrapper.

    The wrapper is not a constant: `{"captions": [` plus a quoted, comma-
    separated item per image grows with the batch, so a flat +40 shrank the
    real per-caption allowance as batches got bigger — which is why the
    4-image batch survived and the 2- and 3-image ones did not.
    """
    scaffolding = 12 + 4 * count
    return preset["caption_max_tokens"] * count + scaffolding

DEFAULT_QUALITY = "economy"
MAX_IMAGES_PER_CALL = 6  # keeps a single request's image payload bounded

CAPTION_PROMPT = (
    "각 사진에 실제로 보이는 것만 한국어로 묘사하세요. 사물, 색상, 소재·질감, 장소/배경, "
    "사람의 유무와 행동, 사진 속 글자를 우선 언급합니다. 추측·감상·마케팅 문구는 쓰지 마세요. "
    "사진 1장당 한 문장, 40자~70자로 짧게 씁니다.\n"
    '반드시 아래 JSON 형식으로만 응답하세요: {"captions": ["1번 사진 설명", "2번 사진 설명"]}'
)


# --- provider image-token accounting ---------------------------------------

def estimate_image_tokens(vendor: str, width: int, height: int) -> int:
    """Provider-documented image token cost, used for the savings report."""
    if vendor == "google":
        # Flat 258 for anything within a single 768px tile footprint;
        # otherwise 258 per 768x768 tile.
        if max(width, height) <= 384:
            return 258
        return 258 * max(1, math.ceil(width / 768)) * max(1, math.ceil(height / 768))
    if vendor == "openai":
        return 85  # detail="low" is a flat rate regardless of resolution
    if vendor == "anthropic":
        return int((width * height) / 750)
    return 258


def _prepare(raw: bytes, preset: dict) -> Tuple[bytes, int, int]:
    """Resize to the preset's max edge and re-encode as JPEG."""
    img = Image.open(io.BytesIO(raw))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((preset["max_edge"], preset["max_edge"]), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=preset["jpeg_quality"], optimize=True)
    return buf.getvalue(), img.width, img.height


_JSON_NOISE_RE = re.compile(r'^[\s{}\[\],]*$|^"?captions"?\s*:?\s*\[?$')


def _parse_captions(raw: str, expected: int) -> Tuple[List[str], bool]:
    """Returns (captions, clean). `clean` is False when the response had to be
    salvaged — the caller must not cache a salvaged caption.

    The salvage path used to split the raw response by lines, which on a
    truncated pretty-printed response yields '{' and '"captions": [' as the
    first two "captions". Worse, those were then written to the content-hash
    cache and served forever, at no API cost, to every future post using
    those photos. Structural fragments are filtered out here, and `clean`
    exists so the caller can refuse to persist anything uncertain.
    """
    cleaned = re.sub(r"```json\s*|```\s*$", "", (raw or "").strip())

    try:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        parsed = json.loads(match.group(0)) if match else {}
        captions = [str(c).strip() for c in (parsed.get("captions") or []) if str(c).strip()]
        if len(captions) >= expected:
            return captions[:expected], True
    except Exception:
        captions = []

    # Truncated array: the completed items are still intact and quoted, so
    # recover those rather than throwing the whole call away. Anything after
    # the last closing quote was cut mid-caption and is dropped.
    if not captions:
        captions = [
            s.strip() for s in re.findall(r'"((?:[^"\\]|\\.)*)"\s*[,\]]', cleaned)
            if s.strip() and s.strip() != "captions"
        ]

    if not captions:
        captions = [
            ln.strip("-• ").strip()
            for ln in cleaned.splitlines()
            if ln.strip() and not _JSON_NOISE_RE.match(ln.strip())
        ]

    salvaged = captions[:expected]
    if len(salvaged) < expected:
        salvaged += ["(사진 설명을 생성하지 못했습니다)"] * (expected - len(salvaged))
    return salvaged, False


# --- per-vendor batched vision call ----------------------------------------

def _call_google(model: str, api_key: str, images: List[bytes], max_tokens: int) -> Tuple[str, int, int]:
    from google.genai import types

    client = google_client(api_key)
    parts = [types.Part.from_bytes(data=b, mime_type="image/jpeg") for b in images]
    parts.append(types.Part.from_text(text=_numbered_prompt(len(images))))

    # `max_output_tokens` is a budget for thinking **plus** visible output on
    # Gemini 2.5+/3.x, and describing a photograph needs no reasoning. Left
    # on, it silently ate the caption budget: a 3-image batch reported only
    # 101 visible tokens against a 264 ceiling and still came back with JSON
    # truncated mid-caption, because the rest went to thoughts. Two of two
    # 3-image batches failed that way and had to be split and re-sent, which
    # meant paying the image tokens twice — 42% of a run's vision spend.
    config_kwargs = {"max_output_tokens": max_tokens}
    if hasattr(types, "ThinkingConfig"):
        try:
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        except Exception:  # noqa: BLE001 — model without a tunable budget
            pass

    try:
        res = client.models.generate_content(
            model=model, contents=parts, config=types.GenerateContentConfig(**config_kwargs)
        )
    except Exception:
        # Some Gemini models reject thinking_budget=0 outright; the caption is
        # worth more than the optimisation, so fall back rather than fail.
        if "thinking_config" not in config_kwargs:
            raise
        config_kwargs.pop("thinking_config")
        res = client.models.generate_content(
            model=model, contents=parts, config=types.GenerateContentConfig(**config_kwargs)
        )

    meta = getattr(res, "usage_metadata", None)
    used_in = getattr(meta, "prompt_token_count", 0) or 0
    # Thoughts are billed and consume the same ceiling, so they belong in the
    # reported output — otherwise the usage screen understates the bill and
    # the truncation check in describe_images compares against the wrong
    # number, which is exactly why it never fired.
    used_out = (getattr(meta, "candidates_token_count", 0) or 0) + (
        getattr(meta, "thoughts_token_count", 0) or 0
    )
    return res.text or "", used_in, used_out


def _call_openai(model: str, api_key: str, images: List[bytes], max_tokens: int) -> Tuple[str, int, int]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    content = []
    for data in images:
        b64 = base64.b64encode(data).decode("ascii")
        content.append(
            {
                "type": "image_url",
                # detail="low" pins the image at a flat 85 tokens; the images
                # are already resized to match, so nothing is wasted either way.
                "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
            }
        )
    content.append({"type": "text", "text": _numbered_prompt(len(images))})

    res = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        max_tokens=max_tokens,
    )
    usage = getattr(res, "usage", None)
    return (
        res.choices[0].message.content or "",
        getattr(usage, "prompt_tokens", 0) or 0,
        getattr(usage, "completion_tokens", 0) or 0,
    )


def _call_anthropic(model: str, api_key: str, images: List[bytes], max_tokens: int) -> Tuple[str, int, int]:
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    content = []
    for data in images:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(data).decode("ascii"),
                },
            }
        )
    content.append({"type": "text", "text": _numbered_prompt(len(images))})

    res = client.messages.create(
        model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": content}]
    )
    text = "".join(block.text for block in res.content if hasattr(block, "text"))
    usage = getattr(res, "usage", None)
    return (
        text,
        getattr(usage, "input_tokens", 0) or 0,
        getattr(usage, "output_tokens", 0) or 0,
    )


def _numbered_prompt(count: int) -> str:
    return f"사진 {count}장이 순서대로 주어집니다.\n{CAPTION_PROMPT}"


# --- public API -------------------------------------------------------------

def describe_images(
    rel_paths: List[str],
    vendor: Optional[str] = None,
    quality: Optional[str] = None,
) -> Dict[str, str]:
    """Returns {rel_path: objective description}.

    Best-effort per photo: a failed batch degrades to placeholder captions
    rather than failing the whole draft, exactly like the original worker.
    """
    if not rel_paths:
        return {}

    brand_kit = repo.get_brand_kit()
    if not brand_kit.get("vision_enabled", True):
        return {p: "(이미지 분석 비활성화 — 담당자 메모만으로 배치합니다)" for p in rel_paths}

    quality = quality or brand_kit.get("vision_quality") or DEFAULT_QUALITY
    preset = QUALITY_PRESETS.get(quality, QUALITY_PRESETS[DEFAULT_QUALITY])
    vendor = vendor or get_vision_vendor()
    model_name, api_key = load_vendor_config(vendor)

    captions: Dict[str, str] = {}
    pending: List[Tuple[str, bytes, int, int]] = []  # (rel_path, jpeg, w, h)
    cache_hits = 0
    saved_tokens = 0

    # Same photo listed twice in one draft (e.g. attached and also referenced
    # by an `[IMAGE:]` tag) must not be analyzed twice — the cache can't catch
    # it, because nothing has been written for it yet within this same run.
    unique_paths = list(dict.fromkeys(rel_paths))

    # --- layer 1+2: cache lookup by image content hash ---
    for rel_path in unique_paths:
        digest = storage.sha256_of(rel_path)
        cached = repo.get_cached_caption(digest, PROMPT_VERSION, quality) if digest else None
        if cached:
            captions[rel_path] = cached
            cache_hits += 1
            continue

        try:
            raw = storage.read_bytes(rel_path)
            prepared, width, height = _prepare(raw, preset)
        except Exception as exc:  # noqa: BLE001
            captions[rel_path] = f"(사진을 읽지 못했습니다: {exc})"
            continue

        # --- layer 3: what the un-resized original would have cost ---
        try:
            orig = Image.open(io.BytesIO(raw))
            saved_tokens += max(
                0,
                estimate_image_tokens(vendor, orig.width, orig.height)
                - estimate_image_tokens(vendor, width, height),
            )
        except Exception:
            pass

        pending.append((rel_path, prepared, width, height))

    if cache_hits:
        # A cache hit avoids both the image tokens and this photo's share of
        # the instruction prompt — counted at the economy image rate plus a
        # conservative 40-token prompt share.
        saved_tokens += cache_hits * (estimate_image_tokens(vendor, preset["max_edge"], preset["max_edge"]) + 40)
    if len(rel_paths) != len(unique_paths):
        saved_tokens += (len(rel_paths) - len(unique_paths)) * estimate_image_tokens(
            vendor, preset["max_edge"], preset["max_edge"]
        )

    def caption_chunk(chunk, allow_split: bool = True) -> None:
        """Caption one batch, writing into `captions`. Splits and retries once
        if the response came back truncated or unparseable.

        Halving is the right retry because the failure is a per-batch output
        ceiling: the same photos in two smaller calls each get the full
        wrapper allowance. Retrying the same batch unchanged would just
        truncate again at the same place.
        """
        nonlocal saved_tokens
        images = [item[1] for item in chunk]
        max_tokens = _output_budget(preset, len(chunk))

        try:
            if vendor == "google":
                raw_text, used_in, used_out = _call_google(model_name, api_key, images, max_tokens)
            elif vendor == "openai":
                raw_text, used_in, used_out = _call_openai(model_name, api_key, images, max_tokens)
            elif vendor == "anthropic":
                raw_text, used_in, used_out = _call_anthropic(model_name, api_key, images, max_tokens)
            else:
                raise ValueError(f"Vision captioning is not implemented for vendor: {vendor}")
        except Exception as exc:  # noqa: BLE001
            print(f"[vision] batch failed ({vendor}): {exc}")
            for rel_path, _data, _w, _h in chunk:
                captions[rel_path] = f"(사진 설명 실패: {exc})"
            return

        parsed, clean = _parse_captions(raw_text, len(chunk))
        # Landing within a few tokens of the ceiling means the model was cut
        # off mid-sentence, even when the salvage path recovered enough
        # complete items to look successful.
        truncated = used_out >= max_tokens - 8

        repo.log_usage(
            kind="vision",
            vendor=vendor,
            model=model_name,
            image_count=len(chunk),
            est_input_tokens=used_in,
            est_output_tokens=used_out,
            note=f"quality={quality}, batch={len(chunk)}"
            + ("" if clean and not truncated else ", truncated/salvaged"),
        )

        if (not clean or truncated) and allow_split and len(chunk) > 1:
            mid = len(chunk) // 2
            print(
                f"[vision] batch of {len(chunk)} came back "
                f"{'truncated' if truncated else 'unparseable'} "
                f"({used_out}/{max_tokens} tokens) — splitting and retrying once."
            )
            caption_chunk(chunk[:mid], allow_split=False)
            caption_chunk(chunk[mid:], allow_split=False)
            return

        # Per-photo prompt overhead saved by batching: the instruction is sent
        # once for the chunk instead of once per photo.
        if len(chunk) > 1:
            saved_tokens += (len(chunk) - 1) * len(_numbered_prompt(len(chunk))) // 2

        for (rel_path, _data, _w, _h), caption in zip(chunk, parsed):
            captions[rel_path] = caption
            digest = storage.sha256_of(rel_path)
            # Only a cleanly parsed, untruncated caption earns a cache entry.
            # The cache is keyed by image content hash and never expires, so
            # persisting a salvaged caption means every future post using that
            # photo silently inherits it — for free, which is what made the
            # previous failure invisible.
            if digest and clean and not truncated and not caption.startswith("("):
                repo.put_cached_caption(digest, PROMPT_VERSION, quality, caption)

    # --- layer 4: one call per batch, not one call per photo ---
    for start in range(0, len(pending), MAX_IMAGES_PER_CALL):
        caption_chunk(pending[start:start + MAX_IMAGES_PER_CALL])

    if cache_hits or saved_tokens:
        repo.log_usage(
            kind="vision-saving",
            vendor=vendor,
            model=model_name,
            cache_hits=cache_hits,
            est_saved_tokens=saved_tokens,
            note=f"cache hits {cache_hits}/{len(rel_paths)}, quality={quality}",
        )

    return {p: captions.get(p, "(사진 설명 없음)") for p in rel_paths}


def preview_cost(rel_paths: List[str], vendor: Optional[str] = None, quality: Optional[str] = None) -> dict:
    """Dry-run estimate shown in the Workbench before the marketer commits to
    a generation run — 'this draft will cost ~N image tokens, M of your K
    photos are already cached'."""
    if not rel_paths:
        return {"images": 0, "cached": 0, "to_analyze": 0, "est_tokens": 0, "quality": quality}

    brand_kit = repo.get_brand_kit()
    quality = quality or brand_kit.get("vision_quality") or DEFAULT_QUALITY
    preset = QUALITY_PRESETS.get(quality, QUALITY_PRESETS[DEFAULT_QUALITY])
    try:
        vendor = vendor or get_vision_vendor()
    except Exception:
        vendor = "google"

    # Dedupe to match describe_images, so the quoted figure is what will
    # actually be spent rather than an inflated one.
    unique_paths = list(dict.fromkeys(rel_paths))
    cached = 0
    for rel_path in unique_paths:
        digest = storage.sha256_of(rel_path)
        if digest and repo.get_cached_caption(digest, PROMPT_VERSION, quality):
            cached += 1

    to_analyze = len(unique_paths) - cached
    per_image = estimate_image_tokens(vendor, preset["max_edge"], preset["max_edge"])
    batches = math.ceil(to_analyze / MAX_IMAGES_PER_CALL) if to_analyze else 0
    prompt_tokens = batches * (len(_numbered_prompt(MAX_IMAGES_PER_CALL)) // 2)

    return {
        # unique count, not raw list length — the same photo referenced twice
        # is one analysis, and reporting 10 would overstate the cost.
        "images": len(unique_paths),
        "cached": cached,
        "to_analyze": to_analyze,
        "est_tokens": to_analyze * per_image + prompt_tokens,
        "per_image": per_image,
        "quality": quality,
        "vendor": vendor,
    }
