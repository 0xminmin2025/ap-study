import json
import subprocess
import sys
import importlib.util


SPEC = importlib.util.spec_from_file_location('enrich_apchem2026', 'scripts/enrich_apchem2026.py')
ENRICH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ENRICH)


def test_enricher_requires_valid_structured_results_and_writes_all_mcq(tmp_path):
    src = tmp_path / 'mcq.json'
    out = tmp_path / 'out.json'
    src.write_text(json.dumps([
        {'id':'chemistry_2026_53','type':'mcq','year':2026,'number':53,
         'text':'What mass?', 'options':{'A':'1','B':'2','C':'3','D':'4'}, 'answer':'B'}
    ]))
    fixtures = tmp_path / 'fixtures.json'
    fixtures.write_text(json.dumps({'53': {
        'answer':'B','unit':'Atomic Structure and Properties',
        'explanation':'Two concise sentences. The stoichiometric ratio gives choice B.'
    }}))
    subprocess.run([
        sys.executable,'scripts/enrich_apchem2026.py','--input',str(src),'--output',str(out),
        '--fixture-results',str(fixtures)
    ],check=True)
    q=json.loads(out.read_text())[0]
    assert q['unit']=='Atomic Structure and Properties'
    assert q['explanation']
    assert q['model_answer']=='B'
    assert q['answer_disputed'] is False
    assert q['review_model']=='fixture'


def test_enricher_flags_answer_disagreement(tmp_path):
    src=tmp_path/'mcq.json';out=tmp_path/'out.json';fixtures=tmp_path/'fixtures.json'
    src.write_text(json.dumps([{'id':'chemistry_2026_54','type':'mcq','year':2026,'number':54,
      'text':'Acid base?', 'options':{'A':'a','B':'b','C':'c','D':'d'},'answer':'D'}]))
    fixtures.write_text(json.dumps({'54':{'answer':'A','unit':'Acids and Bases','explanation':'Choice A follows from the conjugate pair.'}}))
    subprocess.run([sys.executable,'scripts/enrich_apchem2026.py','--input',str(src),'--output',str(out),'--fixture-results',str(fixtures)],check=True)
    q=json.loads(out.read_text())[0]
    assert q['answer_disputed'] is True
    assert q['model_answer']=='A'


def test_image_path_is_confined_and_validates_image_signature(tmp_path):
    root = tmp_path / 'chemistry'; questions = root / 'questions'; images = root / 'images'
    questions.mkdir(parents=True); images.mkdir()
    src = questions / 'mcq.json'; src.write_text('[]')
    good = images / 'figure.png'; good.write_bytes(b'\x89PNG\r\n\x1a\nmore')
    outside = tmp_path / 'secret.png'; outside.write_bytes(b'\x89PNG\r\n\x1a\nsecret')
    assert ENRICH.image_path({'image':'images/figure.png'}, src) == good.resolve()
    assert ENRICH.image_path({'image':'../secret.png'}, src) is None
    assert ENRICH.image_path({'image':str(outside)}, src) is None
    fake = images / 'fake.png'; fake.write_text('not an image')
    assert ENRICH.image_path({'image':'images/fake.png'}, src) is None


def test_endpoint_rejects_key_exfiltration_hosts():
    assert ENRICH.validate_endpoint('https://api.siliconflow.cn/v1/chat/completions')
    for endpoint in ('http://api.siliconflow.cn/v1', 'https://evil.example/v1',
                     'https://api.siliconflow.cn.evil.example/v1'):
        try:
            ENRICH.validate_endpoint(endpoint)
        except ValueError:
            pass
        else:
            raise AssertionError(endpoint)


def test_cache_key_changes_with_review_protocol(monkeypatch):
    q={'text':'x','options':{'A':'a','B':'b','C':'c','D':'d'}}
    before=ENRICH.cache_key(q, 'model')
    monkeypatch.setattr(ENRICH, 'CACHE_VERSION', 'new-version')
    assert ENRICH.cache_key(q, 'model') != before
