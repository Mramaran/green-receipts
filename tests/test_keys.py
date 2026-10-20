import os
import re
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.keys import load_or_create_issuer_key


def test_a_key_is_created_once_and_reused(tmp_path):
    path = tmp_path / "issuer_key"
    first = load_or_create_issuer_key(path)
    assert re.fullmatch(r"0x[0-9a-f]{64}", first)
    assert load_or_create_issuer_key(path) == first


def test_concurrent_first_use_creates_exactly_one_key(tmp_path):
    path = tmp_path / "issuer_key"
    with ThreadPoolExecutor(max_workers=8) as pool:
        keys = list(pool.map(lambda _: load_or_create_issuer_key(path), range(16)))
    assert len(set(keys)) == 1
    assert path.read_text(encoding="utf-8") == keys[0]


@pytest.mark.skipif(os.name == "nt", reason="file modes are not enforced the same way on Windows")
def test_the_key_file_is_readable_by_the_owner_only(tmp_path):
    path = tmp_path / "issuer_key"
    load_or_create_issuer_key(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
