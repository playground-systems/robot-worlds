"""Audit a specific native household subset; never claim full release readiness.

This reads JUnit only. It does not start tests, model requests, CI or simulation.
"""
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

REQUIRED = {
    'test_household_sdk_flow': {
        'test_household_sensor_action_report_and_portable_reload[in_process]',
        'test_household_sensor_action_report_and_portable_reload[http]',
        'test_household_remote_failure_preserves_evidence_without_task_verdict[timeout]',
        'test_household_remote_failure_preserves_evidence_without_task_verdict[disconnect]',
        'test_household_remote_failure_preserves_evidence_without_task_verdict[invalid_action]',
        'test_household_cancel_between_actions_preserves_partial_report',
    },
    'test_asset_import_physics': {
        'test_original_textured_microwave_keeps_empty_cavity_and_passive_joints',
    },
    'test_sourced_household_physics': {
        'test_original_bowl_arm_search_does_not_overflow_contact_solver',
        'test_original_bowl_lifts_with_two_hands_and_public_torso_actuation',
        'test_sphere_settles_in_scanned_bowl_not_on_a_filled_collision_hull',
        'test_original_bowl_fits_and_stays_supported_inside_original_appliance',
        'test_rotated_original_round_bowl_keeps_exact_physical_containment_and_support',
        'test_native_wrong_bowl_inside_does_not_satisfy_target_identity',
        'test_native_partial_insertion_is_not_full_containment',
        'test_native_brief_recontact_does_not_pass_stable_support_duration',
        'test_sdk_runs_original_assets_from_mocked_generation_through_export',
    },
}


def audit(path: Path) -> dict:
    result = {
        'schema_version': 1,
        'scope': 'native household SDK integration, concavity and pre-placed support',
        'status': 'unverified',
        'full_release_accepted': False,
        'does_not_establish': [
            'Successful robot grasp, door opening, transport, insertion and release',
            'Live model generation or autonomous policy success',
            'Generated warehouse visual acceptance or current application screenshots',
            'Clean package installation or macOS execution',
        ],
        'checks': [],
    }
    try:
        if path.stat().st_size > 2*1024*1024:
            raise ValueError('JUnit exceeds the 2 MiB evidence limit')
        data = path.read_bytes()
        if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
            raise ValueError('JUnit declarations/entities are unsupported')
        root = ET.fromstring(data)
        if root.tag not in {'testsuites', 'testsuite'}:
            raise ValueError('Expected a JUnit testsuite or testsuites')
        cases = list(root.iter('testcase'))
        if not cases:
            raise ValueError('JUnit contains no test cases')
    except (OSError, ValueError, ET.ParseError) as error:
        result['error'] = str(error)
        return result
    for module, names in REQUIRED.items():
        for name in sorted(names):
            matches = [c for c in cases if c.get('name') == name
                       and c.get('classname', '').split('.')[-1] == module]
            status = 'missing'
            if len(matches) > 1:
                status = 'duplicate'
            elif matches:
                case = matches[0]
                status = ('failed' if case.find('failure') is not None or case.find('error') is not None
                          else 'skipped' if case.find('skipped') is not None else 'passed')
            result['checks'].append({'module': module, 'name': name, 'status': status})
    result['case_count'] = len(cases)
    result['failed_or_error_count'] = len(list(root.iter('failure')))+len(list(root.iter('error')))
    result['status'] = ('passed' if all(c['status'] == 'passed' for c in result['checks'])
                        and result['failed_or_error_count'] == 0 else 'failed')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('junit', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.junit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(f"Household subset: {result['status']}. Full release acceptance remains separate.")
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
