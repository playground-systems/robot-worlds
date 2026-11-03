"""Export a source bundle for browser review; use an external memory watchdog."""

from __future__ import annotations

import argparse
import json
import resource
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--decisions", type=Path, required=True, help="Original authored JSON containing views"
    )
    parser.add_argument("--name")
    args = parser.parse_args()
    decisions = json.loads(args.decisions.read_text())
    if (
        decisions.get("author", {}).get("model") != "gpt-6-astra"
        or decisions["author"].get("reasoning") != "high"
    ):
        raise ValueError("Use the recorded Astra-high camera decisions")
    resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
    from robot_worlds.source_scene_bundle import verify_source_scene_bundle
    from robot_worlds.source_scene_preview import export_source_scene_preview

    bundle = verify_source_scene_bundle(args.bundle)
    if decisions.get("source_sha256") != bundle.manifest["scene"]["original_sha256"]:
        raise ValueError("Source bundle differs from the authored preview input")
    result = export_source_scene_preview(
        args.bundle, args.output, views=decisions["views"], name=args.name
    )
    print(result, flush=True)


if __name__ == "__main__":
    main()
