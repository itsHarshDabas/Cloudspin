import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


@pytest.fixture(autouse=True)
def _private_cache(tmp_path, monkeypatch):
    """Keep tests from reading or writing the real cache folder."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
