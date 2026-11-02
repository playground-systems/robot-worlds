"""Command line entry point for generation, evaluation and the local workbench."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True, help="Generate physical robot worlds and evaluate agents.")


@app.command()
def prepare_warehouse(data_dir: Path = Path(".robot-worlds"), output: Path | None = None,
                      asset: list[str] = typer.Option([], help="Prepare only this source ID; repeat for a subset. Default: all five reviewed assets."),
                      timeout: float = 300):
    """Install reusable warehouse assets, without model calls or native simulation."""
    from robot_worlds.artifacts import public_error
    from robot_worlds.warehouse_library import prepare_warehouse_library

    def progress(event):
        if event["type"] == "asset_preparing":
            typer.echo(f"[{event['index']}/{event['total']}] Preparing {event['asset_id']}…")
        elif event["type"] == "asset_prepared":
            typer.echo(f"  {event['components']} components; {event['license']}")
        else:
            typer.echo("Saving portable asset library…")
    try:
        result = prepare_warehouse_library(output or data_dir / "asset-library",
            cache_dir=data_dir / "asset-cache", asset_ids=asset or None, timeout=timeout, on_event=progress)
    except (ValueError, RuntimeError, OSError) as error:
        typer.echo(public_error(error)["message"], err=True)
        raise typer.Exit(1) from error
    typer.echo(f"Installed {len(result.asset_ids)} assets in {result.seconds:.2f}s: {result.path}")
    typer.echo("Asset preparation complete. Native scene validation still runs during generation.")
    if output is None:
        typer.echo("The workbench uses this library for new generation and edits with the same --data-dir.")
    typer.echo(f"For CLI generation or edits, pass --asset-library {result.path}")


@app.command()
def starters():
    """List the four bundled source environments; no model key is needed."""
    from robot_worlds.starter_environments import starter_catalog
    for entry in starter_catalog():
        typer.echo(f"{entry['id']:12} {entry['name']} · {entry['license']}")


@app.command('add-starter')
def add_starter_command(identifier: str, output: Path = Path('.robot-worlds/worlds')):
    """Add a reviewed source environment to your workspace without model calls."""
    from robot_worlds.starter_environments import add_starter
    world = add_starter(identifier, output, on_event=lambda e: typer.echo(e.get('message', e['type'])))
    typer.echo(f"{world.id}  {world.path}")


@app.command()
def revalidate(world: Path, output: Path | None = None):
    """Run native checks for this installation, without model calls or scene edits."""
    from robot_worlds.sdk import load
    revised = load(world).revalidate(output=output)
    typer.echo(str(revised.path))
    typer.echo(json.dumps(revised.manifest['validation'], indent=2))
    if revised.manifest['validation']['status'] != 'ready':
        raise typer.Exit(2)


@app.command()
def doctor(json_output: bool = typer.Option(False, "--json"),
           model: str = "gpt-6-astra", data_dir: Path = Path(".robot-worlds")):
    """Check installation offline without starting physics or calling a model."""
    from robot_worlds.diagnostics import collect_diagnostics, format_diagnostics
    report = collect_diagnostics(model=model, data_dir=data_dir)
    typer.echo(format_diagnostics(report, as_json=json_output))
    if not report["offline_ready"]:
        raise typer.Exit(1)


def _progress(event: dict):
    if event["type"] in ("candidate", "preview"):
        typer.echo("World preview generated; validating geometry and physics…")
    elif event["type"] == "validation":
        errors = event.get("errors", [])
        typer.echo(f"Validation attempt {event.get('attempt', '?')}: " + ("passed" if not errors else "; ".join(errors)))
    elif event["type"] == "step":
        typer.echo(f"Episode {event['episode'] + 1}, step {event['step'] + 1}: {event['action']['tool']}")
    else:
        typer.echo(event["type"].replace("_", " "))


@app.command()
def generate(prompt: str, output: Path = Path("worlds"), model: str = "gpt-6-astra",
             reasoning: str = "medium", max_repairs: int = 2,
             max_cost_usd: float = 5, max_model_seconds: float = 600,
             scene_profile: str = "standard", max_objects: int = 5000, min_objects: int = 0,
             asset_library: Path | None = typer.Option(None, exists=True, file_okay=False, help="Portable source asset library directory.")):
    """Generate and validate a new world from a text prompt."""
    from robot_worlds.sdk import generate as generate_world
    world = generate_world(prompt, output=output, model=model, reasoning=reasoning,
                           max_repairs=max_repairs, on_event=_progress,
                           scene_profile=scene_profile, max_objects=max_objects, min_objects=min_objects,
                           asset_library=asset_library,
                           client_options={"max_cost_usd": max_cost_usd, "max_seconds": max_model_seconds,
                                           "max_output_tokens": 16384})
    typer.echo(str(world.path))
    typer.echo(json.dumps(world.manifest["validation"], indent=2))
    if world.manifest["validation"]["status"] != "ready":
        raise typer.Exit(2)


@app.command()
def evaluate(world: Path, output: Path = Path("runs"), model: str = "gpt-6-astra",
             reasoning: str = "medium", episodes: int = 1, max_steps: int = 30,
             max_sim_seconds: float = 120, seed: int = 0, policy: str | None = None,
             remote_url: str | None = None, max_cost_usd: float = 5,
             max_model_seconds: float = 600, open_report: bool = False,
             workers: int = 1, graphics_backend: str = "auto"):
    """Run an LLM robot agent, or a local module:policy object, in a saved world."""
    from robot_worlds.policy import LLMAgent
    from robot_worlds.sdk import load
    if policy and remote_url:
        raise typer.BadParameter("Choose a Python policy or a remote service")
    if not 1 <= workers <= 32:
        raise typer.BadParameter("Workers must be between 1 and 32")
    if graphics_backend not in {"auto", "egl"}:
        raise typer.BadParameter("Graphics backend must be auto or egl")
    execution = dict(output=output, episodes=episodes, max_steps=max_steps,
                     max_sim_seconds=max_sim_seconds, seed=seed, on_event=_progress)
    if workers > 1 or graphics_backend != "auto":
        from robot_worlds.batch import evaluate_batch
        if policy:
            raise typer.BadParameter("Spawned execution supports LLM or remote policies; use one auto worker for Python objects")
        if remote_url:
            config = {"kind": "remote", "base_url": remote_url,
                      "max_steps": max_steps, "max_seconds": max_model_seconds}
            if os.environ.get("ROBOT_WORLDS_POLICY_TOKEN"):
                config["token_env"] = "ROBOT_WORLDS_POLICY_TOKEN"
        else:
            config = {"kind": "llm", "model": model, "reasoning": reasoning,
                      "max_steps": max_steps, "max_cost_usd": max_cost_usd,
                      "max_seconds": max_model_seconds}
        report = evaluate_batch(load(world), policy_config=config, workers=workers,
                                graphics_backend=graphics_backend, **execution)
        _show_report(report, open_report)
        return
    if remote_url:
        from robot_worlds.remote_policy import RemotePolicy
        agent = RemotePolicy(remote_url, token=os.environ.get("ROBOT_WORLDS_POLICY_TOKEN"),
                             max_steps=max_steps, max_seconds=max_model_seconds)
    elif policy:
        module_name, separator, attribute = policy.partition(":")
        if not separator or not module_name or not attribute:
            raise typer.BadParameter("Policy entrypoint must be module:attribute")
        agent = getattr(importlib.import_module(module_name), attribute)
        if isinstance(agent, type):
            agent = agent()
    else:
        agent = LLMAgent(model=model, reasoning=reasoning, max_steps=max_steps,
                         max_cost_usd=max_cost_usd, max_seconds=max_model_seconds)
    try:
        report = load(world).evaluate(agent, **execution)
    finally:
        if remote_url:
            agent.close()
    _show_report(report, open_report)


def _show_report(report, open_report):
    typer.echo(json.dumps(report.summary, indent=2))
    typer.echo(str(report.path / "report.html"))
    if open_report:
        report.open()


@app.command()
def serve(data_dir: Path = Path(".robot-worlds"), port: int = 8765):
    """Open the self-hosted API and browser workbench on localhost."""
    import uvicorn

    from robot_worlds.server import create_app
    typer.echo(f"Robot Worlds: http://127.0.0.1:{port}")
    uvicorn.run(create_app(data_dir), host="127.0.0.1", port=port)


@app.command()
def inspect(world: Path):
    """Print a saved world's task, identity and validation evidence."""
    from robot_worlds.sdk import load
    bundle = load(world)
    typer.echo(json.dumps({"id": bundle.id, "task": bundle.spec.task,
                           "validation": bundle.manifest["validation"]}, indent=2))


@app.command()
def refine(world: Path, prompt: str, model: str = "gpt-6-astra", max_repairs: int = 2,
           scope: list[str] = typer.Option([], help="Entity allowed to change; repeat to select more."),
           allow_task_changes: bool = False, allow_physics_changes: bool = False,
           max_cost_usd: float = 5, max_model_seconds: float = 600,
           asset_library: Path | None = typer.Option(None, exists=True, file_okay=False, help="Portable source asset library directory.")):
    """Edit named scene entities and save a validated version with its actual diff."""
    from robot_worlds.sdk import load
    revised = load(world).refine(prompt, model=model, max_repairs=max_repairs, on_event=_progress,
        scope=scope or None, allow_task_changes=allow_task_changes,
        allow_physics_changes=allow_physics_changes, asset_library=asset_library,
        client_options={"max_cost_usd": max_cost_usd, "max_seconds": max_model_seconds})
    _show_edit(revised)


@app.command()
def repair(world: Path, prompt: str = "Fix unresolved validation failures while preserving the task and physical properties.",
           model: str = "gpt-6-astra", max_repairs: int = 2,
           scope: list[str] = typer.Option([], help="Entity allowed to change; repeat to select more."),
           max_cost_usd: float = 5, max_model_seconds: float = 600,
           asset_library: Path | None = typer.Option(None, exists=True, file_okay=False, help="Portable source asset library directory.")):
    """Diagnose and repair a saved scene without weakening its mission or physics."""
    from robot_worlds.sdk import load
    revised = load(world).repair(prompt, model=model, max_repairs=max_repairs, on_event=_progress,
        scope=scope or None, asset_library=asset_library,
        client_options={"max_cost_usd": max_cost_usd, "max_seconds": max_model_seconds})
    _show_edit(revised)


def _show_edit(revised):
    typer.echo(str(revised.path))
    typer.echo(json.dumps({"changes": revised.manifest.get("edit"),
                           "validation": revised.manifest["validation"]}, indent=2))
    if revised.manifest["validation"]["status"] != "ready":
        raise typer.Exit(2)


@app.command()
def regrade(run: Path, world: Path):
    """Apply a revised rubric to recorded evidence from the same physical world."""
    from robot_worlds.sdk import Report, load
    report = Report(run).regrade(load(world))
    typer.echo(str(report.path / "report.html"))


if __name__ == "__main__":
    app()
