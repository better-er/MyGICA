"""A_compiler：纯函数与配置对象"""

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from MyGICA.A_compiler import (
    ScriptConfig,
    build_drawtext_filters,
    escape_toml_string,
    frame_to_time,
    frame_to_timestamp,
    is_image,
    resolve_path,
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
        "drawtext=fontfile=font.otf:text='爱音':fontcolor=#77BBDD:fontsize=78"
        ":x=960:y=934:borderw=6:bordercolor=#333333"
    )


def test_build_drawtext_filters_centers_by_default():
    project = SimpleNamespace(colors={})
    filters = build_drawtext_filters([Text(text='爱音')], project, Path('font.otf'))
    assert ':x=960-text_w/2:y=934-text_h/2:' in filters


def test_build_drawtext_filters_escapes_text():
    project = SimpleNamespace(colors={})
    filters = build_drawtext_filters([Text(text="It's")], project, Path('font.otf'))
    assert r"text='It\'s'" in filters


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
