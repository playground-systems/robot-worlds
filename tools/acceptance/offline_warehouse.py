"""Replay a saved generated plan and decisions with native physics and no model calls.

A dense-scene storage regression, not a new autonomous policy success claim.
Run with an installed package, the reviewed prepared library, and source fixtures.
"""
import argparse
import copy
import faulthandler
import gc
import json
import resource
import shutil
import sys
import time
import zipfile
from pathlib import Path

from prepare_floor_stencils import prepare

from robot_worlds import generate, load
from robot_worlds.runtime import Runtime
from robot_worlds.sdk import Report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--library', required=True, type=Path)
    parser.add_argument('--fixture', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    faulthandler.dump_traceback_later(60, repeat=True)
    def phase(name):
        print(f"{time.monotonic()-start:.2f}s {name}", flush=True)
    graphics = args.output/'graphics'
    prepare(graphics, ['RECEIVING', 'STORAGE', 'PICK & PACK', 'SHIPPING', 'KEEP CLEAR'])
    plan = json.loads((args.fixture/'live-plan.json').read_text())
    for obj in plan['objects']:
        for field in ('mesh_path', 'texture_path', 'source_notice'):
            if (obj.get(field) or '').startswith('@graphics/'):
                obj[field] = str((graphics/obj[field].split('/', 1)[1]).resolve())
    actions = json.loads((args.fixture/'recorded-actions.json').read_text())

    class SavedPlan:
        def complete_json(self, *args):
            return copy.deepcopy(plan)

    class RecordedActions:
        provenance = {'kind': 'recorded action replay for storage regression', 'autonomous': False,
                      'model_calls': 0, 'privileged_state_reads': False}
        def reset(self, context):
            assert set(context) == {'task', 'robot', 'action_contract', 'sensor_contract'}
            self.actions = iter(actions)
            phase("policy reset")
        def act(self, observation):
            assert observation['image'].startswith(b'\x89PNG')
            assert 'objects' not in observation and 'position' not in observation['robot']
            phase("sensor observation received")
            return next(self.actions, {'tool': 'finish', 'arguments': {}})

    phase("generation started")
    world = generate('Replay the saved generated facility for a deterministic storage regression.',
                     client=SavedPlan(), asset_library=args.library, online_assets=False,
                     max_repairs=0, output=args.output/'worlds')
    assert world.manifest['validation']['status'] == 'ready'
    print('validated', len(world.spec.objects), 'components', flush=True)
    # Retain the built-in runtime only for acceptance auditing. The policy still
    # sees the normal sensor contract. Avoid decoding the full saved trajectory
    # twice: regrade below already verifies its hash, samples and provenance.
    observed_runtimes = []
    def native_runtime(*positional, **kwargs):
        runtime = Runtime(*positional, **kwargs)
        observed_runtimes.append(runtime)
        return runtime
    report = world.evaluate(RecordedActions(), runtime_factory=native_runtime, max_steps=len(actions)+1, max_sim_seconds=60,
                            max_replay_frames=8, output=args.output/'runs')
    phase("evaluation completed")
    assert report.summary['valid'] == 1, report.summary
    episode = report.result['episodes'][0]
    trace_path = report.path/'episode-000'/episode['trace_evidence']['file']
    assert trace_path.suffix == '.gz' and trace_path.stat().st_size < 256*1024*1024
    phase("recorded states audit started")
    trace = observed_runtimes[0].trace
    assert len(trace) > 500, 'Regression did not exercise a long enough dense trace'
    assert all(len(frame['objects']) == len(world.spec.objects) for frame in trace)
    assert len(trace) <= episode['trace_evidence']['sample_count'] <= len(trace)+1
    del trace
    observed_runtimes.clear()
    gc.collect()
    phase("recorded states verified; disk integrity checked by portable regrade below")
    for artifact, name in ((world, 'world'), (report, 'report')):
        archive = artifact.export(args.output/(name+'.zip'))
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(args.output/('restored-'+name))
    phase("exports extracted")
    restored = load(args.output/'restored-world')
    saved = Report(args.output/'restored-report')
    assert restored.id == world.id and saved.summary == report.summary
    assert saved.regrade(restored, output=args.output/'regraded').summary == report.summary
    phase("portable regrade verified")
    faulthandler.cancel_dump_traceback_later()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    record = {'kind': 'mocked plan replay and recorded actions; native physics, sensors, grading and portable artifacts',
              'model_calls': 0, 'autonomous': False, 'world_id': world.id, 'report_id': report.path.name,
              'components': len(world.spec.objects), 'samples': episode['trace_evidence']['sample_count'],
              'trace_bytes': trace_path.stat().st_size, 'sim_seconds': episode['sim_seconds'],
              'summary': report.summary, 'portable_reload_and_regrade': True,
              'elapsed_seconds': time.monotonic()-start,
              'peak_process_memory_bytes': rss if sys.platform == 'darwin' else rss*1024}
    (args.output/'acceptance.json').write_text(json.dumps(record, indent=2)+'\n')
    for name in ('restored-world', 'restored-report', 'regraded'):
        shutil.rmtree(args.output/name)
    print(json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
