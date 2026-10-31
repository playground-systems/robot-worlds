"""Installer and workbench handoff with synthetic imports; no models or physics."""
import shutil
from types import SimpleNamespace

import pytest
from test_asset_library import fixture_import
from typer.testing import CliRunner

from robot_worlds import AssetLibrary, prepare_warehouse_library
from robot_worlds import warehouse_library as installer
from robot_worlds.artifacts import read_json, write_json
from robot_worlds.cli import app
from robot_worlds.server import _job_worker


@pytest.fixture
def source(tmp_path, monkeypatch):
    sourced = fixture_import(tmp_path)
    calls = []
    def import_asset(asset_id, cache, **options):
        calls.append((asset_id, cache, options))
        return sourced
    monkeypatch.setattr(installer, "import_simready_structure", import_asset)
    monkeypatch.setattr(installer, "import_simready_conveyor", lambda cache, **options: import_asset("conveyor_belt_a05", cache, **options))
    return sourced, calls


def test_selected_assets_publish_together_and_survive_original_source_removal(tmp_path, source):
    _, calls = source
    events = []
    result = prepare_warehouse_library(tmp_path / "data/asset-library", asset_ids=['pallet_a01', 'barrier_fence_metal_a02'],
                                       timeout=30, on_event=events.append)
    assert result.asset_ids == ('pallet_a01', 'barrier_fence_metal_a02')
    assert [c[0] for c in calls] == list(result.asset_ids)
    assert all(0 < c[2]['timeout'] <= 30 for c in calls)
    assert [e['type'] for e in events] == ['asset_preparing', 'asset_prepared', 'asset_preparing', 'asset_prepared', 'library_saving']
    shutil.rmtree(tmp_path / "source")
    loaded = AssetLibrary.open(result.path)
    assert loaded.asset_ids == result.asset_ids
    assert loaded.load('pallet_a01', prefix='retained').asset.objects
    assert result.seconds >= 0


@pytest.mark.parametrize("failure", ['import', 'cancel', 'deadline'])
def test_partial_failure_never_installs_a_partial_library(tmp_path, monkeypatch, source, failure):
    sourced, calls = source
    now = [0.]
    monkeypatch.setattr(installer.time, 'monotonic', lambda: now[0])
    def prepare(asset_id, cache, **options):
        calls.append(asset_id)
        if failure == 'import' and len(calls) == 2:
            raise ValueError('source unavailable')
        if failure == 'deadline':
            now[0] = 31
        return sourced
    monkeypatch.setattr(installer, 'import_simready_structure', prepare)
    output = tmp_path / 'library'
    with pytest.raises(ValueError, match='unavailable|cancelled|time budget'):
        prepare_warehouse_library(output, timeout=30, cancelled=lambda: failure == 'cancel' and bool(calls))
    assert not output.exists()


@pytest.mark.parametrize("ids", [[], ['unknown'], ['pallet_a01', 'pallet_a01'], 'pallet_a01', ['conveyor_unknown']])
def test_invalid_selection_fails_before_work(tmp_path, source, ids):
    _, calls = source
    with pytest.raises(ValueError, match='distinct reviewed'):
        prepare_warehouse_library(tmp_path / 'library', asset_ids=ids)
    assert not calls


def test_existing_library_and_nested_cache_are_rejected_before_work(tmp_path, source):
    _, calls = source
    output = tmp_path / 'existing'
    output.mkdir()
    marker = output / 'user-data'
    marker.write_text('retain me')
    with pytest.raises(ValueError, match='already exists'):
        prepare_warehouse_library(output)
    assert marker.read_text() == 'retain me'
    with pytest.raises(ValueError, match='outside'):
        prepare_warehouse_library(tmp_path / 'new', cache_dir=tmp_path / 'new/cache')
    assert not calls


def test_cli_installs_default_workbench_location_without_model_access(tmp_path, source, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    result = CliRunner().invoke(app, ['prepare-warehouse', '--data-dir', str(tmp_path / 'data'), '--asset', 'pallet_a01'])
    assert result.exit_code == 0, result.output
    assert 'Native scene validation still runs' in result.output
    assert 'workbench uses this library' in result.output
    assert AssetLibrary.open(tmp_path / 'data/asset-library').asset_ids == ('pallet_a01',)
    again = CliRunner().invoke(app, ['prepare-warehouse', '--data-dir', str(tmp_path / 'data')])
    assert again.exit_code == 1 and 'already exists' in again.output
    assert len(source[1]) == 1


def test_installed_library_is_passed_to_workbench_generation_worker(tmp_path, source, monkeypatch):
    root = tmp_path / 'data'
    installed = prepare_warehouse_library(root / 'asset-library', asset_ids=['pallet_a01'])
    job_id = 'a'*32
    write_json(root / 'jobs' / job_id / 'job.json', {'id': job_id, 'events': []})
    seen = []
    def generate(**kwargs):
        library = AssetLibrary.open(kwargs['asset_library'])
        seen.extend(library.asset_ids)
        return SimpleNamespace(id='fixture-world', manifest={'validation': {'status': 'preview'}})
    monkeypatch.setattr('robot_worlds.sdk.generate', generate)
    _job_worker(str(root), job_id, 'generate', {'prompt': 'Use the installed pallet'})
    record = read_json(root / 'jobs' / job_id / 'job.json')
    assert installed.path == root / 'asset-library'
    assert seen == ['pallet_a01']
    assert record['status'] == 'completed' and record['validation']['status'] == 'preview'
    assert record['world_id'] == 'fixture-world'


def test_conveyor_subset_uses_articulated_importer(tmp_path, source):
    _, calls = source
    result = prepare_warehouse_library(tmp_path / "conveyor-library", asset_ids=["conveyor_belt_a05"])
    assert result.asset_ids == ("conveyor_belt_a05",)
    assert len(calls) == 1 and calls[0][0] == "conveyor_belt_a05"
    assert "unpowered" in installer.WAREHOUSE_ASSETS["conveyor_belt_a05"]
