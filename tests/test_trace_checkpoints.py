"""Durable partial traces remain ordinary JSON arrays across atomic updates."""
import json

import pytest

from robot_worlds.artifacts import _TraceCheckpoint, read_json


def test_incremental_checkpoint_matches_complete_json_for_empty_append_and_replacement(tmp_path):
    path = tmp_path/'trace.json'
    writer = _TraceCheckpoint(path)
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
    writer = _TraceCheckpoint(path)
    first = {'time': 0}
    writer.write([first])
    with pytest.raises(ValueError):
        writer.write([first, {'time': float('nan')}])
    assert read_json(path) == [first]
    writer.write([first, {'time': 1}])
    assert read_json(path) == [first, {'time': 1}]
    assert not list(tmp_path.glob('.writing-*'))


def test_interrupted_copy_never_replaces_the_previous_valid_trace(tmp_path, monkeypatch):
    import robot_worlds.artifacts as artifacts
    path = tmp_path/'trace.json'
    writer = _TraceCheckpoint(path)
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
