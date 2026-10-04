"""A_compiler：纯函数与配置对象"""

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from MyGICA.A_compiler import (
    ScriptConfig,
    TextLayer,
    build_drawtext_filters,
    build_reason_ass,
    check_frame,
    escape_ass_text,
    escape_filter_path,
    escape_toml_string,
    export_clip_frames,
    frame_to_ass_time,
    frame_to_time,
    frame_to_timestamp,
    is_image,
    parse_range_spec,
    probe_gaps,
    resolve_path,
    text_spans,
    volume_filter,
)
from MyGICA.structure import Text

PROJECT_TOML = """
fps = '24000/1001'
project_suffix = '.mp4'

[sources]
black = 'black.mp4'
bgm = 'bgm.wav'
go1 = 'go1.mkv'

[colors]
'灯' = '#77BBDD'

[[ranges]]
start = 0
end = 100

[[ranges.clips]]
source = 'go1'
start = 0
end = 100
"""


def test_frame_to_time_at_integer_fps():
    assert frame_to_time(48, '24') == pytest.approx(2.0)


def test_frame_to_time_at_fractional_fps():
    assert frame_to_time(24000, '24000/1001') == pytest.approx(1001.0)


def test_frame_to_time_rejects_negative_frame():
    with pytest.raises(ValueError, match='frame'):
        frame_to_time(-1, '24')


@pytest.mark.parametrize(('frame', 'expected'), [
    (0, '00:00:00.000'),
    (24, '00:00:01.000'),
    (24 * 60, '00:01:00.000'),
    (24 * 3600, '01:00:00.000'),
])
def test_frame_to_timestamp(frame, expected):
    assert frame_to_timestamp(frame, '24') == expected


def test_resolve_path_keeps_absolute_path(tmp_path):
    target = (tmp_path / 'a.mp4').resolve()
    assert resolve_path(tmp_path, target) == target


def test_resolve_path_joins_relative_path(tmp_path):
    assert resolve_path(tmp_path, Path('sub/a.mp4')) == (tmp_path / 'sub' / 'a.mp4').resolve()


def test_escape_toml_string_escapes_quote_and_colon():
    assert escape_toml_string("It's") == r"It\'s"
    assert escape_toml_string('12:30') == r'12\:30'


@pytest.mark.parametrize(('name', 'expected'), [
    ('a.png', True),
    ('a.JPG', True),
    ('a.webp', True),
    ('a.mp4', False),
    ('a.mkv', False),
    ('a', False),
])
def test_is_image(name, expected):
    assert is_image(Path(name)) is expected


def test_build_drawtext_filters_maps_color_and_aligns_top_left():
    project = SimpleNamespace(colors={'灯': '#77BBDD'})
    filters = build_drawtext_filters(
        [Text(text='爱音', fontcolor='灯', align='upper left')],
        project,
        Path('font.otf'),
    )
    assert filters == (
        "drawtext=fontfile='font.otf':text='爱音':fontcolor=#77BBDD:fontsize=78"
        ":x=960:y=934:borderw=6:bordercolor=#333333"
        ":shadowx=0:shadowy=0:shadowcolor=black@0.5"
    )


def test_build_drawtext_filters_centers_by_default():
    project = SimpleNamespace(colors={})
    filters = build_drawtext_filters([Text(text='爱音')], project, Path('font.otf'))
    assert ':x=960-text_w/2:y=934-text_h/2:' in filters


def test_build_drawtext_filters_escapes_text():
    project = SimpleNamespace(colors={})
    filters = build_drawtext_filters([Text(text="It's")], project, Path('font.otf'))
    assert r"text='It\'s'" in filters


def test_escape_filter_path_escapes_windows_drive():
    assert escape_filter_path(Path(r'C:\fonts\a.otf')) == r"'C\:/fonts/a.otf'"


@pytest.fixture
def project_dir(tmp_path):
    if shutil.which('ffmpeg') is None:
        pytest.skip('需要系统 PATH 中的 ffmpeg')
    (tmp_path / '项目.MyGICA.toml').write_text(PROJECT_TOML, encoding='utf-8')
    (tmp_path / 'font.otf').write_bytes(b'')
    return tmp_path


def test_script_config_resolves_paths_relative_to_toml(project_dir):
    config = ScriptConfig(MyGICA_path=project_dir / '项目.MyGICA.toml', fontfile=Path('font.otf'))
    assert config.root == project_dir.resolve()
    assert config.cache_dir == (project_dir / 'cache_dir').resolve()
    assert config.output_dir == (project_dir / 'output_dir').resolve()
    assert config.output == (project_dir / 'output_dir' / '项目.MyGICA.mp4').resolve()
    assert config.project.sources['go1'] == str((project_dir / 'go1.mkv').resolve())


def test_script_config_defaults_to_no_recode(project_dir):
    config = ScriptConfig(MyGICA_path=project_dir / '项目.MyGICA.toml', fontfile=Path('font.otf'))
    assert config.recode is False
    assert config.video_preset_cat_recode == [
        '-r', '24000/1001',
        '-c:v', 'hevc_nvenc', '-crf', '18', '-pix_fmt', 'p010le',
    ]


def test_script_config_root_overrides_base_dir(project_dir, tmp_path):
    other = tmp_path / '别处'
    other.mkdir()
    (other / 'font.otf').write_bytes(b'')
    config = ScriptConfig(
        MyGICA_path=project_dir / '项目.MyGICA.toml',
        root=other,
        fontfile=Path('font.otf'),
    )
    assert config.root == other.resolve()
    assert config.cache_dir == (other / 'cache_dir').resolve()


def test_script_config_rejects_wrong_suffix(tmp_path):
    bad = tmp_path / '项目.toml'
    bad.write_text(PROJECT_TOML, encoding='utf-8')
    with pytest.raises(ValueError, match='MyGICA'):
        ScriptConfig(MyGICA_path=bad, fontfile=Path('font.otf'))


@pytest.mark.parametrize(('frame', 'fps', 'expected'), [
    (0, '24', '0:00:00.00'),
    (24, '24', '0:00:01.00'),
    (24 * 60, '24', '0:01:00.00'),
    (24 * 3600, '24', '1:00:00.00'),
    (24000, '24000/1001', '0:16:41.00'),
    (24, '2400', '0:00:00.01'),
])
def test_frame_to_ass_time(frame, fps, expected):
    assert frame_to_ass_time(frame, fps) == expected


def test_escape_ass_text_neutralizes_braces_and_newline():
    assert escape_ass_text('{爱音}') == '｛爱音｝'
    assert escape_ass_text('上\n下') == r'上\N下'


def make_reason_config():
    """构造一份只够 build_reason_ass 使用的最小配置"""
    clips = [
        SimpleNamespace(start=0, end=48, reason='理由一'),
        SimpleNamespace(start=0, end=24, reason=None),
        SimpleNamespace(start=0, end=24, reason='理由二'),
    ]
    project = SimpleNamespace(
        start=100,
        fps='24',
        ranges=[SimpleNamespace(start=100, clips=clips)],
    )
    return SimpleNamespace(project=project, video_width=1920, video_height=1080)


def test_build_reason_ass_offsets_by_project_start_and_skips_empty_reason():
    content = build_reason_ass(make_reason_config())
    assert content.startswith('[Script Info]')
    assert 'PlayResX: 1920' in content
    assert 'PlayResY: 1080' in content
    assert 'Dialogue: 0,0:00:00.00,0:00:02.00,Reason,,0,0,0,,理由一' in content
    assert 'Dialogue: 0,0:00:03.00,0:00:04.00,Reason,,0,0,0,,理由二' in content
    assert content.count('Dialogue:') == 2


def test_build_reason_ass_returns_none_without_any_reason():
    config = make_reason_config()
    for clip in config.project.ranges[0].clips:
        clip.reason = None
    assert build_reason_ass(config) is None


def fake_names(prefix):
    return [[Path(f'{prefix}in{i}.png') for i in range(10)], [Path(f'{prefix}out{i}.png') for i in range(10)]]


def test_text_spans_fill_missing_ends():
    spans = text_spans([Text(text='甲'), Text(text='乙', start=10), Text(text='丙', start=10, end=20)], 100)
    assert [(a, b) for a, b, _ in spans] == [(0, 100), (10, 100), (10, 20)]


def test_text_layer_fades_only_at_its_own_edges():
    """一句连续显示的字幕，中间落进别的字幕也不该淡出再淡入"""
    layer = TextLayer(label='甲', start=40, end=160, base=Path('base.png'), names=fake_names('a'))
    assert layer.fade == 10
    assert layer.at(40) == Path('ain0.png')
    assert layer.at(49) == Path('ain9.png')
    assert layer.at(50) == Path('base.png')
    assert layer.at(60) == Path('base.png')
    assert layer.at(100) == Path('base.png')
    assert layer.at(150) == Path('aout9.png')
    assert layer.at(159) == Path('aout0.png')


def test_text_layer_fade_shrinks_for_short_span():
    layer = TextLayer(label='乙', start=0, end=9, base=Path('base.png'), names=fake_names('b'))
    assert layer.fade == 4
    assert layer.at(0) == Path('bin0.png')
    assert layer.at(4) == Path('base.png')
    assert layer.at(5) == Path('bout3.png')
    assert layer.at(8) == Path('bout0.png')


def test_text_layer_without_room_to_fade_stays_solid():
    layer = TextLayer(label='丙', start=0, end=1, base=Path('base.png'), names=[[], []])
    assert layer.fade == 0
    assert layer.at(0) == Path('base.png')


@pytest.mark.parametrize(('spec', 'total', 'expected'), [
    ('1', 5, [0]),
    ('3', 5, [2]),
    ('2-4', 5, [1, 2, 3]),
    ('1-5', 5, [0, 1, 2, 3, 4]),
])
def test_parse_range_spec(spec, total, expected):
    assert parse_range_spec(spec, total) == expected


@pytest.mark.parametrize(('spec', 'total'), [('0', 5), ('6', 5), ('4-2', 5), ('abc', 5), ('', 5)])
def test_parse_range_spec_rejects_bad_input(spec, total):
    with pytest.raises(ValueError):
        parse_range_spec(spec, total)


def test_export_clip_frames_writes_first_mid_last(tmp_path):
    if shutil.which('ffmpeg') is None:
        pytest.skip('需要系统 PATH 中的 ffmpeg')
    clip = tmp_path / 'clip.mp4'
    subprocess.run(
        ['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=64x36:r=24',
         '-vframes', '24', clip.as_posix()],
        check=True,
    )
    verify_dir = tmp_path / 'verify'
    verify_dir.mkdir()
    config = SimpleNamespace(verify=True, verify_dir=verify_dir)
    outputs = export_clip_frames(clip, config, 24, '0000_00')
    assert [p.name for p in outputs] == ['0000_00_0000.png', '0000_00_0012.png', '0000_00_0023.png']
    assert all(p.exists() and p.stat().st_size > 0 for p in outputs)


def test_export_clip_frames_can_be_disabled(tmp_path):
    config = SimpleNamespace(verify=False, verify_dir=tmp_path / 'verify')
    assert export_clip_frames(Path('not-exist.mp4'), config, 24, '0000_00') == []


def test_export_clip_frames_reuses_existing_files(tmp_path):
    verify_dir = tmp_path / 'verify'
    verify_dir.mkdir()
    for name in ('0000_00_0000.png', '0000_00_0012.png', '0000_00_0023.png'):
        (verify_dir / name).write_bytes(b'x')
    config = SimpleNamespace(verify=True, verify_dir=verify_dir)
    # 片段文件不存在，但三帧都在，应当直接复用而不去调 ffmpeg
    outputs = export_clip_frames(Path('not-exist.mp4'), config, 24, '0000_00')
    assert len(outputs) == 3


def test_volume_filter_passes_through_normal_values():
    assert volume_filter(None) == []
    assert volume_filter(0) == ['-af', 'volume=0dB']
    assert volume_filter(-12.5) == ['-af', 'volume=-12.5dB']


def test_volume_filter_zeroes_out_silent_values():
    assert volume_filter(-80) == ['-af', 'volume=0']
    assert volume_filter(-120) == ['-af', 'volume=0']


def test_build_drawtext_applies_shadow():
    project = SimpleNamespace(colors={})
    out = build_drawtext_filters(
        [Text(text='测试', shadowx=2, shadowy=2, shadowcolor='black@0.5')], project, Path('g.ttf'))
    assert 'shadowx=2' in out
    assert 'shadowy=2' in out
    assert 'shadowcolor=black@0.5' in out


def test_build_drawtext_uses_per_text_font(tmp_path):
    mine = tmp_path / 'mine.ttf'
    mine.write_bytes(b'x')
    project = SimpleNamespace(colors={})
    out = build_drawtext_filters([Text(text='测试', fontfile=str(mine))], project, Path('global.ttf'))
    assert 'mine.ttf' in out
    assert 'global.ttf' not in out


def test_build_drawtext_falls_back_to_global_font():
    project = SimpleNamespace(colors={})
    out = build_drawtext_filters([Text(text='测试')], project, Path('global.ttf'))
    assert 'global.ttf' in out


def test_build_drawtext_appends_extra_after_defaults():
    """自定义参数必须排在默认值之后，同名选项后者覆盖前者才能生效"""
    project = SimpleNamespace(colors={})
    out = build_drawtext_filters([Text(text='测试', extra='box=1:boxborderw=10')], project, Path('g.ttf'))
    assert out.endswith(':box=1:boxborderw=10')


def test_build_drawtext_ignores_empty_extra():
    project = SimpleNamespace(colors={})
    out = build_drawtext_filters([Text(text='测试')], project, Path('g.ttf'))
    assert out.endswith('shadowcolor=black@0.5')


def test_probe_gaps_returns_empty_for_clean_video(tmp_path):
    if shutil.which('ffmpeg') is None:
        pytest.skip('需要系统 PATH 中的 ffmpeg')
    clip = tmp_path / 'clip.mp4'
    subprocess.run(
        ['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=64x36:r=24000/1001',
         '-frames:v', '20', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', clip.as_posix()],
        check=True)
    assert probe_gaps(clip) == []


def test_probe_gaps_finds_dropped_frame(tmp_path):
    if shutil.which('ffmpeg') is None:
        pytest.skip('需要系统 PATH 中的 ffmpeg')
    clip = tmp_path / 'clip.mp4'
    subprocess.run(
        ['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=64x36:r=24000/1001',
         '-frames:v', '20', '-vf', "select='not(eq(mod(n\\,5)\\,3))'", '-fps_mode', 'passthrough',
         '-c:v', 'libx264', '-pix_fmt', 'yuv420p', clip.as_posix()],
        check=True)
    assert probe_gaps(clip), '丢掉几帧后应当能查出时间戳不连续'


def test_check_frame_counts_rendered_frames(tmp_path):
    if shutil.which('ffmpeg') is None:
        pytest.skip('需要系统 PATH 中的 ffmpeg')
    out = tmp_path / 'black.mp4'
    subprocess.run(
        ['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=64x36:r=24',
         '-vframes', '24', out.as_posix()],
        check=True,
    )
    assert check_frame(out) == 24
