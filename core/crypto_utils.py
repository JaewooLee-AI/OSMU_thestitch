"""AES-256-GCM encryption for API keys at rest.

Blob layout (base64-encoded): nonce(12) || tag(16) || ciphertext(N)
The GCM tag authenticates the ciphertext, so any tampering with the stored
blob is detected as a decryption failure rather than silently returning
garbage.

Ported from OSMU_admin/core/crypto_utils.py with one change for local
single-machine operation: if ENCRYPTION_MASTER_KEY_BASE64 isn't set, a key
is generated once into data/.master_key (mode 0600) instead of hard-failing.
Requiring an env var made sense when the ciphertext lived in a shared cloud
DB that a second runtime (Vercel) also had to decrypt; here both the key and
the ciphertext are on the same laptop, so the env var only ever added setup
friction. Set the env var anyway if you want the key kept outside the project
directory (e.g. so a backup of data/ isn't a backup of the key too).
"""
from __future__ import annotations

import base64
import os
import stat
import threading

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from core.db import DATA_DIR

NONCE_SIZE = 12
TAG_SIZE = 16
_KEY_FILE = DATA_DIR / ".master_key"
# Two threads encrypting at first use must not each generate (and one
# overwrite) a key.
_KEY_LOCK = threading.Lock()


def _load_master_key() -> bytes:
    raw = os.environ.get("ENCRYPTION_MASTER_KEY_BASE64", "").strip()
    if raw:
        key = base64.b64decode(raw)
        if len(key) != 32:
            raise RuntimeError("ENCRYPTION_MASTER_KEY_BASE64 must decode to exactly 32 bytes.")
        return key

    with _KEY_LOCK:
        if _KEY_FILE.exists():
            return base64.b64decode(_KEY_FILE.read_text().strip())

        # Generating a key is only safe when nothing was encrypted with a
        # previous one. If secrets already exist, the real key is somewhere
        # else (an .env that wasn't loaded, a deleted/moved key file) — a new
        # key would make every stored API key permanently undecryptable while
        # looking like a plain "wrong key" error later. Stop here instead.
        if _has_encrypted_secrets():
            raise RuntimeError(
                "API 키 암호화 마스터 키를 찾을 수 없습니다. 저장된 키가 이미 있으므로 새 마스터 키를 "
                "만들지 않았습니다. .env의 ENCRYPTION_MASTER_KEY_BASE64 또는 "
                f"{_KEY_FILE} 파일을 복구하세요. 복구할 수 없다면 ⚙️ 설정에서 저장된 키(LLM·네이버)를 "
                "모두 삭제한 뒤 다시 등록하세요."
            )

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        key = os.urandom(32)
        _KEY_FILE.write_text(base64.b64encode(key).decode("ascii"))
        os.chmod(_KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0600 — owner only
        return key


def _has_encrypted_secrets() -> bool:
    from core.db import get_conn

    with get_conn() as conn:
        for table in ("llm_settings", "naver_api_settings", "searchad_settings"):
            if conn.execute(f"select 1 from {table} limit 1").fetchone():
                return True
    return False


def encrypt_api_key(plaintext: str) -> str:
    """Encrypts a plaintext API key, returning a base64 blob safe to store."""
    key = _load_master_key()
    nonce = os.urandom(NONCE_SIZE)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce), backend=default_backend()).encryptor()
    ciphertext = encryptor.update(plaintext.encode("utf-8")) + encryptor.finalize()
    return base64.b64encode(nonce + encryptor.tag + ciphertext).decode("utf-8")


def decrypt_api_key(encrypted_blob_b64: str) -> str:
    """Decrypts a blob produced by encrypt_api_key. Raises on tampering."""
    key = _load_master_key()
    blob = base64.b64decode(encrypted_blob_b64)
    nonce = blob[:NONCE_SIZE]
    tag = blob[NONCE_SIZE:NONCE_SIZE + TAG_SIZE]
    ciphertext = blob[NONCE_SIZE + TAG_SIZE:]
    decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag), backend=default_backend()).decryptor()
    return (decryptor.update(ciphertext) + decryptor.finalize()).decode("utf-8")
