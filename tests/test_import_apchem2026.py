import json
import subprocess
import sys
from pathlib import Path
import pytest

PDF = Path('/Users/clawd/Downloads/ap化学2026_20260514_121801/化学2026MCQ.pdf')
KEY = Path('/Users/clawd/Downloads/ap化学2026_20260514_121801/AP26化学答案.txt')
DOCX = Path('/Users/clawd/Downloads/ap化学2026_20260514_121801/Barium Nitrate_ Enthalpy Change in Different Reactions.docx')


def run_import(tmp_path):
    if not all(path.is_file() for path in (PDF, KEY, DOCX)):
        pytest.skip('local AP Chemistry source files are unavailable')
    out = tmp_path / 'chemistry'
    subprocess.run([
        sys.executable, 'scripts/import_apchem2026.py',
        '--pdf', str(PDF), '--key', str(KEY), '--frq-solutions', str(DOCX),
        '--out', str(out),
    ], check=True)
    return out


def test_import_builds_60_mcq_and_7_frq(tmp_path):
    out = run_import(tmp_path)
    mcq = json.loads((out / 'questions/mcq.json').read_text())
    frq = json.loads((out / 'questions/frq.json').read_text())
    assert len(mcq) == 60
    assert [q['number'] for q in mcq] == list(range(53, 113))
    assert len(frq) == 7
    assert [q['number'] for q in frq] == list(range(113, 120))


def test_verified_key_resolves_known_conflicts(tmp_path):
    out = run_import(tmp_path)
    mcq = {q['number']: q for q in json.loads((out / 'questions/mcq.json').read_text())}
    assert mcq[60]['answer'] == 'C'
    assert mcq[60]['answer_key_pdf'] == 'D'
    assert mcq[60]['answer_key_txt'] == 'C'
    assert mcq[78]['answer'] == 'A'
    assert mcq[78]['answer_key_pdf'] == 'C'
    assert mcq[78]['answer_key_txt'] == 'A'
    assert mcq[90]['answer'] == 'A'
    assert mcq[90]['answer_key_pdf'] == mcq[90]['answer_key_txt'] == 'D'
    assert mcq[92]['answer'] == 'C'
    assert mcq[112]['answer'] == 'A'


def test_every_visible_mcq_has_nonempty_options_and_images_exist(tmp_path):
    out = run_import(tmp_path)
    mcq = json.loads((out / 'questions/mcq.json').read_text())
    for q in mcq:
        assert set(q['options']) == set('ABCD')
        assert all((v or '').strip() for v in q['options'].values())
        assert q['answer'] in q['options']
        if q.get('images'):
            assert q.get('image') == q['images'][0]
        for image in q.get('images', []):
            assert (out / image).exists(), (q['number'], image)


def test_frq_solutions_preserve_equations_and_correct_q113_calculation(tmp_path):
    out = run_import(tmp_path)
    frq = {q['number']: q for q in json.loads((out / 'questions/frq.json').read_text())}
    guide = frq[113]['scoringGuideline']
    assert '39.6 kJ/mol' in guide
    assert '+8.48kJ/mol' not in guide
    assert '2 Ba(NO₃)₂' in guide
    assert '[Xe]' in guide


def test_manifest_has_no_duplicates_or_unresolved_key_conflicts(tmp_path):
    out = run_import(tmp_path)
    report = json.loads((out / 'audit_report.json').read_text())
    assert report['duplicate_hashes'] == 0
    assert report['unresolved_key_conflicts'] == []
    assert report['empty_options'] == []
    assert all(not Path(source['file']).is_absolute() for source in report['sources'].values())


def test_import_rejects_unsafe_output_before_deleting_it(tmp_path):
    marker = tmp_path / 'keep.txt'
    marker.write_text('keep')
    result = subprocess.run([
        sys.executable, 'scripts/import_apchem2026.py', '--pdf', str(marker),
        '--key', str(marker), '--frq-solutions', str(marker), '--out', str(tmp_path),
    ], capture_output=True, text=True)
    assert result.returncode != 0
    assert marker.read_text() == 'keep'
