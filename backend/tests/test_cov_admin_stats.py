"""Coverage for src/admin_stats.py — the image-cache disk walk (count / bytes / OSError)."""

import os

from src.admin_stats import image_cache_disk


def test_image_cache_disk_missing_dir(tmp_path):
    # A non-existent / non-directory path short-circuits to (0, 0).
    assert image_cache_disk(tmp_path / "does-not-exist") == (0, 0)
    assert image_cache_disk(None) == (0, 0)  # falsy directory


def test_image_cache_disk_counts_files(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"12345")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.jpg").write_bytes(b"678")
    count, total = image_cache_disk(tmp_path)
    assert count == 2 and total == 8


def test_image_cache_disk_swallows_oserror(tmp_path):
    (tmp_path / "real.jpg").write_bytes(b"ok")
    # A broken symlink is listed by os.walk but getsize() raises OSError -> skipped.
    broken = tmp_path / "broken.jpg"
    os.symlink(tmp_path / "missing-target", broken)
    count, total = image_cache_disk(tmp_path)
    assert count == 1 and total == 2  # only the real file counted
