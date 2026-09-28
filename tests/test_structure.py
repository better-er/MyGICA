"""structure 层：配置解析与帧率解析"""

import pytest
from dacite import MissingValueError, UnexpectedDataError

from MyGICA.structure import check, parse_config, parse_fps


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
