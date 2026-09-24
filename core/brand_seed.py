"""㈜더스티치 / 더봄봄 Brand Kit — seeded from company_info/.

Everything here is lifted from the company's own documents rather than
invented, so the generated drafts cite facts the company can actually stand
behind:

- company_info/더스티치_한복새활용_공공구매 제품제안서_한국환경산업기술원.pdf
  (brand etymology, concept, target, item-level price zones, certifications)
- company_info/더스티치_성장_2026년- 새활용 산업 육성 지원사업 신청서.pdf
  (company profile, 2026 product roadmap, sourcing model, market rationale)
- company_info/26년-찾아가는-가치소비-기획전... .pdf (B2G/B2B product lines)
- company_info/유플러스_수세미_교안 자료 [복구].pdf (education track, 대표 경력)
- company_info/sns_info.txt (channel handles)

One deliberate departure from OSMU_admin's Brand Kit: the compliance
guardrail there was written for 의료법/표시광고법 (a healthcare client). 더스티치
sells upcycled goods and environmental education, so its real legal exposure
is **환경성 표시·광고** — greenwashing claims under 「환경기술 및 환경산업 지원법」
and 표시·광고의 공정화에 관한 법률. The blacklist and the auditor prompt
(ai_workers/guardrail.py) are rebuilt around that instead.

`seed_if_empty()` runs once on first launch; after that the Brand Kit page is
the source of truth and this file is never re-applied, so admin edits are
never clobbered.
"""
from __future__ import annotations

from core import repo

# --- visual identity --------------------------------------------------------
# 한복 오방색에서 뽑은 더봄봄 팔레트. Flet 테마(flet_app/theme.py)가 이 값을 그대로 씁니다.
BRAND_COLORS = {
    "primary": "#A6224B",      # 자주 — 저고리 고름
    "primary_dark": "#7C1738",
    "secondary": "#2B4C8C",    # 쪽빛
    "accent": "#C9A227",       # 금박
    "mint": "#86C1B3",         # 옥색
    "bg": "#FBF7F2",           # 한지
    "text": "#2B2118",
    "text_muted": "#8A7B6B",
}

BRAND_NAME = "주식회사 더스티치"
SUB_BRAND = "더봄봄"
HOMEPAGE = "http://thestitchstore.co.kr/"
NAVER_BLOG_ID = "migoi1"
INSTAGRAM_HANDLE = "thestitch_artplay"
INDUSTRY = "한복 새활용(업사이클링) 패션소품 제조 · 친환경 미술공예 교육서비스 (사회적기업)"

PERSONA = (
    "20년 차 패션디자이너이자 10년 넘게 친환경 공예를 가르쳐 온 공방 선생님의 목소리로 이야기합니다. "
    "가장 행복한 날 입었던 한복이 종량제 봉투에 담겨 버려지는 것을 안타까워하는 마음에서 출발해, "
    "그 원단이 어떤 손을 거쳐 다시 쓰이는지를 만든 사람의 시선으로 설명합니다. "
    "환경을 앞세워 가르치려 들지 않고, '예뻐서 골랐는데 알고 보니 의미까지 있더라'는 순서로 풀어냅니다. "
    "작가의 감성과 살림하는 사람의 실용 감각을 동시에 갖춘, 다정하지만 전문적인 화자입니다."
)

TONE_AND_MANNER = """- 존댓말을 씁니다. '~합니다'와 '~해요'를 자연스럽게 섞습니다.
- 한 문장은 40자 안팎에서 끊고, 한 문단은 2~3문장까지만 씁니다. 같은 뜻을 두 번 말하지 않습니다.
- 환경 이야기를 첫 문단에 꺼내지 않습니다. 색감·질감·쓰임새를 먼저 말하고, 새활용 가치는 그 뒤에 붙입니다.
- '~해야 합니다' 같은 계몽조·훈계조 표현을 쓰지 않습니다. 죄책감을 자극하지 않습니다.
- 수치와 인증은 확인된 것만 씁니다. 근거 없는 환경 효과(탄소 몇 kg 절감 등)는 절대 쓰지 않습니다.
- 이모지는 한 문단에 최대 1개, 느낌표는 꼭 필요한 곳에만 씁니다.
- 가격은 '싸다/저렴하다' 대신 '부담 없는', '일상형 럭셔리'라는 브랜드 언어를 씁니다.
- 한복은 '낡은/헌' 대신 '기부받은', '가장 행복한 날 입었던'으로 부릅니다.
- 매 글 끝에 한복 기부나 체험으로 이어지는 부드러운 한 문장을 남깁니다."""

CORE_FACTS = [
    "가장 행복한 날 입었던 한복을 기부받아 새활용하는 브랜드 '더봄봄'을 운영합니다 — 더(Plus)+봄(春, 행복)+봄(再生).",
    "한복은 일반 재활용으로 분리배출되지 못해 종량제 봉투로 버려지지만, 실크·물실크·자카드·자수 등 최고급 소재로 만들어져 있습니다.",
    "2025년 한국환경산업협회 새활용제품 인증 4건을 보유하고 있습니다 (행복인형, 더봄봄 향낭, 더봄봄 스크런치, 더봄봄 짱리본핀).",
    "2023년과 2024년 환경부·한국환경산업협회 새활용산업 육성지원사업에 연속 선정된 기업입니다.",
    "패션디자이너 경력 20년의 손미경 대표와 화가·공예작가·웹디자이너·사진작가 등 시각예술작가들이 함께 만드는 수공예 브랜드입니다.",
    "2017년 9월 설립, 2020년 사회적기업 인증 — 교육격차 해소와 예술작가 일자리 만들기가 소셜미션입니다.",
    "2025년 한복 새활용 연구전담부서를 신설하고 전용 작업장을 마련해 제품 개발에 집중하고 있습니다.",
    "한복은 저가 매입이 아니라 기부받은 뒤 새활용 리워드를 돌려드리는 방식으로 수급합니다 (2024년 기부 한복 약 200벌).",
    "행복인형 만들기 키트에는 설명서에 제작 동영상 QR이 들어 있어, 배포 후 가정에서도 이어서 만들어 볼 수 있습니다.",
    "환경·ESG 캠페인 기념품, 기관·기업 행사 답례품으로 B2G·B2B 납품 경험이 있으며 기관 맞춤 라벨과 패키지 제작이 가능합니다.",
    "2026년 3월 텀블벅에서 '한복 새활용 K-행복인형 키링' 크라우드펀딩을 성공적으로 마쳤습니다.",
    "아동미술 1,000회, 어르신미술 500회, 기업·기관 강의 300회 이상의 친환경 미술공예 교육을 진행해 왔습니다.",
    "2026년에는 한복 새활용 K-펫 패션 10종과 금사 자수·금박을 활용한 ESG 금빛 라이프 굿즈 5종을 개발하고 있습니다.",
    "더봄봄의 핵심 가격존은 1만~3만 원대(리본핀·스크런치·행복인형)이며, 5천~1만 원대 엔트리와 3만~5만 원대 포인트 라인이 있습니다.",
    "2025년 성수동 팝업스토어 2회, 인사동 팝업스토어, 현대백화점 킨텍스 에코페스타 등에서 제품 전시와 새활용 체험을 진행했습니다.",
]

TERMINOLOGY = {
    "더스티치": "주식회사 더스티치. 2017년 설립된 친환경 미술공예 사회적기업. 영문은 The Stitch. '더 스티치'로 띄어 쓰지 않습니다.",
    "더봄봄": "더스티치의 한복 업사이클링 전문 브랜드. '더(Plus)+봄(春, 행복)+봄(再生)'의 합성어로, 항상 붙여 씁니다.",
    "새활용": "업사이클링(upcycling)의 우리말 표기. 단순 재활용(recycling)과 반드시 구분해서 사용합니다.",
    "행복인형": "한복 원단과 전통 매듭을 결합한 더봄봄 대표 백참 인형. 새활용제품인증 제품. 소비자가 10,000~25,000원.",
    "행복인형 미니미": "작은 매듭인형에 스커트를 입힌 위트 있는 키링 사이즈 공예품. 소비자가 5,000~7,000원.",
    "향낭": "비치는 한복 소재 주머니에 계피·팔각 등 천연 향재를 넣어 만든 백참. 소비자가 5,000~10,000원.",
    "향기인형": "풍성한 스커트 안에 계피를 넣어 만든 인테리어 소품 인형. 소비자가 35,000원.",
    "짱리본핀": "한복의 컬러감을 살린 대·중·소 사이즈 리본 헤어핀. 새활용제품인증 제품. 소비자가 5,000~19,000원.",
    "스크런치": "한복 실크의 색감을 살린 헤어 곱창밴드. 새활용제품인증 제품. 소비자가 7,000~19,000원.",
    "새활용제품인증": "한국환경산업협회가 부여하는 공식 인증. '친환경 인증', '환경마크', 'GR 인증' 등 다른 제도와 혼용하지 않습니다.",
    "금빛 라이프 굿즈": "금사 자수·금박·흉배 등 전통 금빛 문양을 적용한 더봄봄 ESG 기념품 라인 (사각백, 토트백, 파우치, 여권케이스, 네임텍).",
    "K-펫 패션": "한복 새활용 원단으로 만든 반려동물 패션·액세서리 라인. 2026년 신규 개발 중.",
    "펫 휴머니제이션": "반려동물을 가족처럼 여기는 소비 문화. 펫 패션 수요 증가의 배경으로 설명할 때 씁니다.",
    "가치소비": "가격이나 기능이 아니라 제품에 담긴 의미·스토리를 기준으로 구매하는 소비 방식.",
}

SEO_KEYWORDS = [
    "한복 새활용",
    "한복 업사이클링",
    "더봄봄",
    "새활용제품인증",
    "업사이클링 소품",
    "ESG 기념품",
    "친환경 굿즈",
    "새활용 체험",
    "한복 기부",
    "친환경 공예 키트",
]

# 환경성 표시·광고 관련 규정 + 표시광고법 기준의 결정론적 치환 사전.
# 여기 등록된 표현은 LLM이 무엇을 쓰든 발행 전 100% 자동 치환됩니다.
#
# 치환어를 고를 때의 원칙: **원래 표현과 같은 품사·활용형**을 씁니다. 한국어는
# 조사가 뒤에 붙기 때문에, 관형형('~한/~는')을 명사구로 바꾸면 "국내 최초로"가
# "국내에서도 보기 드문로" 같은 비문이 됩니다. 그래서
#   - 관형어 → 관형어 ("완벽한" → "세심한")
#   - 명사 → 명사 ("친환경 인증" → "새활용제품인증")
#   - 부사 → 부사 ("영구적으로" → "오래도록")
# 로 맞추고, 조사가 붙는 형태('~로')는 별도 항목으로 함께 등록합니다.
# (긴 항목이 먼저 매칭되도록 하는 처리는 ai_workers/guardrail.py에 있습니다.)
BLACKLIST_MAP = {
    # --- 포괄적·절대적 환경성 주장 ---
    "100% 친환경": "친환경적",
    "완전 친환경": "친환경적",
    "100% 자연": "자연 소재 중심",
    "천연 100%": "천연 소재 중심",
    "100% 재활용": "재활용 소재 중심",
    "지구를 살리는": "환경 부담을 줄이는",
    "자연으로 돌아가는": "오래 쓸 수 있는",
    "환경을 완벽하게": "환경 부담을 최대한",
    # --- 검증 불가능한 성분·안전성 주장 ---
    "완전 무해": "자극이 적은",
    "무해한": "자극이 적은",
    "무독성": "유해물질 저감",
    "인체에 전혀": "인체에 비교적",
    # --- 미보유 인증·제도의 암시 ---
    "친환경 인증": "새활용제품인증",
    "환경마크 인증": "새활용제품인증",
    # --- 새활용(upcycling)을 재활용(recycling)으로 오표기 ---
    # 용어집이 "단순 재활용과 반드시 구분"이라고 못박고 있는데도 3차 테스트에서
    # 「한복 리사이클링의 미학」이라는 제목이 나왔습니다. 업사이클링 제품을 한
    # 단계 낮은 재활용으로 표시하는 것이라 브랜드 문제이자 표시 문제입니다.
    # '업사이클링'은 새활용의 원어라 정확한 표현이고, SEO 키워드('한복
    # 업사이클링')이기도 하므로 치환 대상이 아닙니다 — 치환하면 그 키워드는
    # 밀도를 영원히 채우지 못하게 됩니다.
    # 긴 항목부터 치환되므로 '리사이클링'이 '리사이클'보다 먼저 걸립니다.
    "리사이클링": "새활용",
    "리싸이클링": "새활용",
    "리사이클": "새활용",
    "탄소중립 제품": "탄소 배출 저감 제품",
    "제로웨이스트 제품": "폐기물 저감 제품",
    # --- 최상급·배타적 표현 (표시광고법) ---
    "국내 최초로": "국내에서도 보기 드물게",
    "세계 최초로": "해외에서도 보기 드물게",
    "국내 최초": "국내에서도 보기 드문",
    "세계 최초": "해외에서도 보기 드문",
    "업계 최고의": "업계에서 손꼽히는",
    "업계 최고": "업계에서 손꼽히는",
    "최고급": "고급",
    "유일한": "흔치 않은",
    # 수공예 브랜드가 가장 쓰고 싶어 하는 말이고, 실제 초안에서 나왔습니다.
    # 긴 항목이 먼저 치환되므로 '세상에 하나뿐인'이 '하나뿐인'보다 먼저 걸립니다.
    "세상에 하나뿐인": "저마다 다른",
    # '세상에 단 하나뿐인'은 위 두 항목 어느 것과도 통째로 맞지 않아 '하나뿐인'만
    # 바뀌었고, "세상에 단 저마다 다른"이라는 비문이 인스타 캡션에 실렸습니다.
    "세상에 단 하나뿐인": "저마다 다른",
    "단 하나뿐인": "저마다 다른",
    "하나뿐인": "저마다 다른",
    "완벽한": "세심한",
    "영구적으로": "오래도록",
    "영구적인": "오래 쓸 수 있는",
}

FEW_SHOT_SAMPLES = [
    """옷장 맨 위 칸, 상자째 올려둔 한복이 하나쯤은 있으실 거예요.

결혼식 날 딱 한 번 입고, 그다음부터는 꺼내지 못한 채로요. 버리자니 그날의 기억이 걸리고, 두자니 자리만 차지하고요.

저희가 그 한복을 기부받습니다. 그리고 원단을 색깔별로 나누고, 세탁하고, 다림질해서 다시 씁니다.

한복 원단은 생각보다 훨씬 좋은 소재예요. 실크, 물실크, 자카드에 자수까지 들어가 있죠. 요즘 시중에서 이만한 원단을 만나기가 쉽지 않습니다. 그런데 한복은 재활용 분리배출이 되지 않아서, 대부분 종량제 봉투에 담겨 나갑니다.

이번에 나온 짱리본핀은 그중에서도 색이 제일 예뻤던 저고리 원단으로 만들었어요. 어두운 코트 위에 하나 얹으면 얼굴이 환해지는, 딱 그런 색입니다. 크기는 대·중·소 세 가지라 머리숱이나 옷차림에 맞춰 고르실 수 있어요.

가격은 5,000원부터 19,000원까지입니다. 매일 쓰는 물건인데 부담 없이 고를 수 있는 선이면 좋겠다고 생각했습니다.

참, 이 제품은 한국환경산업협회 새활용제품인증을 받았습니다. 예뻐서 골랐는데 알고 보니 그런 의미까지 있었다면, 그게 저희가 바라는 순서예요.

혹시 옷장에 잠들어 있는 한복이 있으시다면, 기부해 주세요. 새활용된 제품으로 다시 돌려드립니다."""
]


def seed_if_empty() -> bool:
    """Populates the Brand Kit on first launch only. Returns True if seeded."""
    existing = repo.get_brand_kit()
    if existing.get("brand_name") or existing.get("persona"):
        return False

    repo.save_brand_kit(
        brand_name=BRAND_NAME,
        sub_brand=SUB_BRAND,
        industry=INDUSTRY,
        homepage=HOMEPAGE,
        naver_blog_id=NAVER_BLOG_ID,
        instagram_handle=INSTAGRAM_HANDLE,
        persona=PERSONA,
        tone_and_manner=TONE_AND_MANNER,
        core_facts=CORE_FACTS,
        terminology=TERMINOLOGY,
        seo_keywords=SEO_KEYWORDS,
        blacklist_map=BLACKLIST_MAP,
        few_shot_samples=FEW_SHOT_SAMPLES,
        guardrail_enabled=True,
        vision_enabled=True,
        vision_quality="economy",
    )
    return True


# One-time corrections to text an earlier version of this file seeded into
# existing Brand Kits. seed_if_empty() never touches a kit that already
# exists, so a fix to a seeded default would otherwise reach new installs
# only. Each (old, new) pair is applied once, and only where `old` is still
# present verbatim — i.e. the admin never edited that line — so admin edits
# are still never clobbered. For a new company this list can simply be empty.
#
# The seeded "문장은 2~3줄 안에서 끊습니다" permits 100+ character sentences,
# so drafts stayed long-winded however often a marketer asked for shorter
# sentences in a one-off revision (customer report). It becomes a measurable
# limit.
SEED_FIXES = [
    ("- 존댓말을 씁니다. '~합니다'와 '~해요'를 자연스럽게 섞되, 문장은 2~3줄 안에서 끊습니다.",
     "- 존댓말을 씁니다. '~합니다'와 '~해요'를 자연스럽게 섞습니다.\n- 한 문장은 40자 안팎에서 끊고, 한 문단은 2~3문장까지만 씁니다. 같은 뜻을 두 번 말하지 않습니다."),
]
_SEED_FIXES_STATE_KEY = "brand_seed_fixes_applied"

# Same idea for the banned-term dictionary: entries added to BLACKLIST_MAP
# after a kit was seeded. Each key is added once, and only if the admin's
# dictionary doesn't already have it — an existing entry (with whatever
# replacement the admin chose) always wins. Empty for a new company.
SEED_BLACKLIST_ADDITIONS = {
    "세상에 단 하나뿐인": "저마다 다른",
    "단 하나뿐인": "저마다 다른",
}


def apply_seed_fixes() -> int:
    """Applies SEED_FIXES to the saved tone guide and SEED_BLACKLIST_ADDITIONS
    to the saved dictionary. Returns how many changes were made."""
    done = set((repo.get_app_state(_SEED_FIXES_STATE_KEY) or {}).get("done") or [])
    tone = repo.get_brand_kit().get("tone_and_manner") or ""
    applied = 0
    for old, new in SEED_FIXES:
        if old in done:
            continue
        if old in tone:
            tone = tone.replace(old, new)
            applied += 1
        done.add(old)
    if applied:
        repo.save_brand_kit(tone_and_manner=tone)

    blacklist = dict(repo.get_brand_kit().get("blacklist_map") or {})
    added = 0
    for term, replacement in SEED_BLACKLIST_ADDITIONS.items():
        marker = f"blacklist:{term}"
        if marker in done:
            continue
        if term not in blacklist:
            blacklist[term] = replacement
            added += 1
        done.add(marker)
    if added:
        repo.save_brand_kit(blacklist_map=blacklist)

    repo.set_app_state(_SEED_FIXES_STATE_KEY, {"done": sorted(done)})
    return applied + added
