"""structure 层：配置解析与帧率解析"""

import pytest
from dacite import MissingValueError, UnexpectedDataError
from loguru import logger

from MyGICA.structure import (
    Clip,
    Range,
    Text,
    check,
    check_reused_frames,
    describe_range,
    parse_config,
    parse_fps,
)


@pytest.fixture
def error_logs(quiet_logger) -> list[str]:
    """收集测试期间 loguru 输出的 error 级别日志，需要先打开被屏蔽的 MyGICA 日志"""
    messages: list[str] = []
    logger.enable('MyGICA')
    sink_id = logger.add(lambda message: messages.append(message), level='ERROR', format='{message}')
    yield messages
    logger.remove(sink_id)
    logger.disable('MyGICA')


def make_config(**overrides) -> dict:
    """构造一份最小可用配置，再用 overrides 覆盖个别字段"""
    config = {
        'fps': '24000/1001',
        'project_suffix': '.mp4',
        'sources': {'black': 'black.mp4', 'bgm': 'bgm.wav', 'go1': 'go1.mkv'},
        'colors': {'灯': '#77BBDD'},
        'ranges': [
            {'start': 0, 'end': 100, 'clips': [{'source': 'go1', 'start': 0, 'end': 100}]},
        ],
    }
    config.update(overrides)
    return config


def timeline(config: dict) -> list[tuple[int, int, list[tuple[str, int, int]]]]:
    """把解析结果压成便于比对的片段列表"""
    project = parse_config(config)
    return [
        (r.start, r.end, [(c.source, c.start, c.end) for c in r.clips])
        for r in project.ranges
    ]


def test_minimal_config_derives_start_and_end():
    project = parse_config(make_config())
    assert (project.start, project.end) == (0, 100)
    assert timeline(make_config()) == [(0, 100, [('go1', 0, 100)])]


def test_explicit_start_and_end_take_priority():
    project = parse_config(make_config(start=0, end=100))
    assert (project.start, project.end) == (0, 100)


def test_text_field_defaults():
    project = parse_config(make_config(ranges=[
        {'start': 0, 'end': 100,
         'clips': [{'source': 'go1', 'start': 0, 'end': 100}],
         'texts': [{'text': '爱音'}]},
    ]))
    text = project.ranges[0].texts[0]
    assert (text.x, text.y, text.fontsize) == (960, 934, 78)
    assert (text.fontcolor, text.borderw, text.bordercolor, text.align) == ('white', 6, '#333333', 'center')


def test_black_clip_forces_mute_volume():
    project = parse_config(make_config(ranges=[
        {'start': 0, 'end': 100, 'clips': [{'source': 'black', 'start': 0, 'end': 100}]},
    ]))
    assert project.ranges[0].clips[0].volume == -50


@pytest.mark.parametrize('layer', ['top', 'range', 'clip', 'text'])
def test_unknown_key_is_rejected(layer):
    ranges = [{'start': 0, 'end': 100, 'clips': [{'source': 'go1', 'start': 0, 'end': 100}]}]
    overrides = {}
    if layer == 'top':
        overrides['拼错的键'] = 1
    elif layer == 'range':
        ranges[0]['拼错的键'] = 1
    elif layer == 'clip':
        ranges[0]['clips'][0]['拼错的键'] = 1
    else:
        ranges[0]['texts'] = [{'text': '爱音', '拼错的键': 1}]
    with pytest.raises(UnexpectedDataError):
        parse_config(make_config(ranges=ranges, **overrides))


def test_missing_required_key_is_rejected():
    config = make_config()
    del config['project_suffix']
    with pytest.raises(MissingValueError):
        parse_config(config)


def test_project_suffix_must_start_with_dot():
    with pytest.raises(ValueError, match='project_suffix'):
        parse_config(make_config(project_suffix='mp4'))


def test_overlapping_ranges_are_rejected():
    with pytest.raises(ValueError, match='不能重叠'):
        parse_config(make_config(ranges=[
            {'start': 0, 'end': 100, 'clips': [{'source': 'go1', 'start': 0, 'end': 100}]},
            {'start': 50, 'end': 150, 'clips': [{'source': 'go1', 'start': 0, 'end': 100}]},
        ]))


def test_range_start_must_be_before_end():
    with pytest.raises(ValueError, match='start 必须小于 end'):
        parse_config(make_config(ranges=[
            {'start': 100, 'end': 100, 'clips': [{'source': 'go1', 'start': 0, 'end': 0}]},
        ]))


def test_unknown_source_is_rejected():
    with pytest.raises(ValueError, match='不在 sources 中'):
        parse_config(make_config(ranges=[
            {'start': 0, 'end': 100, 'clips': [{'source': '不存在的源', 'start': 0, 'end': 100}]},
        ]))


def test_unknown_fontcolor_is_rejected():
    with pytest.raises(ValueError, match='不在 colors 中'):
        parse_config(make_config(ranges=[
            {'start': 0, 'end': 100,
             'clips': [{'source': 'go1', 'start': 0, 'end': 100}],
             'texts': [{'text': '爱音', 'fontcolor': '不存在的颜色'}]},
        ]))


def test_clip_with_only_start_gets_end_filled():
    assert timeline(make_config(ranges=[
        {'start': 100, 'end': 200, 'clips': [{'source': 'go1', 'start': 0}]},
    ])) == [(100, 200, [('go1', 0, 100)])]


def test_gap_between_ranges_continues_previous_clip():
    assert timeline(make_config(ranges=[
        {'start': 0, 'end': 100, 'clips': [{'source': 'go1', 'start': 0, 'end': 100}]},
        {'start': 150, 'end': 200, 'clips': [{'source': 'go1', 'start': 0, 'end': 50}]},
    ])) == [
        (0, 100, [('go1', 0, 100)]),
        (100, 150, [('go1', 100, 150)]),
        (150, 200, [('go1', 0, 50)]),
    ]


def test_empty_range_continues_previous_clip():
    assert timeline(make_config(ranges=[
        {'start': 0, 'end': 100, 'clips': [{'source': 'go1', 'start': 0, 'end': 100}]},
        {'start': 100, 'end': 200},
    ])) == [
        (0, 100, [('go1', 0, 100)]),
        (100, 200, [('go1', 100, 200)]),
    ]


def test_prev_continues_previous_clip():
    assert timeline(make_config(ranges=[
        {'start': 0, 'end': 100, 'clips': [{'source': 'go1', 'start': 0, 'end': 100}]},
        {'start': 100, 'end': 200, 'clips': [{'source': 'PREV'}]},
    ])) == [
        (0, 100, [('go1', 0, 100)]),
        (100, 200, [('go1', 100, 200)]),
    ]


def test_next_continues_the_following_clip():
    assert timeline(make_config(ranges=[
        {'start': 0, 'end': 100, 'clips': [{'source': 'NEXT'}]},
        {'start': 100, 'end': 200, 'clips': [{'source': 'go1', 'start': 100, 'end': 200}]},
    ])) == [
        (0, 100, [('go1', 0, 100)]),
        (100, 200, [('go1', 100, 200)]),
    ]


def test_two_clips_without_range_are_rejected():
    with pytest.raises(ValueError, match='不能超过 1 个'):
        parse_config(make_config(ranges=[
            {'start': 0, 'end': 100, 'clips': [{'source': 'go1'}, {'source': 'go1'}]},
        ]))


@pytest.mark.parametrize(('raw', 'expected'), [
    ('24000/1001', (24000, 1001)),
    ('30000/1001', (30000, 1001)),
    ('60', (60, 1)),
    ('24', (24, 1)),
    ('001/2', (1, 2)),
])
def test_parse_fps_accepts_integer_and_fraction(raw, expected):
    assert parse_fps(raw) == expected


@pytest.mark.parametrize('raw', [
    '23.976', '29.97', '24.0', 'abc', '24000/', '/1001', '24/', ' 24', '24 ', '-24', '24/0', '24/-1', '', '²4',
])
def test_parse_fps_rejects_float_and_garbage(raw):
    with pytest.raises(ValueError):
        parse_fps(raw)


def test_check_raises_with_message():
    with pytest.raises(ValueError, match='炸了'):
        check(False, '炸了')
    check(True, '炸了')


def test_reused_frames_are_reported(error_logs):
    reused = check_reused_frames([
        Range(start=0, end=100, clips=[Clip(source='go1', start=0, end=60)]),
        Range(start=100, end=200, clips=[Clip(source='go1', start=40, end=100)]),
    ])
    assert [(item.source, item.overlap_start, item.overlap_end, item.length) for item in reused] == [
        ('go1', 40, 60, 20),
    ]
    assert len(error_logs) == 1
    assert '重复使用' in error_logs[0]
    assert 'go1' in error_logs[0]


def test_reused_frames_within_one_range_are_reported(error_logs):
    reused = check_reused_frames([
        Range(start=0, end=100, clips=[
            Clip(source='go1', start=0, end=60),
            Clip(source='go1', start=50, end=90),
        ]),
    ])
    assert [item.length for item in reused] == [10]


def test_frames_touching_end_to_start_are_allowed(error_logs):
    reused = check_reused_frames([
        Range(start=0, end=100, clips=[Clip(source='go1', start=0, end=100)]),
        Range(start=100, end=200, clips=[Clip(source='go1', start=100, end=200)]),
    ])
    assert reused == []
    assert error_logs == []


def test_same_frames_on_different_source_are_allowed(error_logs):
    reused = check_reused_frames([
        Range(start=0, end=100, clips=[Clip(source='go1', start=0, end=100)]),
        Range(start=100, end=200, clips=[Clip(source='go2', start=0, end=100)]),
    ])
    assert reused == []
    assert error_logs == []


def test_black_source_is_skipped(error_logs):
    reused = check_reused_frames([
        Range(start=0, end=100, clips=[Clip(source='black', start=0, end=100)]),
        Range(start=100, end=200, clips=[Clip(source='black', start=50, end=150)]),
    ])
    assert reused == []
    assert error_logs == []


def test_parse_config_reports_reused_frames(error_logs):
    parse_config(make_config(ranges=[
        {'start': 0, 'end': 100, 'clips': [{'source': 'go1', 'start': 0, 'end': 100}]},
        {'start': 100, 'end': 200, 'clips': [{'source': 'go1', 'start': 50, 'end': 150}]},
    ]))
    assert len(error_logs) == 1
    assert '重复使用' in error_logs[0]


def test_black_source_is_builtin_without_material():
    """black 是内置占位源，sources 里没准备素材也应该能解析"""
    project = parse_config(make_config(
        sources={'bgm': 'bgm.wav', 'go1': 'go1.mkv'},
        ranges=[{'start': 0, 'end': 100, 'clips': [{'source': 'black', 'start': 0, 'end': 100}]}],
    ))
    assert project.ranges[0].clips[0].source == 'black'
    assert project.ranges[0].clips[0].volume == -50


def test_describe_range_carries_clips_and_texts():
    rng = Range(start=100, end=200, clips=[Clip(source='go1', start=0, end=100)], texts=[Text(text='爱音')])
    line = describe_range(3, rng)
    assert '第 3 个 Range 100-200' in line
    assert 'go1[0:100]' in line
    assert '爱音' in line


def test_text_time_range_must_fit_in_range():
    with pytest.raises(ValueError, match='显示区间'):
        parse_config(make_config(ranges=[
            {'start': 0, 'end': 100,
             'clips': [{'source': 'go1', 'start': 0, 'end': 100}],
             'texts': [{'text': '爱音', 'start': 80, 'end': 120}]},
        ]))


def test_text_with_only_start_keeps_end_empty():
    project = parse_config(make_config(ranges=[
        {'start': 0, 'end': 100,
         'clips': [{'source': 'go1', 'start': 0, 'end': 100}],
         'texts': [{'text': '爱音', 'start': 30}]},
    ]))
    text = project.ranges[0].texts[0]
    assert (text.start, text.end) == (30, None)


def test_text_escape_hatches_are_parsed():
    project = parse_config(make_config(ranges=[
        {'start': 0, 'end': 100,
         'clips': [{'source': 'go1', 'start': 0, 'end': 100}],
         'texts': [{'text': '爱音', 'drawtext': 'box=1', 'filters': 'gblur=sigma=4'}]},
    ]))
    text = project.ranges[0].texts[0]
    assert text.drawtext == 'box=1'
    assert text.filters == 'gblur=sigma=4'


def test_clip_filters_is_parsed():
    project = parse_config(make_config(ranges=[
        {'start': 0, 'end': 100,
         'clips': [{'source': 'go1', 'start': 0, 'end': 100, 'filters': 'hue=s=0'}]},
    ]))
    assert project.ranges[0].clips[0].filters == 'hue=s=0'
