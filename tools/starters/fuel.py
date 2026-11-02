"""Acquire the official hospital world's pinned external Fuel data, never code.

AWS's setup.sh names these 43 OpenRobotics models. Its script is not executed:
this adapter uses Robot Worlds' bounded ZIP downloader and verifies the reviewed
file list, small source XML hashes, versions, licenses and original attribution.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path

import httpx

from robot_worlds.artifacts import file_digest, write_json
from robot_worlds.asset_download import AssetDownloadError, download_fuel_asset
from robot_worlds.asset_sourcing import AssetCandidate

MANIFEST = Path(__file__).with_name("hospital-fuel.json")
MAX_BATCH_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_BATCH_EXTRACTED_BYTES = 512 * 1024 * 1024


class _LimitedResponse:
    def __init__(self, response, expected_bytes, budget):
        self.response, self.expected_bytes, self.budget = response, expected_bytes, budget

    def __getattr__(self, name):
        return getattr(self.response, name)

    def iter_bytes(self, chunk_size):
        count = 0
        for chunk in self.response.iter_bytes(chunk_size):
            count += len(chunk)
            self.budget[0] -= len(chunk)
            if count > self.expected_bytes or self.budget[0] < 0:
                raise AssetDownloadError("Hospital Fuel download exceeds the pinned archive byte budget")
            yield chunk
        if count != self.expected_bytes:
            raise AssetDownloadError("Hospital Fuel archive size does not match the reviewed source")


class _LimitedClient:
    def __init__(self, client, expected_bytes, budget):
        self.client, self.expected_bytes, self.budget = client, expected_bytes, budget

    @contextmanager
    def stream(self, *args, **kwargs):
        with self.client.stream(*args, **kwargs) as response:
            yield _LimitedResponse(response, self.expected_bytes, self.budget)


def _config_author(path):
    data = path.read_bytes()
    if len(data) > 1024 * 1024 or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("Hospital Fuel model.config is not bounded scene metadata")
    root = ET.fromstring(data)
    return root.findtext("author/name") or "", data.decode("utf-8")


def _verify_files(root, expected):
    actual = {}
    for path in root.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("Hospital source directory contains a link or special file")
        if path.is_file():
            actual[path.relative_to(root).as_posix()] = {"sha256": file_digest(path), "bytes": path.stat().st_size}
    if actual != expected:
        raise ValueError("Installed hospital Fuel source differs from the verified source")


def acquire_hospital_fuel(source: Path, cache: Path, *, client=None,
                          manifest_path: Path = MANIFEST, timeout: float = 300) -> list[dict]:
    """Return source records, installing exact original models under source/models.

    Records and a combined attribution/full-license notice are also saved as
    ``fuel-sources.json`` and ``FUEL-NOTICE.txt`` for the scene preparation step.
    First acquisition records all archive/file SHA-256 hashes; subsequent uses
    verify the existing cache before copying. No upstream scripts are imported.
    """
    source, cache, manifest_path = Path(source), Path(cache), Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    models = manifest["models"]
    if (not models or len(models) > 43 or len({model["name"] for model in models}) != len(models)
            or sum(model["archive_bytes"] for model in models) > MAX_BATCH_ARCHIVE_BYTES):
        raise ValueError("Hospital Fuel manifest exceeds its reviewed acquisition budget")
    legalcode_path = manifest_path.parent / "licenses/CC-BY-4.0.txt"
    if file_digest(legalcode_path) != manifest["license_legalcode_sha256"]:
        raise ValueError("Hospital Fuel license text changed")
    if client is None:
        with httpx.Client(follow_redirects=False) as owned:
            return acquire_hospital_fuel(source, cache, client=owned,
                                         manifest_path=manifest_path, timeout=timeout)
    deadline = time.monotonic() + timeout
    budget, expanded, records = [MAX_BATCH_ARCHIVE_BYTES], 0, []
    models_root = source / "models"
    models_root.mkdir(parents=True, exist_ok=True)
    for model in models:
        name, version = model["name"], str(model["version"])
        if model["owner"] != "OpenRobotics" or model["license"] != "CC-BY-4.0":
            raise ValueError("Hospital Fuel model is outside the reviewed publisher/license")
        canonical = f"https://fuel.gazebosim.org/1.0/OpenRobotics/models/{name}/{version}"
        if model["download_url"] != canonical + f"/{name}.zip":
            raise ValueError("Hospital Fuel model does not identify the reviewed pinned URL")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssetDownloadError("Hospital Fuel acquisition exceeded its time budget")
        candidate = AssetCandidate(id=f"fuel:OpenRobotics/{name}", source_url=canonical,
                                   revision=version, license=model["license"], metadata={
                                       "owner": "OpenRobotics", "name": name,
                                       "license_url": model["license_url"], "download_url": model["download_url"]})
        acquired = download_fuel_asset(candidate, cache / "hospital-fuel",
                                       client=_LimitedClient(client, model["archive_bytes"], budget),
                                       timeout=min(45, remaining))
        download = json.loads(acquired.manifest_path.read_text())
        files = download["files"]
        if (model.get("expected_archive_sha256") is not None
                and acquired.archive_sha256 != model["expected_archive_sha256"]):
            raise ValueError(f"{name}: Fuel archive differs from its pinned SHA-256")
        if (download["archive_bytes"] != model["archive_bytes"]
                or set(files) != set(model["expected_files"])):
            raise ValueError(f"{name}: Fuel files do not match the reviewed pinned package")
        for member, expected in model["expected_file_sha256"].items():
            if files.get(member, {}).get("sha256") != expected:
                raise ValueError(f"{name}: reviewed source metadata changed: {member}")
        expanded += sum(file["bytes"] for file in files.values())
        if expanded > MAX_BATCH_EXTRACTED_BYTES:
            raise ValueError("Hospital Fuel source exceeds the expanded batch size budget")
        author, original_config = _config_author(acquired.source_dir / "model.config")
        if author != model["author"]:
            raise ValueError(f"{name}: original source attribution differs from reviewed metadata")
        attribution = (f"{name}, OpenRobotics Fuel version {version}. Publisher: OpenRobotics. "
                       + (f"Original model.config author field: {author}. " if author else "No author name supplied in model.config. ")
                       + f"Source: {model['source_url']}. License: CC BY 4.0 ({model['license_url']}). "
                       + "Robot Worlds converts source visuals/materials and adapts static collision geometry; scene provenance describes those changes.")
        destination = models_root / name
        if destination.exists() or destination.is_symlink():
            if destination.is_symlink():
                raise ValueError("Hospital model destination is a link")
            _verify_files(destination, files)
        else:
            temporary = Path(tempfile.mkdtemp(prefix=".fuel-", dir=models_root))
            try:
                shutil.copytree(acquired.source_dir, temporary, dirs_exist_ok=True)
                _verify_files(temporary, files)
                temporary.rename(destination)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        record = {key: model[key] for key in ("name", "owner", "version", "license", "license_url",
                                               "source_url", "metadata_url", "download_url")}
        record.update(archive_sha256=acquired.archive_sha256, archive_bytes=download["archive_bytes"],
                      files=files, author=author, original_model_config=original_config,
                      attribution=attribution, notice_path="FUEL-NOTICE.txt")
        records.append(record)
        print(json.dumps({"hospital_fuel": name, "version": model["version"], "cache_hit": acquired.cache_hit,
                          "archive_bytes": download["archive_bytes"]}), flush=True)
    notice = ("External hospital model data from OpenRobotics Fuel\n\n"
              "The AWS scene files use MIT-0. The following model data is separately licensed CC BY 4.0. "
              "No endorsement by the source creators or publisher is implied.\n\n"
              + "\n\n".join(record["attribution"] for record in records)
              + "\n\nCreative Commons Attribution 4.0 International — full license text\n\n"
              + legalcode_path.read_text())
    (source / "FUEL-NOTICE.txt").write_text(notice)
    write_json(source / "fuel-sources.json", records)
    return records
