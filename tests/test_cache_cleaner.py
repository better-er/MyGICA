"""time_based_cache_cleaner：清理策略"""

import os
from datetime import datetime, timedelta

from click.testing import CliRunner

from MyGICA.time_based_cache_cleaner import TimeBasedCache, cli


def make_cache(tmp_path, cache_dir):
    return TimeBasedCache(
        cache_file=str(tmp_path / 'time_data.pkl'),
        allowed_directories=[str(cache_dir)],
    )


def test_expired_entry_is_deleted(tmp_path):
    cache_dir = tmp_path / 'cache'
    cache_dir.mkdir()
    stale = cache_dir / 'stale.mp4'
    stale.write_bytes(b'x' * 10)
    cache = make_cache(tmp_path, cache_dir)
    cache.update([stale], timestamp=datetime.now() - timedelta(days=10))
    cache.clearcache(timedelta(days=1))
    assert not stale.exists()
    assert cache.cache == {}


def test_fresh_entry_is_kept(tmp_path):
    cache_dir = tmp_path / 'cache'
    cache_dir.mkdir()
    fresh = cache_dir / 'fresh.mp4'
    fresh.write_bytes(b'x' * 10)
    cache = make_cache(tmp_path, cache_dir)
    cache.update([fresh])
    cache.clearcache(timedelta(days=1))
    assert fresh.exists()
    assert str(fresh) in cache.cache


def test_dry_run_has_no_side_effects(tmp_path):
    cache_dir = tmp_path / 'cache'
    cache_dir.mkdir()
    stale = cache_dir / 'stale.mp4'
    stale.write_bytes(b'x' * 10)
    cache = make_cache(tmp_path, cache_dir)
    cache.update([stale], timestamp=datetime.now() - timedelta(days=10))
    before = (tmp_path / 'time_data.pkl').read_bytes()
    cache.clearcache(timedelta(days=1), dry_run=True)
    assert stale.exists()
    assert str(stale) in cache.cache
    assert (tmp_path / 'time_data.pkl').read_bytes() == before


def test_dry_run_keeps_unregistered_orphan(tmp_path):
    cache_dir = tmp_path / 'cache'
    cache_dir.mkdir()
    orphan = cache_dir / 'orphan.mp4'
    orphan.write_bytes(b'x' * 10)
    old = (datetime.now() - timedelta(days=10)).timestamp()
    os.utime(orphan, (old, old))
    cache = make_cache(tmp_path, cache_dir)
    cache.clearcache(timedelta(days=1), dry_run=True)
    assert orphan.exists()


def test_missing_file_is_dropped_from_cache(tmp_path):
    cache_dir = tmp_path / 'cache'
    cache_dir.mkdir()
    ghost = cache_dir / 'ghost.mp4'
    ghost.write_bytes(b'x')
    cache = make_cache(tmp_path, cache_dir)
    cache.update([ghost])
    ghost.unlink()
    cache.clearcache(timedelta(days=1))
    assert cache.cache == {}


def test_file_outside_whitelist_is_not_deleted(tmp_path, monkeypatch):
    cache_dir = tmp_path / 'cache'
    cache_dir.mkdir()
    outside_dir = tmp_path / 'outside'
    outside_dir.mkdir()
    outside = outside_dir / 'other.mp4'
    outside.write_bytes(b'x' * 10)
    # 把当前目录固定在白名单内，让白名单外的分支条件显式成立，不再依赖真实 cwd
    monkeypatch.chdir(cache_dir)
    cache = make_cache(tmp_path, cache_dir)
    cache.update([outside], timestamp=datetime.now() - timedelta(days=10))
    cache.clearcache(timedelta(days=1))
    assert outside.exists()
    assert str(outside) not in cache.cache


def test_unregistered_expired_file_is_deleted(tmp_path):
    cache_dir = tmp_path / 'cache'
    cache_dir.mkdir()
    orphan = cache_dir / 'orphan.mp4'
    orphan.write_bytes(b'x' * 10)
    old = (datetime.now() - timedelta(days=10)).timestamp()
    os.utime(orphan, (old, old))
    cache = make_cache(tmp_path, cache_dir)
    cache.clearcache(timedelta(days=1))
    assert not orphan.exists()


def test_unregistered_fresh_file_is_kept(tmp_path):
    cache_dir = tmp_path / 'cache'
    cache_dir.mkdir()
    orphan = cache_dir / 'orphan.mp4'
    orphan.write_bytes(b'x' * 10)
    cache = make_cache(tmp_path, cache_dir)
    cache.clearcache(timedelta(days=1))
    assert orphan.exists()


def test_cli_dry_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache_dir = tmp_path / 'cache_dir'
    cache_dir.mkdir()
    result = CliRunner().invoke(cli, [str(cache_dir), '--dry-run'])
    assert result.exit_code == 0
