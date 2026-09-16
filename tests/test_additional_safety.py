"""Test evidence checks, coordinate handling, and additional failure boundaries.

Pytest discovers test_* functions for sparse range coverage, OCR rotations,
Excel rules and protection, malformed JSON, actual HTTP usage fields, input
limits, cache-group separation, key collisions, missing-question evidence, and
native PDF form fields. All fixtures and service responses remain local or
mocked; no Azure requests are made.
"""
import json
import httpx2
import pytest
from pydantic import ValidationError
from openpyxl import load_workbook
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles import Protection
from tender_extraction.schemas import Attribute,Completeness,MissingItem
from tender_extraction.readers.excel import read_excel
from tender_extraction.readers.documents import read_pdf
from tender_extraction.validation import validate,covered,ocr_polygon_to_pdf,enrich
from tender_extraction.services.openai_client import build_request
from tender_extraction.errors import ExtractionError
from conftest import extraction,address
from test_requests import mocked_service,response_body


def test_range_coverage_does_not_accept_one_cell_as_large_region():
    assert not covered("A1:Z100",["A1"])
    assert covered("A1:B1048576",["A1:A1048576","B1:B1048576"])
    assert not covered("A1:B3",["A1:A3","B1","B3"])


@pytest.mark.parametrize("rotation,polygon",[(0,[1,1,2,1,2,2,1,2]),(90,[9,1,10,1,10,2,9,2]),(180,[6,9,7,9,7,10,6,10]),(270,[1,6,2,6,2,7,1,7])])
def test_ocr_coordinate_transform(rotation,polygon):
    pdf={"width":576,"height":792,"rotation":rotation,"user_unit":1}
    ocr={"unit":"inch","width":11 if rotation in (90,270) else 8,"height":8 if rotation in (90,270) else 11}
    assert ocr_polygon_to_pdf(polygon,ocr,pdf)==[72,72,144,144]
    ocr["unit"]="pixel"
    assert ocr_polygon_to_pdf(polygon,ocr,pdf) is None


def test_static_rules_and_protection(sample):
    p=sample[1]; w=load_workbook(p); s=w["Fragen"]
    r=DataValidation(type="decimal",operator="between",formula1="0",formula2="14"); s.add_data_validation(r); r.add("B1")
    s["B1"].protection=Protection(locked=True); w.save(p); w.close()
    e=extraction(); e.attributes=[Attribute(owner_type="field",owner_id="f1",name="maximum",value=14,source_ids=["s1"])]
    checks=validate(e,read_excel(p))
    assert any(c.code=="validation_rule" and c.result=="passed" for c in checks)
    assert not any(c.code=="protected_target" for c in checks)
    e.attributes[0].value=15
    assert any(c.code=="validation_rule" and c.result=="failed" for c in validate(e,read_excel(p)))
    w=load_workbook(p); w["Fragen"].protection.sheet=True; w.save(p); w.close()
    assert any(c.code=="protected_target" for c in validate(e,read_excel(p)))


def test_invalid_json_is_not_repaired(config):
    b=response_body(); b["output"][0]["content"][0]["text"]='{"questions": ['
    s=mocked_service(config,lambda req:httpx2.Response(200,json=b))
    with pytest.raises(ExtractionError) as e: s.call("luna_extraction","excel",[])
    assert e.value.code=="schema_error" and len(s.calls)==1
    s.close()


@pytest.mark.parametrize("details,expected",[({"cached_tokens":2048},True),({"cached_tokens":0,"cache_write_tokens":2048},False),({},None)])
def test_usage_from_actual_http(config,details,expected):
    b=response_body(); b["usage"]["input_tokens_details"]=details
    s=mocked_service(config,lambda req:httpx2.Response(200,json=b))
    s.call("luna_extraction","excel",[])
    assert s.calls[0]["cache_hit"] is expected
    assert s.calls[0]["cache_write_tokens"]==details.get("cache_write_tokens")
    s.close()


def test_input_limit_prevents_request(config):
    config.max_input_bytes=1
    with pytest.raises(ExtractionError) as e: build_request(config,"luna_extraction","excel",[])
    assert e.value.code=="input_too_large"


def test_stage_and_variant_have_separate_cache_groups(config):
    keys=[build_request(config,stage,variant,[])[1]["cache_key"] for stage,variant in [("luna_extraction","excel"),("terra_extraction","pdf"),("terra_extraction","word_pdf"),("terra_review","pdf"),("sol_reextraction","pdf")]]
    assert len(set(keys))==len(keys)


def test_key_collision_reported_without_deleting_question(sample):
    e=extraction(); e.questions.append(e.questions[0].model_copy(update={"id":"qduplicate"})); checks=[]
    result=enrich(e,read_excel(sample[1]),{"family":"same.xlsx"},{},checks)
    assert len(result.questions)==2
    assert any(c.code=="key_collision" for c in checks)


def test_missing_item_requires_concrete_source():
    with pytest.raises(ValidationError):
        Completeness(review_status="missing_found",missing_items=[MissingItem(quote="text",source_reference=address(None,sheet=None),context="product",reason="missing")],limitations=[])


def test_native_pdf_fields_and_wrong_page(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,TextStringObject,ArrayObject,NumberObject
    w=PdfWriter(); page=w.add_blank_page(width=576,height=792); w.add_blank_page(width=576,height=792)
    field=DictionaryObject({NameObject("/Type"):NameObject("/Annot"),NameObject("/Subtype"):NameObject("/Widget"),NameObject("/FT"):NameObject("/Ch"),NameObject("/T"):TextStringObject("answer"),NameObject("/Rect"):ArrayObject([NumberObject(x) for x in [10,10,100,30]]),NameObject("/Opt"):ArrayObject([TextStringObject("Yes"),TextStringObject("No")])})
    ref=w._add_object(field); page[NameObject("/Annots")]=ArrayObject([ref]); w._root_object[NameObject("/AcroForm")]=DictionaryObject({NameObject("/Fields"):ArrayObject([ref])})
    p=tmp_path/"form.pdf"; w.write(p); m=read_pdf(p)
    assert m["form_fields"]["answer"]["options"]==["Yes","No"]
    assert m["form_fields"]["answer"]["pages"]==[1]
    e=extraction(); e.source_references=[]; e.questions[0].source_ids=[]; e.questions[0].context_source_ids=[]
    f=e.answer_fields[0]; f.source_ids=[]; f.address=address(None,sheet=None,page=2,form_field="answer"); f.control_type="pdf_choice"
    checks=validate(e,m,{"pages":[{"pageNumber":1},{"pageNumber":2}]})
    assert any(c.code=="pdf_form_page" and c.result=="failed" for c in checks)
    assert any(c.code=="pdf_options" and c.result=="failed" for c in checks)
