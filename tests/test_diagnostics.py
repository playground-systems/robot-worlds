"""Setup report boundaries: no network, native imports, secrets or writes."""
import builtins
import json

from robot_worlds import diagnostics


def prepared(monkeypatch, tmp_path):
    for path in diagnostics.REQUIRED_RESOURCES:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"test resource")
    monkeypatch.setattr(diagnostics.resources, "files", lambda package: tmp_path)
    monkeypatch.setattr(diagnostics.metadata, "version", lambda name: "1.0.0")
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Darwin")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MUJOCO_GL", raising=False)


def test_offline_success_never_claims_live_model_or_native_readiness(monkeypatch, tmp_path):
    prepared(monkeypatch, tmp_path)
    report = diagnostics.collect_diagnostics(data_dir=tmp_path / "not-created")
    assert report["offline_ready"]
    assert not any(report[key] for key in ("model_verified", "physics_verified", "graphics_verified"))
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["api_key"]["status"] == "warning"
    assert checks["model"]["status"] == checks["graphics"]["status"] == "unverified"
    assert not (tmp_path / "not-created").exists()


def test_missing_asset_or_distribution_is_an_actionable_error(monkeypatch, tmp_path):
    prepared(monkeypatch, tmp_path)
    (tmp_path / "assets/robots/g1/CONTROLLER_LICENSE").unlink()

    def version(name):
        if name == "onnxruntime":
            raise diagnostics.metadata.PackageNotFoundError(name)
        return "1.0.0"

    monkeypatch.setattr(diagnostics.metadata, "version", version)
    report = diagnostics.collect_diagnostics(data_dir=tmp_path)
    assert not report["offline_ready"]
    assert "onnxruntime" in json.dumps(report)
    assert "assets/robots/g1/CONTROLLER_LICENSE" in json.dumps(report)


def test_missing_bundled_starter_is_an_incomplete_installation(monkeypatch, tmp_path):
    prepared(monkeypatch, tmp_path)
    missing = "assets/starters/hospital/world.zip"
    (tmp_path / missing).unlink()
    report = diagnostics.collect_diagnostics(data_dir=tmp_path)
    assert not report["offline_ready"]
    assert missing in json.dumps(report)


def test_shareable_report_never_echoes_credentials_paths_or_arbitrary_env(monkeypatch, tmp_path):
    prepared(monkeypatch, tmp_path)
    secret = "sensitive-value-never-echo"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://" + secret + ".invalid")
    monkeypatch.setenv("MUJOCO_GL", secret)
    report = diagnostics.collect_diagnostics(data_dir=tmp_path / secret)
    encoded = diagnostics.format_diagnostics(report, as_json=True)
    assert secret not in encoded
    assert str(tmp_path) not in encoded
    assert any(check.get("configured") is True for check in report["checks"])


def test_diagnostics_does_not_import_native_or_model_clients(monkeypatch, tmp_path):
    prepared(monkeypatch, tmp_path)
    original = builtins.__import__

    def checked_import(name, *args, **kwargs):
        assert name.split(".")[0] not in {"mujoco", "onnxruntime", "openai", "socket", "subprocess"}
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", checked_import)
    diagnostics.collect_diagnostics(data_dir=tmp_path)


def test_module_main_produces_parseable_json_and_exit_status(monkeypatch, tmp_path, capsys):
    prepared(monkeypatch, tmp_path)
    assert diagnostics.main(["--json", "--data-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "offline"
    monkeypatch.setattr(diagnostics, "collect_diagnostics", lambda **kwargs: {
        "offline_ready": False, "checks": [], "mode": "offline"})
    assert diagnostics.main(["--json"]) == 1
    assert not json.loads(capsys.readouterr().out)["offline_ready"]


def test_optional_warehouse_decoder_does_not_block_basic_workflow_or_reveal_paths(monkeypatch, tmp_path):
    prepared(monkeypatch, tmp_path)
    monkeypatch.setattr(diagnostics.shutil, 'which', lambda _: None)
    report = diagnostics.collect_diagnostics()
    check = next(c for c in report['checks'] if c['id'] == 'warehouse_preparation')
    assert report['offline_ready'] and check['status'] == 'warning'
    assert 'Existing prepared libraries' in check['message']
    monkeypatch.setattr(diagnostics.shutil, 'which', lambda _: '/private/secret/node')
    report = diagnostics.collect_diagnostics()
    check = next(c for c in report['checks'] if c['id'] == 'warehouse_preparation')
    assert check['node_available'] and 'version was not probed' in check['message']
    assert '/private/secret' not in json.dumps(report)
