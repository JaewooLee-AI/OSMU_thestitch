"""Tests never touch the real data/osmu.db: point the app at a temp folder
before any core module is imported (core/db.py reads OSMU_DATA_DIR at import)."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("OSMU_DATA_DIR", tempfile.mkdtemp(prefix="osmu-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
