"""JUnit audit fixtures only: no CI, subprocess, physics, graphics or model calls."""
import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location('household_evidence', Path(__file__).parents[1] / 'tools/check_household_evidence.py')
evidence = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(evidence)


def junit(tmp_path, change=lambda _: None):
    root = ET.Element('testsuites')
    suite = ET.SubElement(root, 'testsuite')
    for module, names in evidence.REQUIRED.items():
        for name in names:
            ET.SubElement(suite, 'testcase', classname='tests.'+module, name=name, time='0.1')
    change(suite)
    path = tmp_path / 'junit.xml'
    ET.ElementTree(root).write(path)
    return path


def test_complete_native_subset_is_not_full_release_acceptance(tmp_path):
    result = evidence.audit(junit(tmp_path))
    assert result['status'] == 'passed'
    assert len(result['checks']) == 11
    assert result['full_release_accepted'] is False
    assert any('Successful robot grasp' in claim for claim in result['does_not_establish'])


@pytest.mark.parametrize('failure', ['skipped', 'failure', 'error', 'missing', 'duplicate', 'wrong_module', 'extra_failure'])
def test_missing_skipped_duplicated_or_failing_native_checks_cannot_pass(tmp_path, failure):
    def change(suite):
        case = suite[0]
        if failure in {'skipped', 'failure', 'error'}:
            ET.SubElement(case, failure)
        elif failure == 'missing':
            suite.remove(case)
        elif failure == 'duplicate':
            ET.SubElement(suite, 'testcase', case.attrib)
        elif failure == 'wrong_module':
            case.set('classname', 'synthetic_only')
        else:
            ET.SubElement(ET.SubElement(suite, 'testcase', name='extra'), 'failure')
    result = evidence.audit(junit(tmp_path, change))
    assert result['status'] == 'failed'
    assert not result['full_release_accepted']


@pytest.mark.parametrize('content', [None, '<broken', '<testsuites/>', '<anything/>'])
def test_unavailable_or_empty_evidence_stays_unverified(tmp_path, content):
    path = tmp_path / 'junit.xml'
    if content is not None:
        path.write_text(content)
    result = evidence.audit(path)
    assert result['status'] == 'unverified' and result['error']
