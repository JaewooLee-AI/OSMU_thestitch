"""SQLite connection + schema for the unified OSMU engine.

Replaces OSMU_admin's Supabase client (core/db_client.py) and OSMU_web's
Supabase JS client. The whole point of folding both apps into one Streamlit
process is that there is no longer a Vercel 10s serverless timeout to work
around, and therefore no reason to keep a hosted Postgres just to act as an
async message queue between two runtimes — a single local file does the job
with zero network latency, zero free-tier storage ceiling, and no RLS/service
-role key handling.

Everything that was a Postgres `jsonb`/`text[]` column is stored here as a
JSON-encoded TEXT column; `core.repo` is the only module that knows that, so
callers still get real Python lists/dicts back.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _default_data_dir() -> Path:
    """Where data/ lives when OSMU_DATA_DIR isn't set.

    A packaged desktop app's install location (e.g. Program Files) may not be
    writable, so a fresh install needs a per-user OS data folder instead of
    PROJECT_ROOT/data. But this repo also has a live Streamlit install whose
    data/ already exists next to the project — for that install, switching
    the default out from under it would make its data look like it vanished.
    So: keep using PROJECT_ROOT/data if it's already there (existing installs
    untouched), and only fall back to a per-user folder for a brand new one.
    """
    existing = PROJECT_ROOT / "data"
    if existing.exists():
        return existing

    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "OSMU_THESTITCH"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OSMU_THESTITCH"
    return Path.home() / ".local" / "share" / "OSMU_THESTITCH"


DATA_DIR = Path(os.environ["OSMU_DATA_DIR"]) if os.environ.get("OSMU_DATA_DIR") else _default_data_dir()
DB_PATH = DATA_DIR / "osmu.db"
ASSETS_DIR = DATA_DIR / "assets"

_INIT_LOCK = threading.Lock()
_initialized = False

SCHEMA = """
-- LLM vendor registry. Mirrors OSMU_admin's `admin_settings`; API keys stay
-- AES-256-GCM encrypted at rest even though the DB is local, because the
-- .db file is far easier to accidentally copy/sync than a Postgres row was.
create table if not exists llm_settings (
    vendor              text primary key,
    model_name          text not null,
    encrypted_api_key   text not null,
    is_active           integer not null default 1,
    updated_at          text not null default (datetime('now'))
);

-- Single-row brand configuration (persona/tone/facts/glossary/compliance).
create table if not exists brand_kit (
    id                        integer primary key check (id = 1),
    brand_name                text,
    sub_brand                 text,
    industry                  text,
    homepage                  text,
    persona                   text,
    tone_and_manner           text,
    guardrail_enabled         integer not null default 1,
    naver_blog_id             text,
    instagram_handle          text,
    core_facts                text not null default '[]',
    few_shot_samples          text not null default '[]',
    terminology               text not null default '{}',
    seo_keywords              text not null default '[]',
    -- keyword -> conversion weight. Keeps "how much traffic can we win" and
    -- "what is that traffic worth to us" as separate inputs: the first is
    -- measurable from Naver, the second only the brand knows. Absent keys
    -- default to 1.0, so an empty map reproduces pure traffic ranking.
    keyword_weights           text not null default '{}',
    -- 본문에는 쓰되 제목은 맡기지 않을 키워드. 브랜드 어휘(더봄봄, 한복 새활용)가
    -- 여기 들어갑니다 — 검색 수요가 없어 제목을 맡기면 노출을 버리는 셈이지만,
    -- 글에는 계속 등장해야 하는 단어라 풀에서 뺄 수도 없습니다. 가중치로 누르지
    -- 않는 이유는 가중치가 '이 유입의 사업적 가치'라는 다른 뜻이기 때문입니다.
    non_target_keywords   text not null default '[]',
    -- Fallback for campaigns.content_mode — see ai_workers/content_mode.py.
    default_content_mode      text not null default 'balanced',
    blacklist_map             text not null default '{}',
    default_generation_vendor text,
    vision_vendor             text,
    vision_quality            text not null default 'economy',
    vision_enabled            integer not null default 1,
    updated_at                text not null default (datetime('now'))
);

-- One row per piece of content, from source selection through publish.
create table if not exists campaigns (
    id                  text primary key,
    source_type         text not null check (source_type in ('news', 'manual')),
    source_url          text,
    -- The original news headline, kept for reference only. It is deliberately NOT
    -- the post's `title`: Naver treats blog/cafe/news as one duplicate-
    -- detection space, so reusing a publisher's headline gets the post
    -- filtered out as a 유사문서 in favour of the original article.
    source_title        text,
    title               text,
    content             text,
    memo                text,
    status              text not null default 'awaiting_media'
        check (status in ('awaiting_media', 'queued', 'processing', 'draft',
                          'ready_to_publish', 'published', 'failed')),
    -- 'rich' | 'balanced' | 'seo'; null = follow brand_kit.default_content_mode.
    -- Deliberately not CHECK-constrained: ai_workers/content_mode.py falls
    -- back to the default for anything it doesn't recognise, and a rejected
    -- INSERT would be a worse failure than an unknown mode.
    content_mode        text,
    -- 공지·모집 글의 일시/장소/비용/신청 방법 (JSON 오브젝트, 빈 값은 저장하지
    -- 않음). 비어 있으면 일반 글입니다 — 별도 유형 플래그를 두지 않는 이유는
    -- ai_workers/factsheet.py 참고.
    notice_fields       text not null default '{}',
    -- 답례품·굿즈 글의 가격대/최소수량/제작기간/규격/구매처. 같은 구조이고 같은
    -- 이유로 존재합니다 — 검색해서 들어온 사람이 답을 못 찾으면 이탈하고, 그
    -- 이탈이 노출 순위를 도로 깎습니다.
    product_fields      text not null default '{}',
    storage_file_paths  text not null default '[]',
    guardrail_passed    integer,
    guardrail_report    text,
    instagram_caption   text,
    instagram_hashtags  text not null default '[]',
    x_content           text not null default '[]',
    x_hashtags          text not null default '[]',
    naver_hashtags      text not null default '[]',
    shorts_script       text,
    publish_error       text,
    created_at          text not null default (datetime('now')),
    updated_at          text not null default (datetime('now'))
);

create index if not exists campaigns_status_idx on campaigns (status);
create index if not exists campaigns_source_url_idx on campaigns (source_url);

-- Uploaded photos. `rel_path` is relative to data/assets and is what gets
-- written into the draft as `[IMAGE: rel_path]`, exactly like the Supabase
-- storage path used to be — so photo_placement.py needed no changes.
create table if not exists assets (
    id          text primary key,
    rel_path    text not null unique,
    filename    text,
    sha256      text,
    width       integer,
    height      integer,
    byte_size   integer,
    created_at  text not null default (datetime('now'))
);

create index if not exists assets_sha256_idx on assets (sha256);

-- Vision caption cache — the single biggest token saver. Keyed by the
-- *content* hash of the original photo, so the same product shot reused
-- across many posts is analyzed exactly once, ever.
create table if not exists vision_cache (
    image_sha256    text not null,
    prompt_version  text not null,
    quality         text not null,
    caption         text not null,
    created_at      text not null default (datetime('now')),
    primary key (image_sha256, prompt_version, quality)
);

-- Every title this workbench has ever produced. Deliberately *not* a column
-- on campaigns and deliberately without a foreign key: the marketer keeps
-- only the last few campaigns and deletes the rest, but a deleted post was
-- still published, and its title still needs to be avoided next time. Tying
-- this to campaigns.id would make deletion silently reopen the exact
-- repetition this table exists to prevent.
-- `campaign_id` is a plain tag, not a foreign key: it exists only so that
-- regenerating a campaign replaces its own entry instead of appending a new
-- one each time, and it must NOT cascade when that campaign is deleted.
create table if not exists title_history (
    id          integer primary key autoincrement,
    campaign_id text,
    title       text not null,
    created_at  text not null default (datetime('now'))
);

create index if not exists title_history_created_idx on title_history (created_at desc);

-- NAVER API HUB (NCP) credentials. Separate from llm_settings because these
-- are a single id/secret pair with no model name, and because the two are
-- billed by different vendors — mixing them would make the settings UI lie
-- about which key costs what.
create table if not exists naver_api_settings (
    id                      integer primary key check (id = 1),
    encrypted_client_id     text not null,
    encrypted_client_secret text not null,
    daily_call_cap          integer not null default 500,
    updated_at              text not null default (datetime('now'))
);

-- 검색광고 API (광고주센터) credentials. Kept apart from naver_api_settings
-- above despite both being "네이버": different vendor, different auth (HMAC
-- request signing vs. a static header pair), and — the reason it matters
-- operationally — this one is free while API HUB is metered. One table with
-- a shared daily cap would throttle free calls to protect a budget they
-- don't spend.
create table if not exists searchad_settings (
    id                      integer primary key check (id = 1),
    encrypted_customer_id   text not null,
    encrypted_api_key       text not null,
    encrypted_secret_key    text not null,
    updated_at              text not null default (datetime('now'))
);

-- Keyword metrics fetched from API HUB. Unlike the LLM calls elsewhere in
-- this app, these are billed per request against a monthly quota, so every
-- lookup goes through here first. A blog document count moves by fractions
-- of a percent per day on a corpus of millions, so a stale-by-a-week answer
-- is the same answer — caching costs nothing in accuracy and is the main
-- thing keeping a keyword sweep from burning quota on every rerun.
-- 여러 단계에 걸치는 화면 작업의 중간 결과. 지금은 키워드 스윕 하나가 씁니다.
-- st.session_state 에만 두면 브라우저 새로고침 한 번에 사라지는데, 스윕의
-- 2단계는 유료 호출로 산 결과라 날아가면 다시 사야 합니다.
create table if not exists app_state (
    key         text primary key,
    value       text not null,
    updated_at  text not null default (datetime('now'))
);

create table if not exists keyword_cache (
    keyword     text not null,
    metric      text not null,
    value       text not null,
    fetched_at  text not null default (datetime('now')),
    primary key (keyword, metric)
);

-- Append-only record of every API HUB request, so the daily cap can be
-- enforced locally *before* a call goes out rather than discovered on the
-- invoice. Counting rows here is the only quota signal available offline.
create table if not exists naver_api_calls (
    id      integer primary key autoincrement,
    ts      text not null default (datetime('now')),
    path    text not null,
    ok      integer not null default 1
);

create index if not exists naver_api_calls_ts_idx on naver_api_calls (ts desc);

-- Estimated token spend, so the savings from batching/caching/downscaling
-- are visible on the dashboard instead of being taken on faith.
create table if not exists usage_log (
    id                  integer primary key autoincrement,
    ts                  text not null default (datetime('now')),
    kind                text not null,
    vendor              text,
    model               text,
    image_count         integer not null default 0,
    cache_hits          integer not null default 0,
    est_input_tokens    integer not null default 0,
    est_output_tokens   integer not null default 0,
    est_saved_tokens    integer not null default 0,
    note                text
);
"""


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL lets the Streamlit UI read while a generation run writes, which a
    # single-file DB otherwise serializes into "database is locked".
    conn.execute("pragma journal_mode = WAL")
    conn.execute("pragma foreign_keys = on")
    conn.execute("pragma busy_timeout = 30000")
    return conn


def init_db() -> None:
    """Idempotent — safe to call on every Streamlit rerun."""
    global _initialized
    if _initialized:
        return
    with _INIT_LOCK:
        if _initialized:
            return
        with _connect() as conn:
            conn.executescript(SCHEMA)
            _migrate(conn)
            conn.execute(
                "insert or ignore into brand_kit (id) values (1)"
            )
            conn.commit()
        _initialized = True


# Columns added after a database was first created. `create table if not
# exists` silently does nothing for an existing table, so a new column in
# SCHEMA above reaches fresh installs only — this brings older files forward.
_ADDED_COLUMNS = {
    # `content_mode` is nullable on purpose: null means "use the brand
    # default", so changing that default moves every campaign that never had
    # an explicit choice, which is what a default should do.
    "campaigns": {
        "source_title": "text",
        "content_mode": "text",
        "notice_fields": "text not null default '{}'",
        "product_fields": "text not null default '{}'",
    },
    "brand_kit": {
        "keyword_weights": "text not null default '{}'",
        "non_target_keywords": "text not null default '[]'",
        "default_content_mode": "text not null default 'balanced'",
    },
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"pragma table_info({table})")}
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"alter table {table} add column {name} {decl}")
                print(f"[db] migrated: {table}.{name} 컬럼 추가")


@contextmanager
def get_conn():
    """Short-lived connection per operation.

    A long-lived module-level connection would be shared across Streamlit's
    script-rerun threads *and* the publish subprocess; opening per call keeps
    thread affinity trivially correct at the cost of a few microseconds on a
    local file.
    """
    init_db()
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- JSON column helpers ----------------------------------------------------

def loads(raw, default):
    if raw in (None, ""):
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)
