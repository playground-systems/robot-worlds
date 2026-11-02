"""Acquire untouched MolmoSpaces FloorPlan201 sources; never compile or simulate.

Uses bounded HTTP ranges into the publisher's versioned shards and an existing
system zstd executable. Category archives are retained; only referenced THOR
files and license notices are extracted. No MolmoSpaces installation is needed.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

DATASET_REVISION = "5f802a46816ffc3520e59b5ef49fa8dafee8eeca"
CODE_REVISION = "22ee21c0fb1d6f2bbccd4a7d5ee6f6dccc488bf1"
BASE = f"https://huggingface.co/datasets/allenai/molmospaces/resolve/{DATASET_REVISION}/"
SCENES = "mujoco/scenes/ithor/20251217_with_occupancy"
OBJECTS = "mujoco/objects/thor/20251117"
APPROVED_CATEGORIES = {
    "Bathroom Objects", "Bedroom Objects", "Common Objects", "Kitchen Objects",
    "Living Room Objects", "Living Room and Bedroom Objects", "Miscellaneous Objects",
}
CHUNK = 128 * 1024
MAX_EXTRACTED_BYTES = 1_000_000_000


def sha256(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            result.update(chunk)
    return result.hexdigest()


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def safe_path(name):
    result = PurePosixPath(name)
    if not name or result.is_absolute() or ".." in result.parts or "\\" in name:
        raise ValueError("Unsafe source archive path")
    return result


class Acquisition:
    def __init__(self, output, limit):
        self.root = output.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.root).free < 2_000_000_000:
            raise ValueError("Source acquisition requires at least 2 GB of free disk space")
        self.ledger_path = self.root / "download-ledger.json"
        self.ledger = (json.loads(self.ledger_path.read_text()) if self.ledger_path.exists()
                       else {"downloaded_bytes": 0, "files": [], "transfers": []})
        if not 0 < limit <= 650_000_000 or self.ledger["downloaded_bytes"] > limit:
            raise ValueError("Download budget must cover existing downloads and be at most 650 MB")
        self.ledger.update(download_limit_bytes=limit,
                           policy="Untouched public source acquisition; no simulation or asset design")
        self.ledger.setdefault("transfers", [])
        self.zstd = shutil.which("zstd")
        if not self.zstd:
            raise ValueError("An existing system zstd executable is required; nothing is installed")
        self.extracted_bytes = sum(p.stat().st_size for folder in ("source", "source-notices")
                                   for p in (self.root / folder).rglob("*") if p.is_file())
        write_json(self.ledger_path, self.ledger)

    def fetch(self, relative, url, *, size_limit=2_000_000, byte_range=None):
        destination = self.root.joinpath(*safe_path(relative).parts)
        if destination.is_symlink() or not destination.resolve().is_relative_to(self.root):
            raise ValueError("Source download destination escapes the acquisition directory")
        previous = next((record for record in self.ledger["files"] if record["path"] == relative), None)
        if destination.exists():
            if not previous or previous["url"] != url or sha256(destination) != previous["sha256"]:
                raise ValueError("Existing source does not match its acquisition ledger: " + relative)
            return destination
        if size_limit > self.ledger["download_limit_bytes"] - self.ledger["downloaded_bytes"]:
            raise ValueError("Remaining download budget is insufficient: " + relative)
        headers = {"Range": f"bytes={byte_range[0]}-{byte_range[1]}"} if byte_range else {}
        transfer = {"path": relative, "url": url, "bytes_received": 0, "status": "running"}
        self.ledger["transfers"].append(transfer)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = None
        digest = hashlib.sha256()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=40) as response:
                if byte_range:
                    expected = f"bytes {byte_range[0]}-{byte_range[1]}/"
                    if response.status != 206 or not response.headers.get("Content-Range", "").startswith(expected):
                        raise ValueError("Source server did not honor the bounded byte range")
                if int(response.headers.get("Content-Length", "0")) > size_limit:
                    raise ValueError("Source response exceeds its byte limit")
                with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".download-", delete=False) as stream:
                    partial = Path(stream.name)
                    while True:
                        data = response.read(min(CHUNK, size_limit - transfer["bytes_received"] + 1))
                        if not data:
                            break
                        transfer["bytes_received"] += len(data)
                        self.ledger["downloaded_bytes"] += len(data)
                        if (transfer["bytes_received"] > size_limit
                                or self.ledger["downloaded_bytes"] > self.ledger["download_limit_bytes"]):
                            raise ValueError("Source download exceeded its byte limit")
                        stream.write(data)
                        digest.update(data)
            if byte_range and transfer["bytes_received"] != byte_range[1] - byte_range[0] + 1:
                raise ValueError("Source range was truncated")
            partial.replace(destination)
            record = {"path": relative, "url": url, "bytes": transfer["bytes_received"], "sha256": digest.hexdigest()}
            if byte_range:
                record["range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
            self.ledger["files"].append(record)
            transfer["status"] = "completed"
            print(json.dumps({"download": relative, "bytes": record["bytes"],
                              "total_network_bytes": self.ledger["downloaded_bytes"]}), flush=True)
            return destination
        except Exception:
            transfer["status"] = "failed"
            raise
        finally:
            if partial is not None:
                partial.unlink(missing_ok=True)
            write_json(self.ledger_path, self.ledger)

    def archive(self, source, row):
        start = row["offset"]
        return self.fetch("archives/" + row["path"], BASE + source + f"/shards/{row['shard_id']:05d}.tar",
                          size_limit=row["size"], byte_range=(start, start + row["size"] - 1))

    def extract(self, archive, row, destination, wanted=None):
        if not 0 < row["inflated_size"] <= 1_000_000_000:
            raise ValueError("Unexpected source archive inflation budget")
        found = set()
        regular_bytes = 0
        process = subprocess.Popen([self.zstd, "-d", "-c", str(archive)], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        try:
            with tarfile.open(fileobj=process.stdout, mode="r|", bufsize=CHUNK) as source:
                for member in source:
                    name = safe_path(member.name)
                    if member.isdir():
                        continue
                    if not member.isfile():
                        if wanted is None or str(name) in wanted:
                            raise ValueError("Referenced source asset is not a regular file: " + member.name)
                        continue
                    regular_bytes += member.size
                    if regular_bytes > row["inflated_size"] or member.size > 128_000_000:
                        raise ValueError("Source archive/member inflation limit exceeded")
                    notice = name.name.lower() in {
                        "license", "license.txt", "license.md", "licence", "licence.txt",
                        "copyright", "copyright.txt", "notice", "notice.txt", "notice.md",
                        "attribution", "attribution.txt", "attribution.md", "credits.txt", "credits.md",
                    }
                    if wanted is not None and str(name) not in wanted and not notice:
                        continue
                    target = (destination.joinpath(*name.parts) if wanted is None or str(name) in wanted
                              else self.root / "source-notices" / archive.name / Path(*name.parts))
                    if target.is_symlink() or not target.resolve().is_relative_to(self.root):
                        raise ValueError("Source destination escapes the acquisition directory")
                    incoming = source.extractfile(member)
                    if target.exists():
                        digest = hashlib.sha256()
                        for data in iter(lambda: incoming.read(CHUNK), b""):
                            digest.update(data)
                        if digest.hexdigest() != sha256(target):
                            raise ValueError("Existing extracted original differs: " + str(target))
                    else:
                        if self.extracted_bytes + member.size > MAX_EXTRACTED_BYTES:
                            raise ValueError("Selected source extraction exceeds its total limit")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        temporary = None
                        try:
                            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".source-", delete=False) as stream:
                                temporary = Path(stream.name)
                                shutil.copyfileobj(incoming, stream, length=CHUNK)
                            if temporary.stat().st_size != member.size:
                                raise ValueError("Truncated source archive member")
                            temporary.replace(target)
                        finally:
                            if temporary is not None:
                                temporary.unlink(missing_ok=True)
                        self.extracted_bytes += member.size
                    found.add(str(name))
            # Drain bounded remaining tar padding so decompression exits cleanly.
            padding = 0
            while data := process.stdout.read(CHUNK):
                padding += len(data)
                if padding > 1_000_000:
                    raise ValueError("Unexpected source tar trailer")
            error = process.stderr.read().decode(errors="replace")
            if process.wait(timeout=10):
                raise ValueError("Source decompression failed: " + error)
        finally:
            process.stdout.close()
            process.stderr.close()
            if process.poll() is None:
                process.kill()
                process.wait()
        if wanted is not None and wanted - found:
            raise ValueError("Source archive lacks referenced files: " + repr(sorted(wanted - found)))
        return found


def prepare(output, limit):
    acquisition = Acquisition(output, limit)
    root = acquisition.root
    readme = acquisition.fetch("metadata/README.md", BASE + "README.md")
    license_source = acquisition.fetch("metadata/molmospaces-license-utils.py",
        f"https://raw.githubusercontent.com/allenai/molmospaces/{CODE_REVISION}/molmo_spaces/utils/license_utils.py")
    acquisition.fetch("metadata/molmospaces-code-LICENSE.txt",
        f"https://raw.githubusercontent.com/allenai/molmospaces/{CODE_REVISION}/LICENSE")
    scene_rows = json.loads(acquisition.fetch("metadata/arrow_table.json", BASE + SCENES + "/arrow_table.json").read_text())
    object_rows = json.loads(acquisition.fetch("metadata/thor-arrow_table.json", BASE + OBJECTS + "/arrow_table.json").read_text())
    row = next(row for row in scene_rows if row["path"] == "ithor_FloorPlan201_physics.tar.zst")
    scene_dir = root / "source/scenes/ithor"
    acquisition.extract(acquisition.archive(SCENES, row), row, scene_dir)
    xml = scene_dir / "FloorPlan201_physics.xml"
    tree = ET.parse(xml)
    if tree.findall(".//include"):
        raise ValueError("This pinned scene unexpectedly contains external XML includes")
    dependencies = {}
    references = []
    for element in tree.findall(".//*[@file]"):
        path = (xml.parent / element.get("file")).resolve()
        if not path.is_relative_to(root / "source"):
            raise ValueError("Source file reference escapes the source directory")
        record = {"element": element.tag, "name": element.get("name"), "source_file": element.get("file"),
                  "path": str(path.relative_to(root))}
        references.append(record)
        if path.is_relative_to(root / "source/objects/thor"):
            relative = path.relative_to(root / "source/objects/thor").as_posix()
            dependencies.setdefault(relative.split("/", 1)[0], set()).add(relative)
        elif not path.is_file():
            raise ValueError("Scene archive omitted a scene-local asset")
    if set(dependencies) != APPROVED_CATEGORIES:
        raise ValueError("Scene dependency categories differ from the seven selected by Astra high")
    # Read the publisher's literal THOR license declaration without importing its code.
    declaration = ast.parse(license_source.read_text())
    license_info = next(ast.literal_eval(node.value) for node in declaration.body
                        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "DEFAULT_LICENSE" for t in node.targets))
    license_info.update(declaration_url=f"https://github.com/allenai/molmospaces/blob/{CODE_REVISION}/molmo_spaces/utils/license_utils.py",
                        declaration_sha256=sha256(license_source),
                        attribution="Model(s) by the Allen Institute for AI (Ai2), licensed under CC BY 4.0.")
    packets = {category: next(row for row in object_rows if row["path"] == f"thor_{category}.tar.zst")
               for category in dependencies}
    manifest = {"status": "acquiring", "dataset_revision": DATASET_REVISION, "scene": "FloorPlan201_physics.xml",
                "scene_resource_version": "20251217_with_occupancy", "object_resource_version": "20251117",
                "source_xml_sha256": sha256(xml), "license": license_info,
                "publisher_data_card": str(readme.relative_to(root)),
                "required_archives": [{**packets[category], "referenced_files": len(paths)} for category, paths in dependencies.items()],
                "references": references, "native_compiled": False, "source_modified": False}
    write_json(root / "dependency-manifest.json", manifest)
    for category, wanted in dependencies.items():
        packet = packets[category]
        archive = acquisition.archive(OBJECTS, packet)
        acquisition.extract(archive, packet, root / "source/objects/thor", wanted)
        print(json.dumps({"resolved_category": category, "referenced_files": len(wanted)}), flush=True)
    for record in references:
        path = root / record["path"]
        record.update(bytes=path.stat().st_size, sha256=sha256(path), license=license_info["license"])
        if path.is_relative_to(root / "source/objects/thor"):
            category = path.relative_to(root / "source/objects/thor").parts[0]
            record["origin_archive"] = packets[category]["path"]
        else:
            record["origin_archive"] = row["path"]
    manifest.update(status="sources_resolved", downloaded_bytes=acquisition.ledger["downloaded_bytes"],
                    extracted_regular_bytes=acquisition.extracted_bytes,
                    unique_referenced_files=len({r["path"] for r in references}),
                    limit="Original source and publisher license declarations only; no native, reachability or visual-quality verification")
    write_json(root / "dependency-manifest.json", manifest)
    print(json.dumps({key: manifest[key] for key in ("status", "downloaded_bytes", "unique_referenced_files", "extracted_regular_bytes")}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-download-bytes", type=int, default=650_000_000)
    args = parser.parse_args()
    os.nice(10)
    prepare(args.output, args.max_download_bytes)


if __name__ == "__main__":
    main()
