"""Shared Brand Kit -> system prompt assembly.

In OSMU_admin this logic was copy-pasted three times (news_scraper,
instagram_caption_writer, x_thread_writer), each with its own slightly
different subset of Brand Kit fields. Here the per-field blocks live once and
each channel picks the ones that apply, because the differences between
channels are real and worth keeping explicit:

- Naver blog wants keyword-density SEO; Instagram and X actively don't
  (a keyword-repeating paragraph is exactly wrong for a caption or a tweet).
- Every channel wants persona, tone and the glossary, since those define the
  brand voice regardless of format.

Brand Kit data is injected *in full* every time rather than retrieved by
similarity search. For a glossary of 14 terms and a fact list of 15 lines
that is both cheaper and more reliable than embedding + pgvector lookup: the
whole corpus costs ~600 tokens, and a retrieval miss on "더봄봄" would silently
produce a draft that misspells the brand's own product names.
"""
from __future__ import annotations

from typing import List

from ai_workers import content_mode


def persona_block(brand_kit: dict) -> List[str]:
    parts = []
    if brand_kit.get("industry"):
        parts.append(f"[업종] {brand_kit['industry']}")
    if brand_kit.get("brand_name"):
        sub = brand_kit.get("sub_brand")
        label = f"{brand_kit['brand_name']}" + (f" (브랜드: {sub})" if sub else "")
        parts.append(f"[기업/브랜드] {label}")
    if brand_kit.get("persona"):
        parts.append(f"[브랜드 페르소나] {brand_kit['persona']}")
    if brand_kit.get("tone_and_manner"):
        parts.append(f"[톤앤매너 가이드]\n{brand_kit['tone_and_manner']}")
    return parts


def core_facts_block(brand_kit: dict, maximum: int = 2) -> List[str]:
    """The brand's verified facts, offered — not required.

    This used to demand "최소 2개 이상" in every post. With one fixed list and
    a quota, every post cited the same two or three facts in near-identical
    sentences, which is most of why posts about similar products read alike
    (a marketer reported rewriting four such posts by hand). The facts stay
    in the prompt as the only numbers the model may use; whether and how many
    to cite is now tied to the post's subject.
    """
    facts = brand_kit.get("core_facts") or []
    if not facts:
        return []
    joined = "\n".join(f"- {fact}" for fact in facts)
    return [
        "[회사 핵심 팩트 — 이 글의 소재와 직접 관련된 것이 있을 때만 0~"
        f"{maximum}개를 골라 자연스럽게 쓰세요. 관련 없는 팩트를 끼워 넣지 말고, 최근 글에서 "
        "이미 쓴 팩트라면 같은 문장으로 반복하지 마세요. 목록에 없는 수치나 실적은 절대 "
        "지어내지 마세요]\n" + joined
    ]


def terminology_block(brand_kit: dict) -> List[str]:
    terminology = brand_kit.get("terminology") or {}
    if not terminology:
        return []
    glossary = "\n".join(f"- {term}: {desc}" for term, desc in terminology.items())
    return [
        "[회사 용어집 — 아래 용어가 등장할 경우 반드시 이 표기와 의미로 정확히 사용하세요]\n" + glossary
    ]


def few_shot_block(brand_kit: dict) -> List[str]:
    samples = brand_kit.get("few_shot_samples") or []
    if not samples:
        return []
    joined = "\n---\n".join(samples)
    # Voice only. This used to say "어조, 문단 호흡, 정보 배치 순서를 최대한
    # 동일하게 모방" — copying the *structure* made every post open, unfold
    # and close in the same order, so posts about similar products converged
    # on one template. Structure now follows the subject; the sample governs
    # how sentences sound. The tone guide wins on any conflict, because the
    # samples were seeded once while the guide is what the marketer edits.
    return [
        "[우수 포스팅 참조 샘플 — 아래 글의 말투와 문장 길이·호흡만 참고하세요. 글의 구성과 "
        "정보 순서는 샘플을 따라 하지 말고 이 글의 소재에 맞게 새로 짜세요. 내용을 베끼지 "
        "마세요. 샘플과 [톤앤매너 가이드]가 다르면 톤앤매너 가이드를 따르세요]\n" + joined
    ]


RECENT_POSTS_PROMPT_LIMIT = 5


def recent_posts_block(recent: List[dict]) -> List[str]:
    """Openings and endings of recent posts, as "don't repeat these".

    Drafts from one brand kit converge on the same opening line and the same
    closing call-to-action; showing the model what was already published is
    the prevention half of ai_workers/body_variety.py (which measures).
    Only the first/last sentence of each is sent — enough to steer away from
    a template, a few hundred tokens at most.
    """
    if not recent:
        return []
    lines = []
    for post in recent[:RECENT_POSTS_PROMPT_LIMIT]:
        lines.append(
            f"- 제목: {post['title']}\n  첫 문장: {post['opening']}\n  마지막 문장: {post['closing']}"
        )
    return [
        "[최근 발행한 글 — 이 글들과 겹치지 않게 쓰세요. 첫 문장, 문단 구성 순서, 마지막 문장을 "
        "아래와 다르게 새로 짓고, 같은 설명을 같은 문장으로 되풀이하지 마세요. 네이버는 같은 "
        "블로그의 비슷한 글을 유사문서로 보고 노출을 낮춥니다]\n" + "\n".join(lines)
    ]


def seo_block(brand_kit: dict, hint: str = content_mode.HINT_RELEVANCE) -> List[str]:
    """Keyword pressure on the draft, at the level the content mode asks for.

    This is the pipeline's single creative call, and it used to be told to
    "pick 2~3 keywords and place each 2~4 times". That quota, not the later
    density pass, is what made every post converge: the pool's centre of
    gravity is the gift keywords, so a post about a teacher-training course
    was steered into 답례품 copy before a single SEO stage had run. Measured
    over one batch, the memo's own subject occupied 0~31% of the finished
    text, and every post landed on exactly the enforced density floor.

    What downstream still needs from this call is *which keywords the topic
    genuinely touches* — `select_target_keywords` reads that off the draft.
    A model free to use a keyword once, or not at all, gives a far more
    honest signal than one filling a quota. Enforcement stays where it can
    be measured and undone: `seo_optimizer`.
    """
    from ai_workers import seo_optimizer

    # 브랜드 어휘는 아예 보여주지 않습니다. 목록에 있으면 모델은 그 글의 주제와
    # 상관없이 그것부터 집는데(자기 브랜드 이야기가 항상 '맞는' 것처럼 보이니까),
    # 그 단어들은 용어집과 core_facts를 통해 어차피 본문에 들어갑니다.
    #
    # 순서도 무작위로 두지 않습니다. 모델은 목록 앞쪽을 먼저 고르는 경향이 있고,
    # 하류의 타깃 선정이 이미 '기회 × 전환 가중치' 순으로 고르므로, 보여주는
    # 순서를 같은 기준으로 맞춰 두 단계가 서로 다른 키워드를 밀지 않게 합니다.
    keywords = seo_optimizer.competing_keywords(
        brand_kit.get("seo_keywords") or [], brand_kit.get("non_target_keywords")
    )
    weights = brand_kit.get("keyword_weights")
    keywords = sorted(keywords, key=lambda kw: -seo_optimizer._opportunity(kw, weights))
    if not keywords or hint == content_mode.HINT_NONE:
        # '내용 우선' never shows the pool at all. Naming keywords and then
        # saying "only if they fit" still anchors the draft toward them; the
        # only way to fully remove that pull is to not mention them.
        return []

    if hint == content_mode.HINT_PLACEMENT:
        return [
            "[네이버 블로그 SEO 지침] 이 글은 검색 노출을 우선 목표로 합니다. "
            "아래 키워드 후보 중 이 글의 주제에 맞는 2~3개를 골라, 제목과 본문에 걸쳐 "
            "각각 2~4회 자연스럽게 배치하세요. 다만 키워드를 넣으려고 글의 소재 자체를 "
            "바꾸지는 마세요 — 소재는 그대로 두고 표현을 키워드 쪽으로 맞추는 것입니다.\n"
            "키워드 후보: " + ", ".join(keywords)
        ]

    return [
        "[검색 키워드 참고] 이 글은 네이버 블로그 검색 노출도 고려합니다. 아래는 회사가 "
        "노리는 키워드 목록입니다. **이 글의 소재와 실제로 맞는 키워드가 있을 때만** "
        "본문에 자연스럽게 쓰고, 맞지 않으면 하나도 쓰지 마세요. 횟수를 채우려 하지 말고, "
        "글의 흐름이 그 단어를 필요로 하는 자리에만 쓰세요. 소재와 무관한 키워드를 끌어들이려고 "
        "글의 주제를 바꾸는 것은 절대 금지입니다.\n"
        "키워드 목록: " + ", ".join(keywords)
    ]


def length_block(length_range) -> List[str]:
    """The body-length target and, more importantly, what fills it.

    A target alone gets met with brand padding (company history, 인증 이력);
    a sentence-length rule alone (톤앤매너 "한 문장 40자") shortens the whole
    post. So the block asks for *more sentences, each short*, and names what
    they should be about: the things a reader who has never seen the product
    needs — the same reader the photos can't serve on their own.
    """
    if not length_range:
        return []
    low, high = length_range
    return [
        f"[분량] 본문은 **공백 제외 {low:,}~{high:,}자**로 쓰세요. 문장은 [톤앤매너 가이드]대로 "
        "짧게 끊고, 분량은 문장 수를 늘려서 채웁니다.\n"
        "이 제품·소재를 **처음 보는 독자**도 이해할 수 있게 쓰세요. 사진만 보고는 알 수 없는 "
        "것 — 무엇인지, 무엇으로 어떻게 만들었는지, 크기·구성·쓰임새, 어떤 사람·상황에 "
        "맞는지, 받는 사람이 느낄 점, 주문·이용 방법 — 중 제공된 자료에 있는 것을 구체적으로 "
        "풀어 쓰세요.\n"
        "분량을 회사 소개, 브랜드 철학, 수상·인증 이력, 같은 말의 반복으로 채우지 마세요. "
        "제공된 자료에 없는 사실을 지어내서 늘리는 것은 금지입니다 — 자료가 부족하면 "
        "분량보다 정확성이 먼저입니다."
    ]


def notice_block(notice_fields: dict | None, product_fields: dict | None = None) -> List[str]:
    """이 글이 담아야 할 사실들 — 공지 정보와 제품·주문 정보.

    Placed with the source memo rather than in the system prompt: these are
    this post's subject, not standing brand configuration.

    The prohibition is the same one core_facts already carries, aimed at the
    other half of the post. A model asked to write a 모집 공고 with no fee in
    front of it will supply a plausible one — and a 수강료 invented by an LLM
    is a number the company then has to honour or retract. The same pull
    applies to shape, not just numbers: given a 추석 연휴 안내 carrying only a
    date, a model that has seen a thousand 모집 공고 will happily add a 신청
    방법 nobody asked for, so the absent fields are named and refused
    explicitly rather than just left out.
    """
    from ai_workers import factsheet

    blocks: List[str] = []
    for sheet, given, headline in (
        (factsheet.NOTICE, notice_fields, "[공지 정보 — 이 글은 공지 글입니다]"),
        (factsheet.PRODUCT, product_fields,
         "[제품·주문 정보 — 사려고 검색해서 들어온 독자가 확인해야 할 것들입니다]"),
    ):
        present = factsheet.clean(sheet, given)
        if not present:
            continue

        lines = "\n".join(f"- {sheet.labels[key]}: {value}" for key, value in present.items())
        absent = [label for key, label in sheet.labels.items() if key not in present]

        block = (
            f"{headline}\n"
            f"{lines}\n\n"
            "위 항목은 독자가 이 글을 읽는 이유입니다. **하나도 빠뜨리지 말고, 숫자와 "
            "날짜를 바꾸지 말고** 본문에 그대로 담으세요. 분위기 묘사로 시작하더라도 "
            "이 사실들이 본문 안에 분명히 자리 잡아야 합니다."
        )
        if absent:
            block += (
                "\n\n위에 적히지 않은 항목(" + ", ".join(absent) + ")은 이 글에 "
                "해당하지 않거나 아직 정해지지 않은 것입니다. **절대 지어내지 마세요.** "
                "그럴듯한 날짜·금액·수량·기간을 만들어 넣는 것은 회사가 지키지 못할 약속을 "
                "발행하는 것과 같습니다. 해당 없는 항목은 아예 언급하지 마세요 — "
                "연휴 안내에 신청 방법을, 휴무 안내에 정원을 끼워 넣지 마세요. "
                "독자가 더 알아야 할 것이 있으면 '자세한 내용은 문의해 주세요' 정도로만 "
                "넘기세요."
            )
        blocks.append(block)

    # 분량 목표를 걷어내는 것만으로는 부족합니다. 목표가 없어도 모델은 블로그
    # 글다운 길이를 맞추려고 브랜드 소개를 끌어옵니다.
    #
    # 판단은 두 시트를 합친 기준(is_brief_overall)으로 한 번만 합니다 — 분량
    # 목표를 걷어낼지 정하는 content_writer와 같은 기준입니다. 예전엔 시트마다
    # is_brief를 따로 봐서, 공지 2개 + 제품 5개인 글에 목표 분량은 그대로 둔 채
    # 공지 블록에만 "짧게 쓰세요"가 붙어 모델이 서로 반대인 지시를 받았습니다.
    if factsheet.is_brief_overall(
        [(factsheet.NOTICE, notice_fields), (factsheet.PRODUCT, product_fields)]
    ):
        blocks.append(
            "[분량] 이 글은 알릴 사실이 적습니다. **짧게 쓰세요.** 회사 소개, "
            "제품 라인업, 수상·인증 이력으로 분량을 늘리지 마세요. 읽는 사람이 알아야 "
            "할 것은 위 사실과 그에 대한 짧은 안내뿐이고, 그것을 다 전했으면 글은 "
            "거기서 끝나는 것이 맞습니다."
        )
    return blocks


SUBJECT_INSTRUCTION = (
    "**이 소재가 글의 중심입니다.** 독자가 이 글에서 가장 먼저, 가장 많이 알게 되어야 하는 "
    "것은 위 소재의 구체적인 내용입니다. 회사 소개·브랜드 철학·제품 라인업·환경 가치는 "
    "이 소재를 설명하는 데 필요한 만큼만 곁들이세요. 소재를 한두 문장으로 스치고 일반적인 "
    "브랜드 소개로 넘어가는 글은 실패한 글입니다. 소재가 교육·강의라면 교육 이야기를, "
    "행사라면 행사 이야기를 하세요 — 제품 판매 글로 바꾸지 마세요."
)


def brand_voice_blocks(brand_kit: dict) -> List[str]:
    """persona + tone + glossary + certification scope — what the Instagram,
    X and Shorts writers get. They deliberately don't get the full fact list
    (a caption isn't a fact sheet), but without the certification facts they
    knew a certification existed and not which items hold it."""
    from ai_workers.guardrail import certification_block

    return persona_block(brand_kit) + terminology_block(brand_kit) + certification_block(brand_kit)


BLOG_SYSTEM_PROMPT_BASE = (
    "당신은 네이버 블로그 독자를 겨냥한 마케팅 카피라이터입니다. 제공된 자료를 바탕으로 "
    "첫 문단에서 독자를 붙잡는 정보성 블로그 포스트 본문을 한국어로 작성하세요. "
    "과장되거나 근거 없는 주장은 절대 쓰지 마세요. 제공된 자료에 없는 수치·인증·수상 이력을 "
    "지어내지 마세요. 마크다운 제목 기호(#)는 쓰지 말고, 문단으로만 구성하세요."
)


def build_blog_system_prompt(brand_kit: dict, mode: dict | None = None) -> str:
    """Full Naver-blog system prompt: base + persona + facts + few-shot +
    glossary + (mode-dependent) keyword pressure and length target."""
    profile = mode or content_mode.resolve(None, brand_kit)
    parts = [BLOG_SYSTEM_PROMPT_BASE]
    parts += persona_block(brand_kit)
    parts += core_facts_block(brand_kit)
    parts += few_shot_block(brand_kit)
    parts += terminology_block(brand_kit)
    parts += seo_block(brand_kit, profile.get("hint", content_mode.HINT_RELEVANCE))
    parts += length_block(profile.get("length_range"))
    return "\n\n".join(parts)


def photo_instruction(captions: dict) -> str:
    """The `[IMAGE:]` placement instruction, shared by every pipeline.

    The worked example uses a real path from *this* call's attachment list
    rather than an abstract placeholder: models were observed echoing a
    `<경로>`-style metavariable back literally, producing `[IMAGE: 경로]` in
    the draft instead of substituting the actual path.
    """
    if not captions:
        return "첨부된 사진이 없으므로 `[IMAGE:]` 태그를 삽입하지 마세요."

    example_path = next(iter(captions))
    return (
        f"**중요: 위 목록의 사진 {len(captions)}장을 전부 빠짐없이 사용해야 합니다.** "
        "각 사진의 설명을 읽고, 그 내용과 가장 잘 맞는 본문 위치에 `[IMAGE: 경로]` 형식의 "
        "태그를 한 줄로 삽입하세요. '경로' 자리에는 위 목록에 적힌 실제 경로 문자열을 그대로 "
        "옮겨 적어야 하며, '경로'라는 글자 자체를 쓰면 절대 안 됩니다. 예를 들어 첫 번째 사진의 "
        f"경로가 정확히 {example_path} 이므로, 그 사진을 쓸 자리에는 반드시 "
        f"[IMAGE: {example_path}] 라고 그대로 적어야 합니다. 경로를 요약하거나 다른 글자로 "
        "바꾸지 마세요. 본문이 짧다면 문단을 늘려서라도 모든 사진이 들어갈 자리를 만드세요. "
        "**사진만 이어 붙이지 마세요.** 각 사진 앞이나 뒤에 그 사진이 보여주는 것 — 무엇이고, "
        "어디를 보면 되고, 왜 의미가 있는지 — 를 설명하는 문장을 2~3개씩 두세요. 제품을 잘 "
        "모르는 독자는 사진이 아니라 이 설명을 읽고 이해합니다."
    )


def photo_context(captions: dict) -> str:
    if not captions:
        return "(첨부된 사진 없음)"
    return "\n".join(f"- [IMAGE: {path}] 설명: {caption}" for path, caption in captions.items())
