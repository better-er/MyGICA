import hashlib
import json
import math
import os
import shutil
import tomllib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from pprint import pformat
from typing import Literal, Optional, Union

import click
import numpy as np
from PIL import Image
from loguru import logger

from .betterer import subprocess_run
from .structure import check, describe_range, parse_config, parse_fps, Clip, Range, Text, ProjectConfig
from .time_based_cache_cleaner import TimeBasedCache


@dataclass
class ScriptConfig:
    MyGICA_path: Path
    project: ProjectConfig = None
    output: Path = None
    root: Path = None  # 项目根目录，相对路径的解析基准，默认取 TOML 所在目录
    fontfile: Path = Path("SC-Heavy.otf")
    video_width: int = 1920
    video_height: int = 1080
    cache_dir: Path = Path('cache_dir')
    output_dir: Path = Path('output_dir')
    range_spec: Optional[str] = None  # 只渲染指定 Range，写法 3 或 3-5，1 起数，用于局部预览
    min_font_ratio: float = 0.03  # 字号相对屏高的下限，低于就告警
    verify: bool = True  # 是否把每个 clip 的首中末帧导出到校验目录
    verify_dir: Path = Path('verify_dir')  # 校验帧的落盘目录，可以随时整个删掉
    video_preset: list[str] = field(default_factory=lambda: ['-c:v', 'hevc_nvenc', '-cq', '18', '-pix_fmt', 'p010le'])
    # 走 concat 滤镜时音频也必须一起编码，-c:a copy 与滤镜链不能共存
    video_preset_cat: list[str] = field(default_factory=lambda: ['-c:v', 'hevc_nvenc', '-cq', '18', '-pix_fmt', 'p010le', '-c:a', 'aac', '-b:a', '192k'])

    def __post_init__(self):
        check(self.MyGICA_path.suffixes[-2:] == ['.MyGICA', '.toml'], 'need .MyGICA.toml file')
        check(self.MyGICA_path.exists(), '.MyGICA.toml file should exists')

        # 相对路径统一以项目根目录为基准，默认取 TOML 所在目录
        if self.root is None:
            self.root = self.MyGICA_path.resolve().parent
        else:
            self.root = Path(self.root).resolve()
        self.fontfile = resolve_path(self.root, self.fontfile)
        self.cache_dir = resolve_path(self.root, self.cache_dir)
        self.output_dir = resolve_path(self.root, self.output_dir)
        self.verify_dir = resolve_path(self.root, self.verify_dir)
        check(self.fontfile.exists(), f'font file not found: {self.fontfile}')
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if self.verify:
            self.verify_dir.mkdir(parents=True, exist_ok=True)

        # =============================
        # 解析配置文件，并且生成 ProjectConfig 对象时排除不合法的情况
        # =============================
        with self.MyGICA_path.open('rb') as f:
            self.project = parse_config(tomllib.load(f))

        # sources 里的相对路径同样相对项目根目录解析
        self.project.sources = {
            key: str(resolve_path(self.root, Path(value)))
            for key, value in self.project.sources.items()
        }

        # 文本可以各自换字体，相对路径同样以项目根为基准
        for rng in self.project.ranges:
            for text in rng.texts:
                if text.fontfile:
                    resolved = resolve_path(self.root, Path(text.fontfile))
                    check(resolved.exists(), f"字体文件不存在: {resolved}")
                    text.fontfile = str(resolved)

        check(self.project.project_suffix in {'.mp4', '.mkv', '.mov'}, 'output file should be .mp4/.mkv/.mov')

        # 只渲染指定的 Range 时先裁剪再算输出名，出来的片子只覆盖这一段，不会盖掉完整版
        output_suffix = ''
        # 报错与告警一律按原配置里的 Range 序号报，裁剪后也不会串位
        range_numbers = list(range(1, len(self.project.ranges) + 1))
        if self.range_spec:
            selected = parse_range_spec(self.range_spec, len(self.project.ranges))
            range_numbers = [index + 1 for index in selected]
            self.project.ranges = [self.project.ranges[index] for index in selected]
            self.project.start = self.project.ranges[0].start
            self.project.end = self.project.ranges[-1].end
            output_suffix = f'_r{self.range_spec}'
            logger.info(f"🎯 只渲染选中的 {len(self.project.ranges)} 个 Range，成片范围 {self.project.start}-{self.project.end}")

        # 只取文件名再拼到 output_dir，避免 MyGICA_path 是绝对路径时把输出目录整个盖掉
        output_name = self.MyGICA_path.with_suffix(self.project.project_suffix)
        if output_suffix:
            output_name = output_name.with_stem(output_name.stem + output_suffix)
        self.output = self.output_dir / output_name.name

        # 字号下限按工作区的画面字号规范，低于屏高 3% 的小字读不清
        for position, rng in enumerate(self.project.ranges):
            for text_index, text in enumerate(rng.texts, start=1):
                index = range_numbers[position]
                ratio = text.fontsize / self.video_height
                if ratio < self.min_font_ratio:
                    logger.warning(
                        f"字号低于下限：{describe_range(index, rng)} 的第 {text_index} 条 Text "
                        f"fontsize={text.fontsize}，占屏高 {ratio:.1%}，下限 {self.min_font_ratio:.0%}"
                    )

        # fps 只接受整数或分数写法，23.976 与 29.97 这类浮点表示会在 parse_fps 里直接报错
        parse_fps(self.project.fps)
        check(shutil.which('ffmpeg') is not None, 'should install ffmpeg and make sure it is in PATH')


# =============================
# 工具函数
# =============================
cache_instance = TimeBasedCache.get_instance()


def subprocess_run_cache(cmd: list[str], files: list[Path], stream_terminal: bool = True):
    """带缓存的 subprocess_run，执行命令后更新文件的时间戳"""
    subprocess_run(cmd, stream_terminal=stream_terminal)
    cache_instance.update(files)


def frame_to_timestamp(frame: int, fps: Union[str, Literal['24000/1001']]) -> str:
    total_seconds = frame_to_time(frame, fps)
    ms = int((total_seconds - int(total_seconds)) * 1000)
    s = int(total_seconds)
    h = s // 3600
    m = (s % 3600) // 60
    s = s % 60
    return f"{h:02}:{m:02}:{s:02}.{ms:03}"


def resolve_path(root: Path, path: Path) -> Path:
    """相对路径按项目根目录解析，并统一规范化为绝对路径"""
    path = Path(path)
    resolved = path if path.is_absolute() else (root / path)
    return resolved.resolve()


def parse_range_spec(spec: str, total: int) -> list[int]:
    """解析 1 起数的 Range 选择，接受 3 与 3-5 两种写法，返回 0 起数的下标列表"""
    text = spec.strip()
    check(text != '', '--range 不能为空')
    if '-' in text:
        head, _, tail = text.partition('-')
        start, end = parse_range_index(head, total), parse_range_index(tail, total)
        check(start <= end, f"--range 的起点不能大于终点: {spec}")
        return list(range(start, end + 1))
    return [parse_range_index(text, total)]


def parse_range_index(raw: str, total: int) -> int:
    """把 1 起数的 Range 序号转成 0 起数下标，并检查是否越界"""
    text = raw.strip()
    check(text.isdigit(), f"--range 只接受数字写法: {raw!r}")
    index = int(text) - 1
    check(0 <= index < total, f"--range 超出范围，共有 {total} 个 Range: {raw}")
    return index


def probe_frames(path: Path) -> tuple[list[int], Fraction]:
    """一次 ffprobe 拿到全部帧的时间戳与帧率，帧数就是时间戳的个数

    读的是包不是帧：包的时间戳就在容器的索引里，不用把画面解出来，995 帧的成片从五秒半
    降到七十毫秒。一个包一帧是 mp4、mkv、mov 的常态，所以包的个数就是帧数。
    包按解码顺序排，带 B 帧时前后会乱，排序之后才是显示顺序。
    """
    cmd = [
        'ffprobe', '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=r_frame_rate:packet=pts',
        '-of', 'json',
        path.as_posix(),
    ]
    res = subprocess_run(cmd, stream_terminal=False)
    check(res.returncode == 0, f"ffprobe 读取失败: {path}\n{res.stderr[-500:]}")
    data = json.loads(res.stdout)
    streams = data.get('streams', [])
    check(len(streams) > 0, f"ffprobe 未返回视频流: {path}")
    raw_rate = streams[0].get('r_frame_rate', '')
    check('/' in raw_rate, f"ffprobe 未返回帧率: {path}\n{res.stdout[:200]}")
    pts: list[int] = []
    for packet in data.get('packets', []):
        value = str(packet.get('pts', ''))
        check(value.lstrip('-').isdigit(), f"ffprobe 返回了无法解析的时间戳 {value!r}: {path}")
        pts.append(int(value))
    check(pts, f"ffprobe 未返回任何帧: {path}")
    return sorted(pts), Fraction(raw_rate)


def probe_video(path: Path) -> tuple[int, Fraction]:
    """读出视频的帧数与帧率"""
    pts, fps = probe_frames(path)
    return len(pts), fps


def check_frame(path: Path) -> int:
    """数出视频里实际有多少帧，用于校验渲染结果与配置声明是否一致"""
    return probe_video(path)[0]


def clip_label(rng_start: int, index: int, clip: Clip) -> str:
    """校验帧的文件名前缀，带上片段身份，换了取帧就不会复用上一次留下的图"""
    identity = f"{clip.source}:{clip.start}:{clip.end}:{clip.filters or ''}"
    return f"{rng_start:04d}_{index:02d}_{hashlib.md5(identity.encode()).hexdigest()[:6]}"


def export_clip_frames(clip_file: Path, config: ScriptConfig, frame_count: int, label: str) -> list[Path]:
    """把片段的首、中、末三帧导出到校验目录，方便逐个 clip 核对取到的画面

    只抽一个时间点很容易看走眼，三帧能看出这一段是不是稳定的、有没有夹到转场。
    已经存在的帧直接复用，所以重复编译不会重跑 ffmpeg。
    """
    if not config.verify:
        return []
    indices = sorted({0, frame_count // 2, frame_count - 1})
    outputs: list[Path] = []
    for index in indices:
        target = config.verify_dir / f"{label}_{index:04d}.png"
        if target.exists() and target.stat().st_size > 0:
            outputs.append(target)
            continue
        cmd = [
            'ffmpeg', '-y', '-hide_banner',
            '-i', clip_file.as_posix(),
            '-vf', f"select='eq(n\\,{index})'",
            '-frames:v', '1',
            target.as_posix(),
        ]
        subprocess_run(cmd, stream_terminal=False)
        check(target.exists() and target.stat().st_size > 0, f"抽取校验帧失败: {target}")
        outputs.append(target)
    logger.info(f"🖼️  校验帧 {label}: " + ', '.join(p.name for p in outputs))
    return outputs


def find_gaps(pts: list[int]) -> list[int]:
    """找出相邻帧时间戳不是标准步长的位置，返回这些帧的序号

    步长取众数，所以不必知道容器的时间基。
    """
    if len(pts) < 3:
        return []
    diffs = [b - a for a, b in zip(pts, pts[1:])]
    one = Counter(diffs).most_common(1)[0][0]
    return [i for i, d in enumerate(diffs) if d != one]


def probe_gaps(path: Path) -> list[int]:
    """只查某个文件的时间戳连续性，校验主路径走 probe_frames，这里留给单独调用的场合"""
    return find_gaps(probe_frames(path)[0])


def check_video(path: Path, expect_frames: int, expect_fps: str, where: str) -> None:
    """同时校验帧数、帧率与时间戳连续性，改帧数的滤镜和拼接留下的空档都在这里拦下"""
    pts, fps = probe_frames(path)
    frames = len(pts)
    expect = Fraction(*parse_fps(expect_fps))
    check(frames == expect_frames, f"{where} 帧数不匹配：期望 {expect_frames} 帧，实际 {frames} 帧")
    check(
        fps == expect,
        f"{where} 帧率不匹配：期望 {expect_fps} ({float(expect):.3f})，实际 {fps} ({float(fps):.3f})，检查 Clip.filters",
    )
    gaps = find_gaps(pts)
    check(
        not gaps,
        f"{where} 时间戳有 {len(gaps)} 处不连续，出现在第 {gaps[:5]} 帧之后，成片会在这些位置定格一帧",
    )


def frame_to_time(frame: int, fps: Union[str, Literal['24000/1001']]) -> float:
    """帧转时间，单位为秒"""
    check(frame >= 0, 'frame should >= 0')
    num, denom = parse_fps(fps)
    return frame * denom / num


def escape_toml_string(s: str) -> str:
    """转义字符串用于 drawtext"""
    return s.replace("'", r"\'").replace(":", r"\:")


def escape_filter_path(path: Path) -> str:
    """ffmpeg 滤镜里冒号是选项分隔符，Windows 盘符必须转义，并用单引号包住整个路径"""
    escaped = str(path).replace('\\', '/').replace(':', r'\:')
    return f"'{escaped}'"


def frame_to_ass_time(frame: int, fps: Union[str, Literal['24000/1001']]) -> str:
    """帧转 ASS 时间戳，格式 H:MM:SS.cc，ASS 只精确到厘秒"""
    total_seconds = frame_to_time(frame, fps)
    centiseconds = int(round(total_seconds * 100))
    seconds, cs = divmod(centiseconds, 100)
    hours, rem = divmod(seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours}:{minutes:02}:{seconds:02}.{cs:02}"


def escape_ass_text(s: str) -> str:
    """转义字符串用于 ASS 字幕，大括号是覆盖标签的定界符，换行写成 ASS 的 \\N"""
    return s.replace('{', '｛').replace('}', '｝').replace('\n', r'\N')


def build_reason_ass(config: ScriptConfig) -> Optional[str]:
    """为每个带 reason 的 Clip 生成一条 ASS 对话，全部 Clip 都没有 reason 时返回 None"""
    project = config.project
    dialogues = []
    for rng in project.ranges:
        # 视频时间轴从 project.start 开始算，Range 的 start 是项目时间轴的绝对帧
        now = rng.start - project.start
        for clip in rng.clips:
            length = clip.end - clip.start
            if clip.reason:
                start = frame_to_ass_time(now, project.fps)
                end = frame_to_ass_time(now + length, project.fps)
                dialogues.append(f"Dialogue: 0,{start},{end},Reason,,0,0,0,,{escape_ass_text(clip.reason)}")
            now += length
    if not dialogues:
        return None
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {config.video_width}\n"
        f"PlayResY: {config.video_height}\n"
        "WrapStyle: 0\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Reason,微软雅黑,42,&H00FFFFFF,&H000000FF,&H00202020,&H00000000,0,0,0,0,100,100,0,0,1,2,1,8,20,20,60,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    return header + "\n".join(dialogues) + "\n"


def write_reason_subtitle(config: ScriptConfig) -> Optional[Path]:
    """生成与输出视频同名的外挂字幕，用来在播放时实时查看每个画面的选取理由"""
    content = build_reason_ass(config)
    if content is None:
        return None
    ass_path = config.output.with_suffix('.ass')
    # 带 BOM 保存，避免部分播放器把中文识别成乱码
    ass_path.write_text(content, encoding='utf-8-sig')
    logger.info(f"📝 已生成选帧理由外挂字幕: {ass_path}")
    return ass_path


def is_image(file_path: Path) -> bool:
    """判断文件是否为图片格式"""
    image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tiff', '.webp'}
    return file_path.suffix.lower() in image_extensions


def build_drawtext_filters(
        texts: list[Text], project: ProjectConfig, fontfile: Path
) -> str:
    """
    构建 ffmpeg drawtext 滤镜字符串，使用指定字体文件，避免 fontconfig 崩溃
    参数:
        texts: 字幕列表，每个元素包含 text, fontsize, fontcolor, y, borderw, bordercolor
        fontfile: 字体文件路径（支持 .ttf, .otf）
        video_width, video_height: 输出分辨率
    返回:
        drawtext 滤镜字符串
    """
    filters: list[str] = []
    for txt in texts:
        # 提取参数，带默认值
        text_str = escape_toml_string(txt.text)
        fontcolor = txt.fontcolor
        fontsize = txt.fontsize
        x = txt.x
        y = txt.y
        borderw = txt.borderw
        bordercolor = txt.bordercolor

        if fontcolor in project.colors:
            fontcolor = project.colors[fontcolor]

        if txt.align == 'center':
            xy = [
                f"x={x}-text_w/2",  # 居中
                f"y={y}-text_h/2",
            ]
        elif txt.align == 'upper left':
            xy = [
                f"x={x}",  # 左上角对齐
                f"y={y}",
            ]

        # 构建 drawtext 参数
        dt_args = \
            [
                f"fontfile={escape_filter_path(Path(txt.fontfile) if txt.fontfile else fontfile)}",
                f"text='{text_str}'",  # 显示文本
                f"fontcolor={fontcolor}",
                f"fontsize={fontsize}",
            ] + xy + [
                f"borderw={borderw}",
                f"bordercolor={bordercolor}",
                f"shadowx={txt.shadowx}",
                f"shadowy={txt.shadowy}",
                f"shadowcolor={txt.shadowcolor}",
            ]
        # 自定义参数排在最后，ffmpeg 同名选项后者覆盖前者，所以能盖掉上面的默认值
        if txt.drawtext:
            dt_args.append(txt.drawtext)
        filters.append(f"drawtext={':'.join(dt_args)}")

    return ",".join(filters)


# =============================
# 缓存剪辑
# =============================
def cache_clip(cmd: list[str], files: list[Path], cache: bool = True, stream_terminal: bool = True) -> Path:
    """使用命令签名缓存剪辑，要求 cmd 最后一个参数为输出文件"""

    def check(path: Path) -> bool:
        res = subprocess_run(['ffprobe', '-hide_banner', '-print_format', 'json', '-show_format', '-show_streams', path], stream_terminal=False)
        if res.returncode != 0:
            return False
        j = json.loads(res.stdout)
        if 'streams' not in j or len(j['streams']) == 0:
            return False
        return True

    # 获取输出文件
    output_file = cmd[-1]
    output_path = Path(output_file)
    if cache:
        # 生成命令签名，保存在文件名中
        cmd_signature = hashlib.md5(' '.join(cmd[:-1]).encode()).hexdigest()
        new_output_path = output_path.with_suffix(f'.{cmd_signature[:6]}{output_path.suffix}')
        for file in files:
            if not file.exists():
                raise FileNotFoundError(f"剪辑时发现文件不存在：{file}")
        # 如果签名文件存在且大小大于0则跳过
        if new_output_path.exists() and new_output_path.stat().st_size > 0 and check(new_output_path):
            logger.info(f"⏭️  使用缓存文件: {new_output_path}")
            return new_output_path

        cmd[-1] = new_output_path.as_posix()  # 更新输出文件名为带签名的文件
    else:
        new_output_path = output_path
    logger.info(f"🎬 执行命令: {' '.join(cmd)}")
    subprocess_run_cache(cmd, files, stream_terminal=stream_terminal)
    logger.info(f"✅ 成功生成: {new_output_path}")
    return new_output_path


# =============================
# 主函数
# =============================
def work(config: ScriptConfig) -> None:
    project = config.project
    logger.info(pformat(project))

    logger.info(f"🎬 开始处理项目: {config.MyGICA_path}")
    segment_files = []

    # =============================
    # 🎬 正常剪辑片段
    # =============================
    for i, rng in enumerate(project.ranges):
        seg_file = config.cache_dir / f"seg_{rng.start}.mp4"
        new_seg_file = work_clips(config, rng, seg_file)
        segment_files.append(new_seg_file)

    # =============================
    # 拼接所有片段
    # =============================
    no_bgm = config.cache_dir / f"no_bgm.mp4"
    no_bgm = cat_video_copy(no_bgm, segment_files, config)

    # =============================
    # 拼接完成后添加背景音乐 / 在片段中添加背景音乐跳过此处
    # =============================
    output = config.cache_dir / f"output.mp4"
    new_output = add_bgm(Path(project.sources['bgm']), frame_to_time(project.start, project.fps), no_bgm, output)

    # 硬链接到最终输出文件
    config.output.unlink(missing_ok=True)
    os.link(new_output, config.output)

    # 生成与视频同名的外挂字幕，记录每个画面的选取理由，方便实时检查
    write_reason_subtitle(config)

    # 成片帧数必须与配置声明的长度一致，这是唯一能拦住滤镜或换算出错的闸门
    expect_frames = project.end - project.start
    check_video(config.output, expect_frames, project.fps, "成片")
    logger.info(f"✅ 成片帧数与帧率校验通过：{expect_frames} 帧 @ {project.fps}")

    logger.info(f"\n\n\n🎉🎉🎉 全部处理完成！输出文件: {config.output} 🎉🎉🎉\n\n")


def work_clips(config: ScriptConfig, rng: Range, seg_file: Path) -> Path:
    # 提前生成字幕缓存
    pool = ThreadPoolExecutor()
    future_text = None
    futures_clip: list[tuple] = []
    # 构建字幕，Text 自带 start/end 时会在 get_fade_text 里按时段切片
    texts = rng.texts
    if texts:
        new_seg_file_txt = seg_file.with_stem(seg_file.stem + '_text')
        input_list = new_seg_file_txt.with_suffix('.txt')
        future_text = pool.submit(get_fade_text, texts, input_list, config, rng.end - rng.start)

    segment_files = []
    now_time = rng.start
    for i, clip in enumerate(rng.clips):
        src_path = config.project.sources.get(clip.source)
        # project_start_time = frame_to_time(now_time, config.project.fps)
        # bgm = config.project.sources['bgm']
        frame_count = clip.end - clip.start  # 精确帧数

        clip_file = seg_file.with_stem(seg_file.stem + f'_{i}') if len(rng.clips) > 1 else seg_file

        af = volume_filter(clip.volume)
        # af_inline = f'volume={clip.volume}dB' if clip.volume is not None else ''
        # af_in = f'[0:a]{af_inline}[a0_vol];[a0_vol]' if clip.volume is not None else '[0:a]'

        # black 且用户没有准备素材时直接用 lavfi 合成，不必依赖 cache_in 里的占位视频
        if clip.source == 'black' and clip.source not in config.project.sources:
            cmd = [
                'ffmpeg', '-y', '-hide_banner',
                '-f', 'lavfi', '-i', f'color=c=black:s={config.video_width}x{config.video_height}:r={config.project.fps}',
                '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
                '-vframes', str(frame_count),
                '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-ac', '2',
            ]
            if clip.filters:
                cmd.extend(['-vf', clip.filters])
            cmd.extend(af + config.video_preset + [clip_file.as_posix()])
            files = []
        # 判断 source 是否是图片
        elif is_image(Path(src_path)):
            # 基础滤镜：缩放和填充
            base_filter = f'scale={config.video_width}:{config.video_height}:force_original_aspect_ratio=decrease,pad={config.video_width}:{config.video_height}:(ow-iw)/2:(oh-ih)/2'
            if clip.filters:
                base_filter = f"{base_filter},{clip.filters}"

            # 图片 -> 视频：循环 + 精确帧数控制
            cmd = \
                [
                    'ffmpeg', '-y', '-hide_banner',
                    '-f', 'lavfi',  # 使用 lavfi 生成静音
                    '-i', 'anullsrc',
                    '-t', str(frame_to_time(frame_count, config.project.fps)),  # 设置音频时长与视频匹配
                    '-loop', '1',
                    '-i', str(src_path),
                    '-vframes', str(frame_count),  # 精确控制帧数
                    '-r', str(config.project.fps),  # 设置帧率
                    '-vf', base_filter,  # 合并所有滤镜
                    '-pix_fmt', 'yuv420p10le',
                ] + config.video_preset + [str(clip_file)]
            files = [Path(src_path)]
        else:
            # 正常视频处理（保持原来的精确帧数控制）
            start_time = frame_to_timestamp(clip.start, config.project.fps)
            if clip.sound:
                sound_path = config.project.sources[clip.sound]
                # 如果在片段中替换音频
                cmd = [
                    'ffmpeg', '-y', '-hide_banner',
                    '-ss', start_time,
                    '-i', src_path,
                    '-i', sound_path,  # 替换音频
                    '-map', '0:v',
                    '-map', '1:a',
                ]
                files = [Path(src_path), Path(sound_path)]
            else:
                cmd = [
                    'ffmpeg', '-y', '-hide_banner',
                    '-ss', start_time,
                    '-i', src_path,
                ]
                files = [Path(src_path)]
            cmd.extend([
                '-vframes', str(frame_count),  # 使用精确帧数
                '-c:a', 'aac',
                '-b:a', '128k',
                '-ar', '44100',  # 统一采样率
                '-ac', '2',  # 统一声道数
            ])
            if clip.filters:
                cmd.extend(['-vf', clip.filters])
            cmd.extend(af + config.video_preset + [clip_file.as_posix()])

        logger.info(f"✂️ 剪辑: {clip.source} [{clip.start}:{clip.end}] ({frame_count} 帧) → {clip_file.name}")
        # new_clip_file = cache_clip(cmd, files)
        future = pool.submit(cache_clip, cmd, files)
        futures_clip.append((future, frame_count, clip_file, clip_label(rng.start, i, clip)))

        now_time += frame_count

    # 每个片段渲染出来的帧数必须与声明一致，改帧数的 filters 会在这里现形
    for future, expect_frames, clip_file, label in futures_clip:
        new_clip_file = future.result()
        check_video(new_clip_file, expect_frames, config.project.fps, f"片段 {clip_file.name}")
        export_clip_frames(new_clip_file, config, expect_frames, label)
        segment_files.append(new_clip_file)

    if len(rng.clips) > 1:
        new_seg_file = cat_video(seg_file, segment_files, config, config.video_preset_cat, stream_terminal=False)
        check_video(new_seg_file, rng.end - rng.start, config.project.fps, f"拼接后的 {seg_file.name}")
    else:
        new_seg_file = segment_files[0]

    # 添加字幕
    if future_text is not None:
        pattern, text_files = future_text.result()
        pool.shutdown()
        new_seg_file_txt = seg_file.with_stem(seg_file.stem + '_text')
        cmd = \
            [
                'ffmpeg', '-y', '-hide_banner',
                '-i', new_seg_file.as_posix(),
                '-framerate', config.project.fps,  # 匹配视频帧率
                '-i', pattern,  # image2 可以，但 concat 不行
                '-filter_complex', "[0:v][1:v]overlay=0:0",
            ] + config.video_preset + [
                new_seg_file_txt.as_posix()
            ]
        files = [new_seg_file, *text_files]
        new_seg_file_txt = cache_clip(cmd, files)
        return align_duration(new_seg_file_txt, config)

    pool.shutdown()
    return align_duration(new_seg_file, config)


def ensure_transparent(config: ScriptConfig) -> Path:
    """全透明底图，多个字幕段共用同一张"""
    transparent_path = config.cache_dir / Path("transparent.png")
    if not transparent_path.exists():
        transparent = np.zeros((config.video_height, config.video_width, 4), dtype=np.uint8)
        Image.fromarray(transparent).save(transparent_path)
    return transparent_path


FADE_FRAMES = 10  # 淡入淡出各占多少帧，字幕显示得不够长时按显示长度的一半压


@dataclass
class TextLayer:
    """一条 Text 单独渲染出来的一层，连同它自己那份淡入淡出序列

    淡入淡出只按这条 Text 自己的显示区间算，所以同屏的别的字幕进出不会连累它，
    一句连续显示的字幕不会因为中间插进另一句就在原处闪一下。
    """
    label: str
    start: int  # 相对 Range 起点的显示起点
    end: int  # 开区间
    base: Path
    names: list[list[Path]]  # 淡入 10 张、淡出 10 张

    @property
    def span(self) -> int:
        return self.end - self.start

    @property
    def fade(self) -> int:
        return min(FADE_FRAMES, self.span // 2)

    def at(self, frame: int) -> Path:
        """按这条 Text 自己的进度挑该用哪张图"""
        offset = frame - self.start
        fade = self.fade
        if fade:
            if offset < fade:
                return self.names[0][offset]
            if offset >= self.span - fade:
                return self.names[1][self.span - 1 - offset]
        return self.base


def text_spans(texts: list[Text], length: int) -> list[tuple[int, int, Text]]:
    """把每条 Text 的显示区间补齐成绝对帧号，缺一端就按 Range 的两端补"""
    return [
        (0 if text.start is None else text.start, length if text.end is None else text.end, text)
        for text in texts
    ]


def build_layer_filter(text: Text, project: ProjectConfig, fontfile: Path) -> str:
    """一层的滤镜串：drawtext 打底，Text.filters 接在它后面只作用在这一条字幕上"""
    chain = build_drawtext_filters([text], project, fontfile=fontfile)
    if text.filters:
        chain = f'{chain},{text.filters}'
    return chain


def check_layer_size(image: Image.Image, config: ScriptConfig, label: str) -> None:
    """图层尺寸必须和成片一致，Text.filters 里混进缩放会让叠加错位"""
    check(
        image.size == (config.video_width, config.video_height),
        f"Text 的 filters 把图层尺寸改成了 {image.size}，叠加会错位：{label}",
    )


def render_text_layer(index: int, start: int, end: int, text: Text, transparent_path: Path, config: ScriptConfig) -> TextLayer:
    """把一条 Text 单独渲到透明底上，作为合成用的一层"""
    layer_filter = build_layer_filter(text, config.project, config.fontfile)
    target = config.cache_dir / f'layer_{index}.png'
    cmd = [
        "ffmpeg", "-y", "-hide_banner",
        "-i", transparent_path.as_posix(),
        "-vf", layer_filter,
        '-frames:v', '1',
        '-update', '1',
        target.as_posix(),
    ]
    base = cache_clip(cmd, [transparent_path], stream_terminal=False)
    with Image.open(base) as image:
        check_layer_size(image, config, text.text)
    layer = TextLayer(label=text.text, start=start, end=end, base=base, names=get_blur(base))
    if layer.fade < FADE_FRAMES:
        logger.warning(f'字幕「{text.text}」只显示 {layer.span} 帧，淡入淡出压缩到 {layer.fade} 帧')
    return layer


class LayerComposer:
    """同屏多层字幕的合成器，按层内容命名，内容变了不会复用旧图"""
    def __init__(self, output_list: Path):
        self.output_list = output_list
        self.done: dict[tuple[str, ...], Path] = {}

    def compose(self, paths: list[Path]) -> Path:
        key = tuple(path.name for path in paths)
        hit = self.done.get(key)
        if hit is not None:
            return hit
        digest = hashlib.md5('|'.join(key).encode()).hexdigest()[:6]
        target = self.output_list.with_stem(f'{self.output_list.stem}_mix_{digest}').with_suffix('.png')
        if not target.exists():
            image = Image.open(paths[0]).convert('RGBA')
            for path in paths[1:]:
                image = Image.alpha_composite(image, Image.open(path).convert('RGBA'))
            image.save(target)
        self.done[key] = target
        return target


def link_frame(source: Path, target: Path) -> None:
    """用硬链接把某一帧指向已生成的图，避免复制像素"""
    target.unlink(missing_ok=True)
    os.link(source, target)


def get_fade_text(texts: list[Text], output_list: Path, config: ScriptConfig, length: int) -> tuple[str, list[Path]]:
    """生成整段字幕的逐帧 PNG 序列，每条 Text 各渲一层，各自淡入淡出"""
    transparent_path = ensure_transparent(config)
    spans = text_spans(texts, length)
    # 缓存键取配置本身，不必先把图层渲出来才知道内容变没变
    digest = hashlib.md5('|'.join(f'{t0}:{t1}:{text!r}' for t0, t1, text in spans).encode()).hexdigest()[:6]
    file_name = output_list.with_stem(f'{output_list.stem}_{digest}_%04d').with_suffix('.png').as_posix()

    # 使用缓存：首尾两帧都在才认为序列完整，避免读到中断留下的半套
    if Path(file_name % 0).exists() and Path(file_name % (length - 1)).exists():
        logger.info("⏭️  使用缓存的淡入淡出字幕图片序列")
        return file_name, [Path(file_name % i) for i in range(length)]

    layers = [
        render_text_layer(index, t0, t1, text, transparent_path, config)
        for index, (t0, t1, text) in enumerate(spans)
    ]
    composer = LayerComposer(output_list)

    for i in range(length):
        active = [layer for layer in layers if layer.start <= i < layer.end]
        if not active:
            link_frame(transparent_path, Path(file_name % i))
        elif len(active) == 1:
            link_frame(active[0].at(i), Path(file_name % i))
        else:
            link_frame(composer.compose([layer.at(i) for layer in active]), Path(file_name % i))

    return file_name, [Path(file_name % i) for i in range(length)]


def get_blur(base_text: Path) -> list[list[Path]]:
    """生成淡入淡出字幕的图片序列，文件名跟着图层内容走，生成过就直接复用"""
    names = [
        [base_text.with_stem(base_text.stem + f'_{i:02d}') for i in range(FADE_FRAMES)],
        [base_text.with_stem(base_text.stem + f'-{i:02d}') for i in range(FADE_FRAMES)],
    ]
    if all(path.exists() for pair in names for path in pair):
        return names

    img = Image.open(base_text)
    img_np = np.array(img)

    def rotate(image_np: np.ndarray) -> np.ndarray:
        return image_np[::-1, ::-1, :]

    for k in range(2):
        if k == 1: img_np = rotate(img_np)  # noqa: E701
        alpha_channel = img_np[:, :, 3]
        alpha_channel = np.max(alpha_channel, axis=0)
        painted = np.where(alpha_channel != 0)[0]
        if len(painted) == 0:
            # 空字幕没有像素可淡，两端的图都拿原图顶上
            for i in range(FADE_FRAMES):
                for path in (names[0][i], names[1][i]):
                    path.unlink(missing_ok=True)
                    os.link(base_text, path)
            return names
        start = int(painted.min())
        end = int(painted.max())
        step = (end - start) // FADE_FRAMES
        for i in range(FADE_FRAMES):
            mask = np.ones_like(alpha_channel).astype(np.double)
            l = start + i * step
            r = start + (i + 1) * step
            mask[r:] = 0
            mask[l:r] *= 1 - np.arange(step) / step
            new_img_np = img_np.copy().astype(np.double)
            new_img_np[:, :, 3] *= mask[np.newaxis, :]
            if k == 0:
                new_img = Image.fromarray(new_img_np.astype(np.uint8))
            else:
                new_img = Image.fromarray(rotate(new_img_np).astype(np.uint8))
            new_img.save(names[k][i])

    return names


SILENT_DB = -80  # 到这个档位以下的音量一律按静音处理，直接归零


def volume_filter(volume: Optional[float]) -> list[str]:
    """把音量值转成 ffmpeg 参数

    volume 是相对衰减，原始越响残留越高，光靠减够不了零；所以到静音档就直接把振幅写成 0。
    """
    if volume is None:
        return []
    if volume <= SILENT_DB:
        return ['-af', 'volume=0']
    return ['-af', f'volume={volume}dB']


def cat_video(output: Path, segment_files: list[Path], config: ScriptConfig, param: list[str], stream_terminal: bool = True) -> Path:
    """用 concat 滤镜拼接，并把时间戳归零

    早先用 concat 分离器配 -c:v copy，因为各段音频对齐的差异，每个接缝处会留下约一帧的空档，
    成片在那些位置会定格一帧。改成滤镜拼接，由 ffmpeg 重新生成连续时间戳。
    """
    logger.info(f"🎥 拼接 {len(segment_files)} 个片段 → {output}")
    inputs: list[str] = []
    for seg in segment_files:
        inputs += ['-i', seg.as_posix()]
    count = len(segment_files)
    pairs = ''.join(f'[{i}:v][{i}:a]' for i in range(count))
    # 按帧序号重写时间戳，段内音频与视频长度不完全一致时会带出漂移，靠这一步拉平
    graph = (
        f"{pairs}concat=n={count}:v=1:a=1[cv][ca];"
        "[cv]setpts=N/FRAME_RATE/TB[v];[ca]asetpts=N/SR/TB[a]"
    )
    cmd = \
        [
            'ffmpeg', '-y', '-hide_banner',
        ] + inputs + [
            '-filter_complex', graph,
            '-map', '[v]', '-map', '[a]',
        ] + param + [
            output.as_posix()
        ]
    logger.info(cmd)
    return cache_clip(cmd, segment_files, stream_terminal=stream_terminal)


def align_duration(path: Path, config: ScriptConfig) -> Path:
    """把整段的容器时长对齐到视频的精确时长

    concat 分离器是按容器时长累加偏移的。段内音频比视频长十几毫秒，下一段就要晚十几毫秒
    才开始，接缝处于是留下一个不到一帧的空档，成片会在那里定格一下。把音频 pad 或裁到
    帧数乘上分母再除以分子，时长就精确了，最终拼接才能直接复制流而不再重编码。
    """
    frames = probe_video(path)[0]
    num, denom = parse_fps(config.project.fps)
    exact = Fraction(frames * denom, num)
    target = path.with_stem(path.stem + '_aligned')
    cmd = [
        'ffmpeg', '-y', '-hide_banner',
        '-i', path.as_posix(),
        '-c:v', 'copy',
        '-af', 'apad',
        '-t', f'{float(exact):.9f}',
        '-c:a', 'aac', '-b:a', '192k', '-ar', '44100', '-ac', '2',
        target.as_posix(),
    ]
    return cache_clip(cmd, [path], stream_terminal=False)


def cat_video_copy(output: Path, segment_files: list[Path], config: ScriptConfig, stream_terminal: bool = True) -> Path:
    """用 concat 分离器直接复制流拼接，一帧画面都不用再编

    前提是各段的容器时长都对齐过，见 align_duration。有一段没对齐，接缝处就会留下空档，
    成片校验会当场报出来。
    """
    logger.info(f"🎥 直接复制拼接 {len(segment_files)} 个片段 → {output}")
    content = ''.join(f"file '{seg.as_posix()}'\n" for seg in segment_files)
    # 缓存签名只看命令不看清单内容，把清单摘要写进文件名，换了片段才不会命中旧结果
    digest = hashlib.md5(content.encode()).hexdigest()[:6]
    concat_file = config.cache_dir / f'{output.stem}_concat_{digest}.txt'
    concat_file.write_text(content, encoding='utf-8')
    cmd = [
        'ffmpeg', '-y', '-hide_banner',
        '-f', 'concat', '-safe', '0',
        '-i', concat_file.as_posix(),
        '-c', 'copy',
        output.as_posix(),
    ]
    return cache_clip(cmd, segment_files, stream_terminal=stream_terminal)


def measure_true_peak(path: Path) -> float:
    """用 loudnorm 测量音频真峰值，只读音频流，返回 dBTP"""
    cmd = [
        'ffmpeg', '-hide_banner',
        '-i', path.as_posix(),
        '-vn',
        '-af', 'loudnorm=print_format=json',
        '-f', 'null',
        '-'
    ]
    res = subprocess_run(cmd, stream_terminal=False)
    lines: list[str] = [k.strip() for k in res.stderr.splitlines()]
    try:
        start = lines.index('{')
        end = lines.index('}')
    except ValueError as error:
        raise ValueError(f"loudnorm 未输出可解析的 JSON，stderr 末尾：{res.stderr[-500:]}") from error
    j = json.loads('\n'.join(lines[start:end + 1]))
    return float(j['input_tp'])


def add_bgm(bgm: Path, audio_advance_sec: float, input_path: Path, output_path: Path, stream_terminal: bool = True) -> Path:
    """添加背景音乐，先只对混音音频做真峰值闭环，收敛后再一次性合成视频"""
    logger.info(f'添加 bgm 并提前 {audio_advance_sec:.6f} 秒')

    tmp_output = input_path.parent / output_path.name
    audio_path = tmp_output.with_suffix('.aac')
    cmd = [
        'ffmpeg', '-y', '-hide_banner',
        '-i', input_path.as_posix(),
        '-i', bgm.as_posix(),
        '-filter_complex',
        # 关键修改：对两个音频流都进行aresample和asetpts，确保它们严格同步
        f'[0:a]aresample=async=1:first_pts=0[a0]'  # 处理视频原音频，重置时间戳并异步重采样
        f';[1:a]atrim=start={audio_advance_sec},aresample=async=1[a1]'  # 处理背景音乐
        f';[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[a]'  # 混合
        ,
        '-map', '[a]',
        '-c:a', 'aac',
        '-b:a', '192k',
        audio_path.as_posix(),
    ]
    current_audio = cache_clip(cmd, [input_path, bgm], stream_terminal=stream_terminal)

    target_tp = -2.0
    tolerance = 0.1
    max_rounds = 3
    for iteration in range(max_rounds):
        input_tp = measure_true_peak(current_audio)
        if not math.isfinite(input_tp):
            logger.warning('♻️ 混音是纯静音，真峰值无从归一，跳过增益闭环')
            break
        dB = target_tp - input_tp
        if abs(dB) <= tolerance:
            logger.info(f"♻️ 真峰值已达标：input_tp={input_tp:.2f}dBTP，第 {iteration + 1} 轮收敛")
            break
        logger.info(f"♻️ 真峰值归一化第 {iteration + 1} 轮：input_tp={input_tp:.2f}dBTP，增益 {dB:+.2f}dB")
        gain_audio = current_audio.with_name(current_audio.stem + f'_gain{iteration}.aac')
        cmd = [
            'ffmpeg', '-y', '-hide_banner',
            '-i', current_audio.as_posix(),
            '-af', f'volume={dB}dB',
            '-c:a', 'aac',
            '-b:a', '192k',
            gain_audio.as_posix(),
        ]
        current_audio = cache_clip(cmd, [current_audio], stream_terminal=False)
    else:
        logger.warning(f"♻️ 真峰值归一化 {max_rounds} 轮仍未进入 ±{tolerance}dB 容差，请人工确认输出")

    cmd = [
        'ffmpeg', '-y', '-hide_banner',
        '-i', input_path.as_posix(),
        '-i', current_audio.as_posix(),
        '-map', '0:v',
        '-map', '1:a',
        '-c:v', 'copy',
        '-c:a', 'copy',
        tmp_output.as_posix(),
    ]
    return cache_clip(cmd, [input_path, current_audio], stream_terminal=stream_terminal)


# =============================
# 启动
# =============================
@click.command()
@click.argument('mygica_path', type=click.Path(exists=True, path_type=Path))
@click.option('--root', default=None, type=click.Path(path_type=Path), help='项目根目录，相对路径的解析基准，默认取 TOML 所在目录')
@click.option('--font-file', default='SC-Heavy.otf', type=click.Path(path_type=Path), help='字体文件路径', show_default=True)
@click.option('--cache-dir', default='cache_dir', type=click.Path(path_type=Path), help='缓存文件夹路径', show_default=True)
@click.option('--output-dir', default='output_dir', type=click.Path(path_type=Path), help='输出文件夹路径', show_default=True)
@click.option('--range', 'range_spec', default=None, help='只渲染指定的 Range，写法 3 或 3-5，1 起数，用于局部预览')
@click.option('--min-font-ratio', default=0.03, type=float, help='字号相对屏高的下限，低于就告警', show_default=True)
@click.option('--verify/--no-verify', default=True, help='把每个 clip 的首中末帧导出到校验目录', show_default=True)
@click.option('--verify-dir', default='verify_dir', type=click.Path(path_type=Path), help='校验帧的落盘目录', show_default=True)
def cli(
    mygica_path: Path,
    root: Path,
    cache_dir: Path,
    output_dir: Path,
    font_file: Path,
    range_spec: str,
    min_font_ratio: float,
    verify: bool,
    verify_dir: Path,
) -> None:
    config = ScriptConfig(
        MyGICA_path=mygica_path,
        root=root,
        cache_dir=cache_dir,
        output_dir=output_dir,
        fontfile=font_file,
        range_spec=range_spec,
        min_font_ratio=min_font_ratio,
        verify=verify,
        verify_dir=verify_dir,
    )
    work(config)


if __name__ == '__main__':
    cli()
