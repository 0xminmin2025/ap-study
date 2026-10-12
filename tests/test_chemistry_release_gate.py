import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_all_chemistry_questions_meet_release_gate():
    base=ROOT/'docs/data/chemistry'
    mcq=json.loads((base/'questions/mcq.json').read_text())
    frq=json.loads((base/'questions/frq.json').read_text())
    assert len(mcq)==60 and len(frq)==7
    assert all(q.get('unit') for q in mcq+frq)
    assert all(q.get('explanation') for q in mcq)
    assert not any(q.get('answer_disputed') for q in mcq)
    assert all(q['answer'] in q['options'] and q['options'][q['answer']].strip() for q in mcq)
    for q in mcq:
        if q.get('images'):
            assert q.get('image') == q['images'][0]
        if q.get('image'):
            assert (base/q['image']).is_file()
    assert all(q.get('scoringGuideline') for q in frq)
    assert '+8.48kJ/mol' not in next(q for q in frq if q['number']==113)['scoringGuideline']
    q92=next(q for q in mcq if q['number']==92)
    assert q92['answer']=='C' and '22.5 mL' in q92['explanation'] and '8.3' in q92['explanation']
    q112=next(q for q in mcq if q['number']==112)
    assert 'Section II' not in q112['options']['D']


def test_site_lists_chemistry_subject():
    html=(ROOT/'docs/index.html').read_text()
    assert "chemistry:{name:'Chemistry'" in html
    assert 'function safeQuestionImagePath(q)' in html
    assert "typeof q.source==='string'" in html
