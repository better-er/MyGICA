import atexit
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
        self.allowed_directories: list = allowed_directories if allowed_directories is not None else []
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

    def clearcache(self, time_diff: timedelta, dry_run: bool = False, check_corruption: bool = False) -> None:
        """
        清理早于指定时间差的元素及其对应的文件

        Args:
            time_diff: 时间差对象，用于判断哪些元素需要被清理
            dry_run: 如果为True，则只打印将要删除的元素和文件，而不实际删除
            check_corruption: 如果为True，则检查缓存文件是否损坏
        """
        current_time = datetime.now()
        items_to_remove = []
        items_to_delete = []
        count = 0
        size = 0

        pool = ThreadPoolExecutor()
        futures = []

        def task(info_: dict) -> tuple[str | None, str | None]:
            if info_['file_path'] and Path(info_['file_path']).exists():
                result = subprocess_run(['ffprobe', '-hide_banner', '-print_format', 'json', '-show_format', '-show_streams', info_['file_path']], stream_terminal=False)
                print(f"检查文件完整性：{info_['file_path']}, 返回码：{result.returncode}, 输出：{result.stdout}, 错误：{result.stderr}")
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
            file_path = Path(info['file_path'])
            if not file_path.exists():
                logger.warning(f"缓存项对应的文件不存在：{info['file_path']}")
                items_to_remove.append(item)
                continue

            # 检查是否在允许的目录范围内
            if self.allowed_directories:
                # 检查文件路径是否在允许的目录列表中
                allowed = False
                for allowed_dir in self.allowed_directories:
                    allowed_path = Path(allowed_dir)
                    if file_path.is_relative_to(allowed_path):
                        allowed = True
                        break
                if not allowed:
                    # 若不在当前目录则移除
                    if not file_path.is_relative_to(Path.cwd()):
                        items_to_remove.append(item)
                        logger.info(f"移除不在当前目录的缓存：{info['file_path']}")
                        continue
                    continue  # 跳过不在允许目录中的文件
                if current_time - last_access > time_diff:
                    items_to_delete.append(item)
                    continue

            count += 1
            size += Path(info['file_path']).stat().st_size
            if check_corruption:
                future = pool.submit(task, info)
                futures.append(future)

        for item in items_to_remove:
            # 仅从缓存中移除
            del self.cache[item]
            print(f"已从缓存中移除：{item}")

        for item in items_to_delete:
            # 如果对应的是文件，尝试删除
            if self.cache[item]['file_path'] and Path(self.cache[item]['file_path']).exists():
                if dry_run:
                    print(f"[模拟运行] 将删除文件：{self.cache[item]['file_path']}")
                    continue
                Path(self.cache[item]['file_path']).unlink()
                print(f"已删除文件：{self.cache[item]['file_path']}")

            # 从缓存中移除
            del self.cache[item]
            print(f"已从缓存中移除：{item}")

        print(f"总共删除项：{count}，释放空间：{size / (1024 * 1024):.2f} MB")

        # 保存更新后的缓存
        self._save_cache()

        for future in futures:
            stderr, file_path = future.result()
            if stderr:
                print(f"文件可能损坏或不可用：{file_path}\n错误信息：{stderr}")

        # 统计剩余缓存信息
        remaining_count = len(self.cache)
        remaining_size = 0
        seen_inodes = set()  # 存储 (device_id, inode) 元组，用于去重
        for info in self.cache.values():
            p = Path(info['file_path'])
            if not p.exists():
                logger.warning(f"统计缓存大小时发现文件不存在：{p}")
                continue
            stat_info = p.stat()
            inode_key = (stat_info.st_dev, stat_info.st_ino)
            if inode_key in seen_inodes:
                # print(f"跳过重复文件：{p}")
                continue
            seen_inodes.add(inode_key)
            remaining_size += stat_info.st_size
        print(f"剩余缓存项：{remaining_count}，剩余大小：{remaining_size / (1024 * 1024):.2f} MB")

        # 查询在目录中但不在缓存中的文件
        for allowed_dir in self.allowed_directories:
            allowed_path = Path(allowed_dir)
            for file in allowed_path.rglob('*'):
                if file.is_file():
                    if str(file) not in self.cache:
                        file_size = file.stat().st_size
                        print(f"目录中但不在缓存中的文件：{file}，大小：{file_size / (1024 * 1024):.2f} MB, 修改时间：{datetime.fromtimestamp(file.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')}")
                        file.unlink()

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
@click.option('--dry-run', is_flag=True, help='仅打印将要删除的文件，而不实际删除')
@click.option('--check-corruption', is_flag=True, help='检查缓存文件是否损坏')
def cli(cache_dir: Path, days: float, dry_run: bool, check_corruption: bool) -> None:
    """命令行接口，清理指定文件夹中早于指定天数的缓存文件"""
    cache = TimeBasedCache(allowed_directories=[str(cache_dir)])
    cache.clearcache(timedelta(days=days), dry_run=dry_run, check_corruption=check_corruption)


if __name__ == '__main__':
    pass
