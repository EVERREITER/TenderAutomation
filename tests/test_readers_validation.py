"""Test source readers and technical validation with synthetic documents.

Pytest discovers test_* functions covering workbook structure, static and dynamic
lists, multilingual sources, separate product contexts, multiple answer fields,
invalid claims, shared targets, Word ambiguity, and incomplete OCR coverage.
Tests call read_excel(), word_index(), validate(), and enrich() directly and do
not invoke cloud services.
"""
import copy
from zipfile import ZipFile
import pytest
from tender_extraction.readers.excel import read_excel
from tender_extraction.readers.documents import word_index
from tender_extraction.validation import validate,enrich
from tender_extraction.schemas import Option
from conftest import extraction,address


def test_structure_and_lists(sample):
    root,path=sample; m=read_excel(path); s=m["sheets"][0]
    assert len(m["sheets"])==2 and m["sheets"][1]["state"]=="hidden"
    assert [r["options"] for r in s["validations"]]==[["Yes","No"],["Exact YES","no"],None]
    assert s["cells"]["A4"]["formula"]["text"]=="1+1" and s["cells"]["A4"]["cached_value"] is None
    assert s["merges"]==[{"range":"A3:B3","anchor":"A3"}]
    assert s["row_ranges"] and s["columns"] and s["blank_ranges"]


def test_multiple_fields_products_and_parallel_language(sample):
    m=read_excel(sample[1]); e=extraction()
    e.source_references.append(e.source_references[0].model_copy(update={"id":"de","quote":"pH angeben","address":address("A2")}))
    e.questions[0].source_ids.append("de")
    e.answer_fields.append(e.answer_fields[0].model_copy(update={"id":"comment","role":"comment","address":address("B2")}))
    e.source_references.append(e.source_references[1].model_copy(update={"id":"ctxB","quote":"Product B","address":address("D1")}))
    e.questions.append(e.questions[0].model_copy(update={"id":"q2","context_source_ids":["ctxB"]}))
    e.answer_fields.append(e.answer_fields[0].model_copy(update={"id":"f2","question_id":"q2","address":address("B5")}))
    c=validate(e,m); r=enrich(e,m,{"family":"test.xlsx"},{},c)
    assert len(r.questions)==2 and len(r.questions[0].answer_fields)==2
    assert r.questions[0].key!=r.questions[1].key
    e.questions.reverse()
    r2=enrich(e,m,{"family":"test.xlsx"},{},[])
    assert {q.id:q.key for q in r.questions}=={q.id:q.key for q in r2.questions}


@pytest.mark.parametrize("mutation,code,result",[("missing","missing_target","not_verifiable"),("unknown","unknown_context","not_verifiable"),("invalid","excel_address","failed"),("merge","merge_anchor","failed"),("fake_dropdown","dropdown_type","failed"),("dynamic","dropdown_options","not_verifiable"),("reference","reference_integrity","failed"),("formula","formula_target","failed")])
def test_validation_cases(sample,mutation,code,result):
    m=read_excel(sample[1]); e=extraction()
    if mutation=="missing": e.answer_fields=[]
    if mutation=="unknown": e.questions[0].context_status="unknown"
    if mutation=="invalid": e.source_references[0].address.sheet="invented"
    if mutation=="merge": e.answer_fields[0].address.cell_range="B3"
    if mutation=="fake_dropdown": e.answer_fields[0].control_type="excel_dropdown"
    if mutation=="dynamic": e.answer_fields[0].address.cell_range="B6"
    if mutation=="reference": e.questions[0].source_ids=["missing"]
    if mutation=="formula": e.answer_fields[0].address.cell_range="A4"
    checks=validate(e,m)
    assert any(c.code==code and c.result==result for c in checks)


def test_exact_options_and_shared_target(sample):
    e=extraction(); e.answer_fields[0].address=address("B2"); e.answer_fields[0].control_type="excel_dropdown"
    e.options=[Option(id=str(i),field_id="f1",label=v,value=v,meaning=None,order=i,source_ids=["s1"]) for i,v in enumerate(["Yes","No"])]
    m=read_excel(sample[1]); assert any(c.code=="dropdown_options" and c.result=="passed" for c in validate(e,m))
    e.options.reverse(); e.options[0].order=0; e.options[1].order=1
    assert any(c.code=="dropdown_options" and c.result=="failed" for c in validate(e,m))
    e.answer_fields.append(e.answer_fields[0].model_copy(update={"id":"f2"}))
    assert any(c.code=="shared_physical_target" for c in validate(e,m))


def test_word_structure_duplicate_and_ocr_coverage(tmp_path):
    p=tmp_path/"word.docx"
    with ZipFile(p,"w") as z:
        z.writestr("word/document.xml",'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Same</w:t></w:r></w:p><w:p><w:r><w:t>Same</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl></w:body></w:document>')
    index=word_index(p)
    e=extraction(); e.source_references=[]; e.questions[0].source_ids=[]; e.questions[0].context_source_ids=[]
    e.answer_fields[0].source_ids=[]; e.answer_fields[0].address=address(None,sheet=None,word_path=index[0]["path"])
    m={"kind":"pdf","word_index":index,"pages":[{"number":1},{"number":2}],"form_fields":{},"limitations":[]}
    checks=validate(e,m,{"pages":[{"pageNumber":1}]})
    assert any(c.code=="word_pdf_mapping" and c.result=="not_verifiable" for c in checks)
    assert any(c.code=="ocr_page_coverage" and c.result=="failed" for c in checks)
