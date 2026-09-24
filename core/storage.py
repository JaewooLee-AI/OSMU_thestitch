"""Local file storage — replaces the Supabase `field_assets` bucket.

Two things happen at upload time that used to be spread across the Next.js
client (browser-image-compression) and the Python worker (downscale before
vision):

1. **Store-time normalization.** Photos land on disk already resized to a
   sane max edge and re-encoded as JPEG. A 6MB phone photo becomes ~200KB,
   which matters more here than it did on Supabase — there's no 1GB free-tier
   ceiling anymore, but every downstream consumer (vision, Naver upload,
   simulator preview) reads the smaller file.
2. **Content hashing.** The sha256 of the *stored* bytes is the cache key for
   ai_workers/vision_cache.py, so re-uploading the same product shot under a
   different filename still hits the caption cache instead of paying for a
   second vision call.
"""
from __future__ import annotations

import hashlib
import io
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageOps

from core import repo
from core.db import ASSETS_DIR

# Stored copies never need to be bigger than a Naver blog's widest render
# (880px content column) or an Instagram 1080px feed image.
STORE_MAX_EDGE = 1280
STORE_JPEG_QUALITY = 82


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_image(raw: bytes) -> Tuple[bytes, int, int]:
    """Returns (jpeg_bytes, width, height). Honors EXIF orientation so a
    portrait phone photo doesn't end up sideways in the simulator."""
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")
    img.thumbnail((STORE_MAX_EDGE, STORE_MAX_EDGE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=STORE_JPEG_QUALITY, optimize=True)
    return buf.getvalue(), img.width, img.height


def save_upload(raw: bytes, original_filename: str) -> str:
    """Stores an uploaded photo and returns its `rel_path`, which is the token
    that goes into the draft as `[IMAGE: rel_path]`.

    If the same image content was uploaded before, the existing rel_path is
    returned instead of writing a duplicate — that alone keeps the vision
    cache warm across campaigns.
    """
    try:
        data, width, height = normalize_image(raw)
        ext = ".jpg"
    except Exception:
        # Not a decodable image (or an unusual format Pillow lacks a plugin
        # for) — store it verbatim rather than losing the marketer's file.
        data, width, height = raw, 0, 0
        ext = Path(original_filename).suffix or ".bin"

    digest = _sha256(data)
    existing = repo.find_asset_by_sha256(digest)
    if existing and (ASSETS_DIR / existing["rel_path"]).exists():
        return existing["rel_path"]

    day = datetime.now().strftime("%Y%m")
    rel_path = f"{day}/{digest[:16]}{ext}"
    abs_path = ASSETS_DIR / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(data)

    repo.register_asset(
        rel_path=rel_path,
        filename=original_filename,
        sha256=digest,
        width=width,
        height=height,
        byte_size=len(data),
    )
    return rel_path


def abs_path(rel_path: str) -> Path:
    return ASSETS_DIR / rel_path


def read_bytes(rel_path: str) -> bytes:
    return abs_path(rel_path).read_bytes()


def exists(rel_path: str) -> bool:
    return abs_path(rel_path).exists()


def sha256_of(rel_path: str) -> Optional[str]:
    """Prefers the hash recorded at upload time; falls back to hashing the
    file (e.g. for assets copied into data/assets by hand)."""
    row = repo.find_asset_by_rel_path(rel_path)
    if row and row.get("sha256"):
        return row["sha256"]
    try:
        return _sha256(read_bytes(rel_path))
    except OSError:
        return None


def storage_usage() -> Tuple[int, int]:
    """(file_count, total_bytes) for the dashboard."""
    if not ASSETS_DIR.exists():
        return 0, 0
    files = [p for p in ASSETS_DIR.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def clear_all() -> int:
    """Deletes every uploaded photo on disk. Only ASSETS_DIR (data/assets) is
    touched — data/osmu.db, data/.master_key and data/naver_state.json live
    one level up and are never in scope here.

    Paired with repo.reset_generated_content(), which clears the matching DB
    rows: once campaigns are gone nothing references these files, and the
    dashboard's "게시 완료 · N장 · M MB" card would keep counting orphaned
    bytes forever otherwise.
    """
    if not ASSETS_DIR.exists():
        return 0
    files = [p for p in ASSETS_DIR.rglob("*") if p.is_file()]
    for f in files:
        f.unlink(missing_ok=True)
    # 비어버린 날짜별 하위 폴더(예: 202609/)도 정리합니다. 깊은 것부터 지워야
    # 부모 폴더를 비우고 지울 수 있어 역순으로 순회합니다.
    for d in sorted((p for p in ASSETS_DIR.rglob("*") if p.is_dir()), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass  # 아직 파일이 남아 있으면(예상 밖 파일) 건드리지 않습니다.
    return len(files)
