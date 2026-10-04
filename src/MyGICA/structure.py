from dataclasses import dataclass, field
from typing import Optional, Literal

from dacite import from_dict, Config
from loguru import logger


def check(condition: bool, message: str) -> None:
    """显式校验，失败时抛出带上下文的 ValueError，避免 assert 在 python -O 下被整体剥离"""
    if not condition:
        raise ValueError(message)


def parse_fps(fps: str) -> tuple[int, int]:
    """把 fps 解析为分子与分母组成的分数元组，只接受整数或分数写法，拒绝浮点表示"""
    if '/' in fps:
        num_str, denom_str = fps.split('/', 1)
        if not (
            num_str.isascii() and num_str.isdecimal()
            and denom_str.isascii() and denom_str.isdecimal()
        ):
            raise ValueError(f"fps 只接受整数或分数写法，不接受浮点: {fps}")
        num, denom = int(num_str), int(denom_str)
    elif fps.isascii() and fps.isdecimal():
        num, denom = int(fps), 1
    else:
        raise ValueError(f"fps 只接受整数或分数写法，不接受浮点: {fps}")
    if denom == 0:
        raise ValueError(f"fps 的分母不能为 0: {fps}")
    return num, denom


@dataclass
class Clip:
    source: str  # source 的 key
    start: Optional[int] = None
    end: Optional[int] = None
    volume: Optional[float] = None  # 音量，-80 及以下按静音处理，直接归零
    sound: Optional[str] = None  # sound 的 key
    reason: Optional[str] = None  # 选这个画面的理由，编译后会写入同名外挂字幕
    filters: Optional[str] = None  # 透传给 ffmpeg 的滤镜串，必须不改变帧数，否则会被帧数校验拦下

    def __post_init__(self):
        if self.source == 'black':
            self.volume = -50


@dataclass
class Text:
    text: str
    x: int = 960
    y: int = 934
    fontsize: int = 78
    fontcolor: str = 'white'
    borderw: int = 6
    bordercolor: str = '#333333'
    fontfile: Optional[str] = None  # 这一段单用别的字体，留空则跟工程的全局字体走
    shadowx: int = 0  # 阴影横向偏移，正数往右
    shadowy: int = 0  # 阴影纵向偏移，正数往下
    shadowcolor: str = 'black@0.5'  # 阴影颜色，支持 颜色@透明度
    # drawtext 自身的参数，接在 drawtext 末尾，写在后面的同名参数会覆盖前面的默认值，想开默认没暴露的 box、alpha 等就写这里
    drawtext: Optional[str] = None
    # 叠加在这一层字幕图上的滤镜链，和 Clip.filters 一个意思，接在 drawtext 之后，只影响这一条字幕
    filters: Optional[str] = None
    align: Literal['center', 'upper left'] = 'center'
    start: Optional[int] = None  # 相对 Range 起点的帧偏移，留空表示从 Range 头开始显示
    end: Optional[int] = None  # 相对 Range 起点的帧偏移，开区间，留空表示显示到 Range 尾


@dataclass
class Range:
    start: int
    end: int
    clips: list[Clip] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)


def describe_range(index: int, rng: Range) -> str:
    """把一个 Range 压成一行，供报错时定位，免得在长配置里数第几个 Range"""
    clips = ' | '.join(
        f"[{i}]{clip.source}[{clip.start}:{clip.end}]"
        for i, clip in enumerate(rng.clips, start=1)
    )
    texts = ' / '.join(text.text for text in rng.texts)
    return (
        f"第 {index} 个 Range {rng.start}-{rng.end} 长 {rng.end - rng.start} "
        f"clips: {clips or '无'} texts: {texts or '无'}"
    )


@dataclass
class ReusedFrames:
    """同一个源上被两个 Clip 重复使用的一段帧"""
    source: str  # 重复发生的源
    overlap_start: int  # 重复区间起点
    overlap_end: int  # 重复区间终点
    first_start: int
    first_end: int
    first_where: str  # 先出现的那个 Clip 的位置描述
    second_start: int
    second_end: int
    second_where: str  # 后出现的那个 Clip 的位置描述

    @property
    def length(self) -> int:
        """重复使用的帧数"""
        return self.overlap_end - self.overlap_start


@dataclass
class ProjectConfig:
    fps: str
    project_suffix: str
    sources: dict[str, str]
    colors: dict[str, str]
    ranges: list[Range]
    start: Optional[int] = None
    end: Optional[int] = None

    def __post_init__(self):
        if self.start is None:
            self.start = min((r.start for r in self.ranges), default=0)
        if self.end is None:
            self.end = max((r.end for r in self.ranges), default=0)
        check(self.project_suffix.startswith('.'), "project_suffix 必须以 . 开头, 例如 .mkv, .mp4")


        # 检查 start end 有序性
        check(self.start < self.end, "ProjectConfig 的 start 必须小于 end")
        for index, r in enumerate(self.ranges, start=1):
            check(r.start < r.end, f"第 {index} 个 Range 的 start 必须小于 end, {r=}")
        for i in range(len(self.ranges) - 1):
            check(
                self.ranges[i].end <= self.ranges[i + 1].start,
                f"第 {i + 1} 与第 {i + 2} 个 Range 之间不能重叠, {self.ranges[i]=}, {self.ranges[i + 1]=}",
            )

        # 检查所有 Range 的 start 和 end 是否在 ProjectConfig 的 start 和 end 范围内，自动调整超出部分
        if sum(1 for r in self.ranges if r.start < self.start or r.end > self.end) != 0:
            logger.warning("警告: 有 Range 的 start 和 end 不在 ProjectConfig 的 start 和 end 范围内, 将自动调整 Range 的 start 和 end")
            new_range = []
            for r in self.ranges:
                if r.end <= self.start or r.start >= self.end:
                    logger.warning(f"警告: Range {r} 完全在 ProjectConfig 范围外, 将被移除")
                    continue
                new_start = max(r.start, self.start)
                new_end = min(r.end, self.end)
                if new_start != r.start or new_end != r.end:
                    logger.warning(f"警告: Range {r} 的 start 或 end 超出 ProjectConfig 范围, 将被调整为 ({new_start}, {new_end})")
                new_range.append(Range(start=new_start, end=new_end, clips=r.clips, texts=r.texts))
            self.ranges = new_range

        # 所有 Range 的时间段必须完整覆盖 ProjectConfig 的时间段，自动补全空白部分
        if sum(r.end - r.start for r in self.ranges) != self.end - self.start:
            logger.warning("警告: 所有 Range 的时间段未完整覆盖 ProjectConfig 的时间段，或有重叠部分，自动补全空白部分")
            new_ranges = []
            current_start = self.start
            for r in self.ranges:
                if r.start > current_start:
                    logger.warning(f"警告: 在 {current_start} 到 {r.start} 之间有空白时间段, 将自动补全一个 Range")
                    new_ranges.append(Range(start=current_start, end=r.start, clips=[], texts=[]))
                new_ranges.append(r)
                current_start = r.end
            if current_start < self.end:
                logger.warning(f"警告: 在 {current_start} 到 {self.end} 之间有空白时间段, 将自动补全一个 Range")
                new_ranges.append(Range(start=current_start, end=self.end, clips=[], texts=[]))
            self.ranges = new_ranges

        # 检查 Clip 的 source 是否在 sources 中
        NEXT = 'NEXT'  # noqa: N806
        PREV = 'PREV'  # noqa: N806
        # black 是内置占位源，不需要用户在 sources 里准备素材
        all_sources = set(self.sources.keys()) | {NEXT, PREV, 'black'}
        for index, r in enumerate(self.ranges, start=1):
            for clip in r.clips:
                check(clip.source in all_sources, f"Clip source '{clip.source}' 不在 sources 中：{describe_range(index, r)}")

        # 检查 Text 的 fontcolor 是否在 colors 中
        all_colors = set(self.colors.keys()) | {'white', 'black'}
        for index, r in enumerate(self.ranges, start=1):
            for text in r.texts:
                check(text.fontcolor in all_colors, f"Text fontcolor '{text.fontcolor}' 不在 colors 中：{describe_range(index, r)}")

        # 检查 Clip 的 start 和 end 为 None 的总数不超过 1
        for index, r in enumerate(self.ranges, start=1):
            none_count = sum(1 for clip in r.clips if clip.start is None and clip.end is None)
            check(none_count <= 1, f"start 和 end 同时为 None 的 Clip 不能超过 1 个：{describe_range(index, r)}")

        # 如果某个 Range 内没有任何 Clip，则自动延续上一个 Range 的最后一个 Clip
        last_source = 'black'
        last_end = None
        last_volume = 0
        skip_next = False
        for range_index, r in enumerate(self.ranges, start=1):
            sum_length = sum((clip.end - clip.start) for clip in r.clips if clip.start is not None and clip.end is not None)
            if len(r.clips) > 0 and r.clips[-1].source == NEXT:
                skip_next = True
            elif len(r.clips) == 0:
                pass
            else:
                skip_next = False
            if skip_next:
                continue
            if len(r.clips) == 1 and r.clips[0].source == PREV and r.clips[0].volume is not None:
                last_volume = r.clips[0].volume
                r.clips.clear()
            count_both_none = sum(1 for clip in r.clips if clip.start is None and clip.end is None)
            check(count_both_none <= 1, f"start 和 end 同时为 None 的 Clip 不能超过 1 个：{describe_range(range_index, r)}")
            if len(r.clips) == 0:
                logger.debug(f"日志: Range {r} 内没有任何 Clip, 自动延续上一个 Clip")
                r.clips.append(Clip(source=last_source, start=last_end if last_source != 'black' else 0, volume=last_volume))
            if len(r.clips) > 0 and r.clips[0].source == PREV:
                logger.debug(f"日志: Range {r} 内 Clip source 为 PREV, 自动延续上一个 Clip")
                r.clips[0].source = last_source
                r.clips[0].start = last_end if last_source != 'black' else 0
                if r.clips[0].volume is None:
                    r.clips[0].volume = last_volume
            for clip in r.clips:
                if clip.start is None:
                    clip.start = clip.end - (r.end - r.start - sum_length)
                    sum_length += (clip.end - clip.start)
                if clip.end is None:
                    clip.end = clip.start + (r.end - r.start - sum_length)
                    sum_length += (clip.end - clip.start)
                check(clip.start <= clip.end, f"Clip 的 start 必须小于等于 end：{describe_range(range_index, r)}")
            check(sum_length == r.end - r.start, f"Clip 总长度与 Range 长度不符：{describe_range(range_index, r)}")
            last_source = r.clips[-1].source
            last_end = r.clips[-1].end
            last_volume = r.clips[-1].volume

        # 反向处理 NEXT
        last_source = 'black'
        last_start = None
        last_volume = 0
        for reverse_index, r in enumerate(reversed(self.ranges), start=1):
            if (len(r.clips) > 0 and r.clips[-1].source == NEXT) or (len(r.clips) == 0):
                logger.debug(f"日志: Range {r} 内 Clip source 为 NEXT, 自动延续下一个 Clip")
                if len(r.clips) == 1 and r.clips[0].source == NEXT and r.clips[0].volume is not None:
                    last_volume = r.clips[0].volume
                if len(r.clips) > 0:
                    r.clips.pop()
                rest = (r.end - r.start) - sum((clip.end - clip.start) for clip in r.clips)
                clip = Clip(source=last_source, start=last_start - rest if last_start != 'black' else 0, volume=last_volume)
                clip.end = clip.start + rest
                check(clip.start <= clip.end, f"Clip 的 start 必须小于等于 end：{describe_range(len(self.ranges) - reverse_index + 1, r)}")
                r.clips.append(clip)
            check(
                (r.end - r.start) == sum((clip.end - clip.start) for clip in r.clips),
                f"Clip 总长度与 Range 长度不符：{describe_range(len(self.ranges) - reverse_index + 1, r)}",
            )
            last_source = r.clips[0].source
            last_start = r.clips[0].start
            last_volume = r.clips[0].volume

        # 再次检查合法性
        # 1 检查所有 Range 的 start 和 end 是否在 ProjectConfig 的 start 和 end 范围内
        check(
            sum(1 for r in self.ranges if r.start < self.start or r.end > self.end) == 0,
            "所有 Range 的 start 和 end 必须在 ProjectConfig 的 start 和 end 范围内",
        )
        # 2 检查所有 Range 的时间段必须完整覆盖 ProjectConfig 的时间段，且不能重叠
        check(
            sum(r.end - r.start for r in self.ranges) == self.end - self.start,
            "所有 Range 的时间段必须完整覆盖 ProjectConfig 的时间段，且不能重叠",
        )
        for i in range(len(self.ranges) - 1):
            check(
                self.ranges[i].end <= self.ranges[i + 1].start,
                f"第 {i + 1} 与第 {i + 2} 个 Range 之间不能重叠, {self.ranges[i]=}, {self.ranges[i + 1]=}",
            )
        # 3 检查 Clip 的 start 和 end 不为 None
        for index, r in enumerate(self.ranges, start=1):
            for clip_index, clip in enumerate(r.clips, start=1):
                where = f"{describe_range(index, r)} 的第 {clip_index} 个 Clip"
                check(clip.start is not None and clip.end is not None, f"Clip 的 start 和 end 不能为空：{where}")
                check(clip.start <= clip.end, f"Clip 的 start 必须小于等于 end：{where}")
        # 4 检查每个 Range 内 Clip 的 start 和 end 的总长度必须等于 Range 的长度
        for index, r in enumerate(self.ranges, start=1):
            check((r.end - r.start) == sum((clip.end - clip.start) for clip in r.clips), f"Clip 总长度与 Range 长度不符：{describe_range(index, r)}")
        # 5 检查 Clip 的 source 是否在 sources 中
        for index, r in enumerate(self.ranges, start=1):
            for clip_index, clip in enumerate(r.clips, start=1):
                check(clip.source in all_sources, f"Clip source '{clip.source}' 不在 sources 中：{describe_range(index, r)} 的第 {clip_index} 个 Clip")
        # 6 检查 Text 的 fontcolor 是否在 colors 中
        for index, r in enumerate(self.ranges, start=1):
            for text_index, text in enumerate(r.texts, start=1):
                check(text.fontcolor in all_colors, f"Text fontcolor '{text.fontcolor}' 不在 colors 中：{describe_range(index, r)} 的第 {text_index} 条 Text")
        # 7 检查 Text 的显示区间落在 Range 内，缺一个端点时按另一端补齐
        for index, r in enumerate(self.ranges, start=1):
            length = r.end - r.start
            for text_index, text in enumerate(r.texts, start=1):
                if text.start is None and text.end is None:
                    continue
                t0 = 0 if text.start is None else text.start
                t1 = length if text.end is None else text.end
                check(
                    0 <= t0 < t1 <= length,
                    f"Text 显示区间 [{t0}, {t1}) 必须落在 [0, {length}) 内：{describe_range(index, r)} 的第 {text_index} 条 Text",
                )

        # 8 检查同一个源的同一帧是否被多个 Clip 重复使用
        check_reused_frames(self.ranges)


def check_reused_frames(ranges: list[Range]) -> list[ReusedFrames]:
    """检查同一个源的同一帧是否被多个 Clip 重复使用

    同一段画面用两次，观众会在很短的时间里看到一模一样的镜头，属于选帧失误，
    这里直接以 error 级别报出来，并把冲突区间返回给调用方。
    """
    spans: dict[str, list[tuple[int, int, str]]] = {}
    for index, rng in enumerate(ranges, start=1):
        for clip_index, clip in enumerate(rng.clips, start=1):
            # black 只是纯色占位，重复使用没有意义，不参与检查
            if clip.source == 'black':
                continue
            where = f"第 {index} 个 Range {rng.start}-{rng.end} 的第 {clip_index} 个 Clip"
            spans.setdefault(clip.source, []).append((clip.start, clip.end, where))

    reused: list[ReusedFrames] = []
    for source, items in spans.items():
        # 按起点排序后，只要存在重叠，就一定有一对相邻区间重叠
        items.sort()
        for first, second in zip(items, items[1:]):
            if second[0] >= first[1]:
                continue
            reused.append(ReusedFrames(
                source=source,
                overlap_start=second[0],
                overlap_end=min(first[1], second[1]),
                first_start=first[0],
                first_end=first[1],
                first_where=first[2],
                second_start=second[0],
                second_end=second[1],
                second_where=second[2],
            ))

    for item in reused:
        logger.error(
            f"♻️ 源 {item.source} 的帧 {item.overlap_start}-{item.overlap_end} 共 {item.length} 帧被重复使用："
            f"{item.first_where} [{item.first_start},{item.first_end}] 与 "
            f"{item.second_where} [{item.second_start},{item.second_end}]"
        )
    return reused


def parse_config(data) -> ProjectConfig:
    # strict=True 意味着数据里不能出现未知键，嵌套的 Range/Clip/Text 同样生效，拼错字段会直接抛 UnexpectedDataError
    config = Config(
        forward_references={"Clip": Clip, "Text": Text, "Range": Range},
        strict=True
        # 字段名必须与 dataclass 完全一致，多一个未知键都会报错，不会静默取默认值
    )
    project_config = from_dict(
        data_class=ProjectConfig,
        data=data,
        config=config
    )
    return project_config


if __name__ == '__main__':
    pass
