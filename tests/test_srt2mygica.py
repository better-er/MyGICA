"""srt2MyGICA：时间换算、转义与命令行"""

import pysrt
import pytest
from click.testing import CliRunner

from MyGICA.srt2MyGICA import cli, escape_toml_basic_string, time_to_frames


@pytest.mark.parametrize(('seconds', 'fps', 'frames'), [
    (0, (24, 1), 0),
    (1, (24, 1), 24),
    (1, (24000, 1001), 24),
    (1001, (24000, 1001), 24000),
])
def test_time_to_frames(seconds, fps, frames):
    assert time_to_frames(pysrt.SubRipTime(seconds=seconds), fps) == frames


def test_time_to_frames_uses_milliseconds():
    assert time_to_frames(pysrt.SubRipTime(seconds=1, milliseconds=500), (24, 1)) == 36


def test_escape_toml_basic_string():
    assert escape_toml_basic_string('\\') == '\\\\'
    assert escape_toml_basic_string('"') == '\\"'
    assert escape_toml_basic_string('\n') == '\\n'
    assert escape_toml_basic_string('\r') == '\\r'
    assert escape_toml_basic_string('\t') == '\\t'
    assert escape_toml_basic_string('爱音') == '爱音'


SAMPLE_SRT = '1\n00:00:01,000 --> 00:00:02,000\n爱音\n\n'


def test_cli_converts_srt(tmp_path):
    srt_file = tmp_path / '示例.srt'
    srt_file.write_text(SAMPLE_SRT, encoding='utf-8')
    result = CliRunner().invoke(cli, [str(srt_file), '--fps', '24'])
    assert result.exit_code == 0
    assert '[[ranges]]' in result.output
    assert 'start = 24' in result.output
    assert 'end = 48' in result.output
    assert 'text = "爱音"' in result.output


def test_cli_defaults_to_23_976(tmp_path):
    srt_file = tmp_path / '示例.srt'
    srt_file.write_text(SAMPLE_SRT, encoding='utf-8')
    result = CliRunner().invoke(cli, [str(srt_file)])
    assert result.exit_code == 0
    assert 'start = 24' in result.output
    assert 'end = 48' in result.output


def test_cli_rejects_float_fps(tmp_path):
    srt_file = tmp_path / '示例.srt'
    srt_file.write_text(SAMPLE_SRT, encoding='utf-8')
    result = CliRunner().invoke(cli, [str(srt_file), '--fps', '23.976'])
    assert result.exit_code != 0
    assert '不接受浮点' in str(result.exception)
