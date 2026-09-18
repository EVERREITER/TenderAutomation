"""Material errors block completion; presentation differences remain visible."""
import json
import httpx2
import pytest
from conftest import extraction, address
from test_requests import mocked_service, response_body
from tender_extraction.pipeline import run_file
from tender_extraction.readers.excel import read_excel
from tender_extraction.schemas import Option
from tender_extraction.validation import norm, text_outcome, validate, validation_status


@pytest.mark.parametrize('source,claim,outcome', [
    ('First._x000D_\nSecond.', 'First.\nSecond.', 'passed'),
    ('IVL Svenska Miljöinstituttet.', 'IVL Svenska Miljöinstitutet.', 'not_verifiable'),
    ('Use 10 mg per dose.', 'Use 100 mg per dose.', 'failed'),
    ('Must not use an ampoule.', 'Must use an ampoule.', 'failed'),
    ('Dose in mg.', 'Dose in ml.', 'failed'),
    ('Provide stability and dilution volume.', 'Provide stability.', 'failed'),
    ('Product A', 'Product B', 'failed'),
])
def test_text_severity(source, claim, outcome):
    assert text_outcome(source, claim) == outcome


def test_literal_excel_escape_is_not_decoded_twice():
    assert norm('_x005F_x000D_') == '_x005F_x000D_'


def test_blank_dropdown_entries_ignored_but_actual_options_required(sample):
    candidate = extraction()
    candidate.answer_fields[0].address = address('B2')
    candidate.answer_fields[0].control_type = 'excel_dropdown'
    candidate.options = [Option(id=str(i), field_id='f1', label=str(v), value=v,
        meaning=None, order=i, source_ids=['s1']) for i,v in enumerate(['Yes','No',0,False])]
    manifest = read_excel(sample[1])
    manifest['sheets'][0]['validations'][0]['options'] = ['Yes','No',0,False,None,'']
    assert validation_status(validate(candidate, manifest)) == 'completed'
    candidate.options.pop()  # False is an actual option, not a blank.
    checks = validate(candidate, manifest)
    assert validation_status(checks) == 'needs_review'
    assert any(c.code == 'dropdown_options' and c.severity == 'error' for c in checks)


@pytest.mark.parametrize('mutation', ['reference','address','duplicate','unrelated_quote'])
def test_material_errors_still_require_review(sample, mutation):
    candidate = extraction()
    if mutation == 'reference': candidate.questions[0].source_ids = ['missing']
    if mutation == 'address': candidate.answer_fields[0].address.sheet = 'Nonexistent'
    if mutation == 'duplicate': candidate.questions.append(candidate.questions[0].model_copy())
    if mutation == 'unrelated_quote': candidate.source_references[0].quote = 'Completely invented requirement'
    assert validation_status(validate(candidate, read_excel(sample[1]))) == 'needs_review'


def test_warnings_and_model_limitations_do_not_block_pipeline(sample, config):
    candidate = extraction()
    candidate.limitations = ['Unused blank template rows were not extracted.']
    candidate.questions[0].context_status = 'unknown'
    service = mocked_service(config, lambda req: httpx2.Response(200,json=response_body(candidate)))
    folder, run = run_file(sample[1], 'outputs', config, sample[0], service)
    service.close()
    result = json.loads((folder/'result.json').read_text(encoding='utf-8'))
    assert run['status'] == 'completed'
    assert result['limitations']
    assert any(c['severity'] == 'warning' for c in run['validation_checks'])
