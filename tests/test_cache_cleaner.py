"""time_based_cache_cleaner：清理策略"""

import os
from datetime import datetime, timedelta
from pathlib import Path

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


def test_file_outside_whitelist_is_untouched(tmp_path, monkeypatch):
    """缓存目录外的记录既不删文件，也不从记录里摘掉"""
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
    assert str(outside) in cache.cache


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


def test_batch_mode_keeps_registered_entry(tmp_path):
    """批次模式不看天数，登记项整体保留"""
    cache_dir = tmp_path / 'cache'
    cache_dir.mkdir()
    registered = cache_dir / 'registered.mp4'
    registered.write_bytes(b'x' * 10)
    cache = make_cache(tmp_path, cache_dir)
    cache.update([registered], timestamp=datetime.now() - timedelta(days=30))
    cache.clearcache(timedelta(days=1), batch=True)
    assert registered.exists()
    assert str(registered) in cache.cache


def test_batch_mode_deletes_orphan_before_compile_start(tmp_path):
    """批次模式删掉修改时间早于这次编译起点的未登记文件"""
    cache_dir = tmp_path / 'cache'
    cache_dir.mkdir()
    registered = cache_dir / 'registered.mp4'
    registered.write_bytes(b'x' * 10)
    orphan = cache_dir / 'orphan.mp4'
    orphan.write_bytes(b'x' * 10)
    cache = make_cache(tmp_path, cache_dir)
    cache.update([registered], timestamp=datetime.now() - timedelta(hours=1))
    old = (datetime.now() - timedelta(days=2)).timestamp()
    os.utime(orphan, (old, old))
    cache.clearcache(timedelta(days=1), batch=True)
    assert registered.exists()
    assert not orphan.exists()


def test_batch_mode_keeps_orphan_generated_during_compile(tmp_path):
    """批次模式保留修改时间晚于编译起点的未登记文件，也就是编译期新生成的产物"""
    cache_dir = tmp_path / 'cache'
    cache_dir.mkdir()
    registered = cache_dir / 'registered.mp4'
    registered.write_bytes(b'x' * 10)
    fresh = cache_dir / 'fresh.mp4'
    fresh.write_bytes(b'x' * 10)
    cache = make_cache(tmp_path, cache_dir)
    cache.update([registered], timestamp=datetime.now() - timedelta(hours=1))
    cache.clearcache(timedelta(days=1), batch=True)
    assert fresh.exists()


def test_batch_mode_with_empty_cache_keeps_orphan(tmp_path):
    """批次模式判断不出编译起点时，未登记文件一律保留"""
    cache_dir = tmp_path / 'cache'
    cache_dir.mkdir()
    orphan = cache_dir / 'orphan.mp4'
    orphan.write_bytes(b'x' * 10)
    old = (datetime.now() - timedelta(days=10)).timestamp()
    os.utime(orphan, (old, old))
    cache = make_cache(tmp_path, cache_dir)
    cache.clearcache(timedelta(days=1), batch=True)
    assert orphan.exists()


def test_cli_batch_dry_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache_dir = tmp_path / 'cache_dir'
    cache_dir.mkdir()
    result = CliRunner().invoke(cli, [str(cache_dir), '--batch', '--dry-run'])
    assert result.exit_code == 0


def test_outside_whitelist_is_not_probed(tmp_path, monkeypatch):
    """缓存目录外的记录不做存在性探测，清理只落在缓存目录里"""
    cache_dir = tmp_path / 'cache'
    cache_dir.mkdir()
    outside_dir = tmp_path / 'outside'
    outside_dir.mkdir()
    outside = outside_dir / 'other.mp4'
    outside.write_bytes(b'x' * 10)
    inside = cache_dir / 'inside.mp4'
    inside.write_bytes(b'x' * 10)
    cache = make_cache(tmp_path, cache_dir)
    cache.update([outside, inside], timestamp=datetime.now() - timedelta(days=10))

    probed = []
    original = Path.exists

    def spy(self):
        probed.append(str(self))
        return original(self)

    monkeypatch.setattr(Path, 'exists', spy)
    cache.clearcache(timedelta(days=1))
    assert str(outside) not in probed
