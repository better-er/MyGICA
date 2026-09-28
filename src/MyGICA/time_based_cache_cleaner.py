import atexit
import os
import pickle
import threading
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import click
from loguru import logger

from .betterer import subprocess_run


class TimeBasedCache:
    _instance = None
    _lock = threading.Lock()

    def __init__(self, cache_file: str = "time_data.pkl", allowed_directories: list = None):
        """
        初始化缓存管理器

        Args:
            cache_file: 缓存数据保存的文件名
            allowed_directories: 允许清理的目录列表，如果为None则不限制
        """
        self.cache_file: Path = Path(cache_file)
        self.lock_file = self.cache_file.with_suffix('.lock')  # 锁文件
        # 统一解析成绝对路径，避免与缓存键的路径形式不一致导致匹配失败
        self.allowed_directories: list[Path] = [Path(d).resolve() for d in (allowed_directories or [])]
        self.cache: dict = self._load_cache()
        self._internal_lock = threading.Lock()  # ✅ 所有访问都靠这个串行锁！

        # 注册程序退出时的清理函数
        atexit.register(self._save_cache)

    @classmethod
    def get_instance(cls):
        with cls._lock:  # 只在实例创建时加锁
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    @staticmethod
    def _key(path) -> str:
        """把路径归一化成可比较的键，纯字符串运算，不访问文件系统"""
        return os.path.normcase(os.path.abspath(str(path)))

    @staticmethod
    def _under(path: Path, directory: Path) -> bool:
        """纯路径运算判断 path 是否在 directory 内，不访问文件系统"""
        target = TimeBasedCache._key(path)
        base = TimeBasedCache._key(directory)
        return target == base or target.startswith(base + os.sep)

    def _load_cache(self) -> dict:
        """从文件加载缓存数据"""
        if self.cache_file.exists():
            with self.cache_file.open('rb') as f:
                return pickle.load(f)
        return {}

    def _save_cache(self) -> None:
        """保存缓存数据到文件"""
        with self.cache_file.open('wb') as f:
            pickle.dump(self.cache, f)

    def update(self, items: list, timestamp: datetime = None) -> None:
        """
        更新列表中每个元素的最近出现时间

        Args:
            items: 要更新的元素列表
            timestamp: 可选的时间戳，如果不提供则使用当前时间
        """
        with self._internal_lock:  # 👈 所有访问都排队
            if timestamp is None:
                timestamp = datetime.now()

            for item in items:
                item_path = Path(item)
                # 保留旧的时间戳，如果新时间戳更早则不更新
                save_timestamp = timestamp.timestamp()
                if str(item) in self.cache:
                    old_timestamp = self.cache[str(item)]['last_access']
                    if old_timestamp > save_timestamp:
                        save_timestamp = old_timestamp
                if item_path.exists():
                    self.cache[str(item)] = {
                        'last_access': save_timestamp,
                        'file_path': str(item_path),
                    }
                else:
                    logger.warning(f"更新缓存时发现文件不存在：{item_path}")

            # 立即保存缓存
            self._save_cache()

    def clearcache(self, time_diff: timedelta, dry_run: bool = False, check_corruption: bool = False, batch: bool = False) -> None:
        """
        清理早于指定时间差的记录及其对应的文件

        Args:
            time_diff: 时间差对象，用于判断哪些记录需要被清理，batch 为 True 时不用
            dry_run: 如果为True，则只记录将要删除的记录和文件，缓存与磁盘都不改动
            check_corruption: 如果为True，则检查缓存文件是否损坏
            batch: 按最近一次编译的批次清理，保留登记过的文件和编译期新生成的产物，适合时间上分不出层次的缓存
        """
        current_time = datetime.now()
        items_to_remove = []
        items_to_delete = []
        kept_count = 0
        kept_size = 0
        deleted_count = 0
        deleted_size = 0

        # 批次模式不看天数的绝对阈值，改看这次编译从什么时候开始
        batch_start = None
        if batch:
            access_times = [info['last_access'] for info in self.cache.values() if info.get('file_path')]
            if access_times:
                batch_start = datetime.fromtimestamp(min(access_times))
                logger.info(f"批次模式：这次编译从 {batch_start:%Y-%m-%d %H:%M:%S} 前后开始")
            else:
                logger.warning("批次模式：缓存索引为空，判断不出编译起点，未登记文件一律保留")

        pool = ThreadPoolExecutor()
        futures = []

        def task(info_: dict) -> tuple[str | None, str | None]:
            # errors='ignore' 让 ffprobe 出错时返回非零返回码而不是抛异常，下面的 returncode 判断才有意义
            result = subprocess_run(
                ['ffprobe', '-hide_banner', '-print_format', 'json', '-show_format', '-show_streams', info_['file_path']],
                stream_terminal=False,
                errors='ignore',
            )
            logger.debug(f"检查文件完整性：{info_['file_path']}，返回码：{result.returncode}")
            if result.returncode != 0:
                return result.stderr, info_['file_path']
            return None, None

        for item, info in self.cache.items():
            last_access = info['last_access']
            last_access = datetime.fromtimestamp(last_access)
            if not info['file_path']:
                logger.warning(f"缓存项缺少文件路径：{item}")
                items_to_remove.append(item)
                continue
            # 先按目录筛，缓存目录外的记录一律不碰，不探测、不摘记录、不动文件
            file_path = Path(info['file_path'])
            if self.allowed_directories and not any(self._under(file_path, d) for d in self.allowed_directories):
                continue

            if not file_path.exists():
                logger.warning(f"缓存项对应的文件不存在：{info['file_path']}")
                items_to_remove.append(item)
                continue

            if batch:
                # 登记项就是这次编译读入过的文件，批次模式下整体保留
                pass
            elif current_time - last_access > time_diff:
                items_to_delete.append(item)
                continue

            kept_count += 1
            kept_size += file_path.stat().st_size
            if check_corruption:
                future = pool.submit(task, info)
                futures.append(future)

        # dry_run 下缓存与磁盘都保持原样，只报告将要发生的动作
        for item in items_to_remove:
            if dry_run:
                logger.info(f"[模拟运行] 将从缓存索引中移除：{item}")
                continue
            # 只摘掉记录，这类文件本来就已经不在磁盘上了
            del self.cache[item]
            logger.info(f"已从缓存索引中移除：{item}")

        for item in items_to_delete:
            file_path = Path(self.cache[item]['file_path'])
            if dry_run:
                if file_path.exists():
                    deleted_count += 1
                    deleted_size += file_path.stat().st_size
                logger.info(f"[模拟运行] 将删除文件并从缓存索引中移除：{file_path}")
                continue
            # 如果对应的是文件，尝试删除
            if file_path.exists():
                file_size = file_path.stat().st_size
                file_path.unlink()
                deleted_count += 1
                deleted_size += file_size
                logger.info(f"已删除文件：{file_path}")
            # 同时摘掉缓存索引里的记录
            del self.cache[item]
            logger.info(f"已从缓存索引中移除：{item}")

        # 处理目录中未登记的文件，只有超过保留期限且非模拟运行才删除
        # 未登记文件没有访问记录，只能按修改时间判断；已登记项一律按 last_access 处理
        known_paths = {self._key(key) for key in self.cache}
        for allowed_dir in self.allowed_directories:
            for file in allowed_dir.rglob('*'):
                if not file.is_file():
                    continue
                if self._key(file) in known_paths:
                    continue
                mtime = datetime.fromtimestamp(file.stat().st_mtime)
                if batch:
                    # 未登记文件没有访问记录，只能按修改时间跟编译起点比
                    expired = batch_start is not None and mtime < batch_start
                else:
                    expired = current_time - mtime > time_diff
                if not expired:
                    kept_count += 1
                    kept_size += file.stat().st_size
                    continue
                if dry_run:
                    deleted_count += 1
                    deleted_size += file.stat().st_size
                    logger.info(f"[模拟运行] 将删除未登记的过期文件：{file}")
                    continue
                deleted_count += 1
                deleted_size += file.stat().st_size
                logger.info(f"删除目录中未登记的过期文件：{file}，修改时间：{mtime:%Y-%m-%d %H:%M:%S}")
                file.unlink()
                known_paths.discard(self._key(file))

        logger.info(f"删除 {deleted_count} 项，释放空间 {deleted_size / (1024 * 1024):.2f} MB；保留 {kept_count} 项，占用空间 {kept_size / (1024 * 1024):.2f} MB")

        # 模拟运行没有改动内存中的缓存，也不必落盘
        if dry_run:
            logger.info("[模拟运行] 缓存文件保持原样")
        else:
            self._save_cache()

        for future in futures:
            stderr, file_path = future.result()
            if stderr:
                logger.warning(f"文件可能损坏或不可用：{file_path}\n错误信息：{stderr}")

        # 统计剩余缓存信息，只算落在缓存目录内的记录，索引里还有源素材，算进来对不上目录真实占用
        remaining_count = len(self.cache)
        remaining_paths = set()
        remaining_size = 0
        for info in self.cache.values():
            p = Path(info['file_path'])
            if self.allowed_directories and not any(self._under(p, d) for d in self.allowed_directories):
                continue
            if not p.exists():
                logger.warning(f"统计缓存大小时发现文件不存在：{p}")
                continue
            # 按路径去重，同一路径只算一次，不按 inode，Windows 上 inode 会把不同文件判成同一份
            key = self._key(p)
            if key in remaining_paths:
                continue
            remaining_paths.add(key)
            remaining_size += p.stat().st_size
        logger.info(f"剩余缓存记录：{remaining_count} 条，其中缓存目录内 {len(remaining_paths)} 条，合计 {remaining_size / (1024 * 1024):.2f} MB")



    def get_cache_info(self) -> dict:
        """获取缓存信息（用于调试）"""
        # 将时间戳转换为可读格式
        readable_cache = {}
        for item, info in self.cache.items():
            readable_info = info.copy()
            readable_info['last_access'] = datetime.fromtimestamp(info['last_access']).strftime('%Y-%m-%d %H:%M:%S')
            readable_cache[item] = readable_info
        return readable_cache

    def __del__(self) -> None:
        """析构函数，确保缓存被保存"""
        self._save_cache()


@click.command()
@click.argument('cache_dir', default=Path('cache_dir'), type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option('--days', default=30, type=float, help='清理早于多少天前的文件', show_default=True)
@click.option('--batch', is_flag=True, help='按最近一次编译的批次清理，保留登记过的文件和编译期新生成的产物')
@click.option('--dry-run', is_flag=True, help='仅打印将要删除的文件，而不实际删除')
@click.option('--check-corruption', is_flag=True, help='检查缓存文件是否损坏')
def cli(cache_dir: Path, days: float, batch: bool, dry_run: bool, check_corruption: bool) -> None:
    """命令行接口，按天数或按最近一次编译的批次清理缓存文件"""
    cache = TimeBasedCache(allowed_directories=[str(cache_dir)])
    cache.clearcache(timedelta(days=days), dry_run=dry_run, check_corruption=check_corruption, batch=batch)


if __name__ == '__main__':
    cli()
