"""Durable partial traces remain ordinary JSON arrays across atomic updates."""
import json

import pytest

from robot_worlds.artifacts import read_json
from robot_worlds.trace_storage import TraceCheckpoint, read_trace


def test_incremental_checkpoint_matches_complete_json_for_empty_append_and_replacement(tmp_path):
    path = tmp_path/'trace.json'
    writer = TraceCheckpoint(path)
    trace = []
    for count in range(4):
        writer.write(list(trace))
        assert path.read_bytes() == json.dumps(trace, separators=(',', ':'), allow_nan=False).encode()+b'\n'
        trace.append({'time': count, 'objects': {'part': {'position': [count+.01, 0, 1]}}})
    writer.write(trace)
    replacement = [{'time': 9, 'events': ['different trace']}]
    writer.write(replacement)
    assert read_json(path) == replacement
    writer.write([])
    assert read_json(path) == []


def test_invalid_new_sample_preserves_previous_checkpoint_and_can_retry(tmp_path):
    path = tmp_path/'trace.json'
    writer = TraceCheckpoint(path)
    first = {'time': 0}
    writer.write([first])
    with pytest.raises(ValueError):
        writer.write([first, {'time': float('nan')}])
    assert read_json(path) == [first]
    writer.write([first, {'time': 1}])
    assert read_json(path) == [first, {'time': 1}]
    assert not list(tmp_path.glob('.writing-*'))


def test_interrupted_copy_never_replaces_the_previous_valid_trace(tmp_path, monkeypatch):
    import robot_worlds.trace_storage as artifacts
    path = tmp_path/'trace.json'
    writer = TraceCheckpoint(path)
    first = {'time': 0}
    writer.write([first])
    copyfile = artifacts.shutil.copyfile
    def interrupted(source, target):
        copyfile(source, target)
        raise OSError('simulated storage failure')
    monkeypatch.setattr(artifacts.shutil, 'copyfile', interrupted)
    with pytest.raises(OSError, match='storage failure'):
        writer.write([first, {'time': 1}])
    assert read_json(path) == [first]
    assert not list(tmp_path.glob('.writing-*'))
    monkeypatch.setattr(artifacts.shutil, 'copyfile', copyfile)
    writer.write([first, {'time': 1}])
    assert read_json(path) == [first, {'time': 1}]


@pytest.mark.parametrize('suffix', ['.json', '.json.gz'])
def test_lossless_checkpoints_stream_read_and_pool_unchanged_object_states(tmp_path, suffix):
    import gzip
    path = tmp_path/('trace'+suffix)
    writer = TraceCheckpoint(path)
    trace = []
    for index in range(4):
        trace.append({'time': index, 'objects': {'static': {'position': [1, 2, 3], 'contacts': []},
                                              'moving': {'position': [index, 0, 0]}}})
        writer.write(trace)
        decoded = read_trace(path)
        assert decoded == trace
        if index:
            assert decoded[-1]['objects']['static'] is decoded[0]['objects']['static']
            assert decoded[-1]['objects']['moving'] is not decoded[0]['objects']['moving']
        raw = gzip.decompress(path.read_bytes()) if suffix.endswith('.gz') else path.read_bytes()
        assert json.loads(raw) == trace
    with pytest.raises(ValueError):
        writer.write([*trace, {'time': float('nan')}])
    assert read_trace(path) == trace
    writer.write([])
    assert read_trace(path) == []


@pytest.mark.parametrize('data', ['{}', '[', '[{}', '[{},]', '[{}] trailing', '[{},broken]'])
def test_streaming_reader_rejects_incomplete_or_malformed_evidence(tmp_path, data):
    path = tmp_path/'trace.json'
    path.write_text(data)
    with pytest.raises(ValueError):
        read_trace(path)


def test_large_repeated_scene_serialization_does_not_build_a_second_full_trace(tmp_path):
    import tracemalloc
    objects = {f'part_{i}': {'position': [float(i), 0., 1.], 'quaternion': [1., 0., 0., 0.],
                            'size': [.2, .3, .4], 'contacts': [], 'held_by': None}
               for i in range(1000)}
    trace = [{'time': i*.05, 'objects': objects} for i in range(100)]
    path = tmp_path/'trace.json.gz'
    tracemalloc.start()
    TraceCheckpoint(path).write(trace)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    # The expanded JSON is >10 MiB, but only one frame is serialized at once.
    assert peak < 4*1024*1024
    assert path.stat().st_size < 1024*1024
    restored = read_trace(path)
    assert restored == trace
    assert restored[0]['objects']['part_999'] is restored[-1]['objects']['part_999']
