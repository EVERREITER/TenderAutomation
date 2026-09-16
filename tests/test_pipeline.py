"""Test pipeline control flow, artifact handling, and local input boundaries.

Pytest discovers test_* functions that exercise Excel, PDF, and Word flows with
HTTP-mocked model responses and local conversion/layout substitutes. Coverage
includes Sol replacement and failure, review consistency, original-file
preservation, unique run outputs, required CLI arguments, and path restrictions.
empty() provides a valid empty extraction response for pipeline scenarios.
"""
import json
import shutil
import sys
from pathlib import Path
from zipfile import ZipFile
import httpx2
import pytest
from pypdf import PdfWriter
from pydantic import ValidationError
from tender_extraction.cli import main
from tender_extraction.pipeline import run_file,selected_input,digest
from tender_extraction.schemas import Completeness,MissingItem,Extraction
from tender_extraction.errors import ExtractionError
from conftest import extraction,address,no_missing
from test_requests import mocked_service,response_body


def empty(): return Extraction(questions=[],answer_fields=[],options=[],source_references=[],positions=[],attributes=[],limitations=[])


@pytest.mark.parametrize("suffix",[".xlsx",".pdf",".docx"])
@pytest.mark.parametrize("review_status",["no_missing_found","missing_found","unable_to_assess"])
def test_full_pipeline_order_replacement_and_source(sample,config,suffix,review_status):
    root,original=sample
    config.converter=sys.executable
    p=original if suffix==".xlsx" else original.with_suffix(suffix)
    def make_pdf(dest):
        w=PdfWriter(); w.add_blank_page(width=600,height=800); w.write(dest)
    if suffix==".pdf": make_pdf(p)
    if suffix==".docx":
        with ZipFile(p,"w") as z: z.writestr("word/document.xml",'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p/></w:body></w:document>')
    before=digest(p)
    review=Completeness(review_status=review_status,missing_items=[MissingItem(quote="Provide pH",source_reference=address(),context="Product A",reason="Missing")] if review_status=="missing_found" else [],limitations=["Cannot assess"] if review_status=="unable_to_assess" else [])
    initial=extraction() if suffix==".xlsx" else empty()
    responses=[initial,review]+([empty()] if review_status=="missing_found" else [])
    requests=[]; di_calls=[]
    def handle(req):
        requests.append(json.loads(req.content))
        return httpx2.Response(200,json=response_body(responses.pop(0)))
    service=mocked_service(config,handle)
    def converter(source,folder,*args):
        dest=folder/"rendered.pdf"; make_pdf(dest); return dest
    def analyze(pdf,cfg):
        di_calls.append(digest(pdf)); return {"pages":[{"pageNumber":1,"lines":[],"unit":"inch"}]},{"model":"prebuilt-layout"}
    folder,run=run_file(p,"outputs",config,root,service,analyze,converter)
    assert not responses and digest(p)==before
    assert len(requests)==(3 if review_status=="missing_found" else 2)
    assert [x["model"] for x in requests]==(["dep-l" if suffix==".xlsx" else "dep-t","dep-t"]+(["dep-s"] if review_status=="missing_found" else []))
    assert requests[0]["input"][1]["content"]==requests[1]["input"][1]["content"][:-1]
    result=json.loads((folder/"result.json").read_text(encoding="utf-8"))
    assert run["status"]!="failed", run.get("error")
    if review_status=="missing_found":
        assert requests[2]["input"][1]["content"]==requests[0]["input"][1]["content"]
        assert result["questions"]==[]
        assert "Keine erneute" in run["final_candidate_completeness"]
    if review_status=="unable_to_assess": assert run["status"]=="needs_review"
    assert len(di_calls)==(0 if suffix==".xlsx" else 1)
    if suffix!=".xlsx":
        assert requests[0]["input"][1]["content"][1]["type"]=="input_file"
        assert requests[0]["input"][1]["content"][1]["file_data"].startswith("data:application/pdf;base64,")
    service.close()


def test_sol_failure_does_not_promote_old_candidate(sample,config):
    root,p=sample
    review=Completeness(review_status="missing_found",missing_items=[MissingItem(quote="Provide pH",source_reference=address(),context="Product A",reason="Missing")],limitations=[])
    responses=[extraction(),review]; calls=[]
    def handle(req):
        calls.append(req)
        return httpx2.Response(200,json=response_body(responses.pop(0))) if responses else httpx2.Response(500,json={"error":{"message":"failed"}})
    s=mocked_service(config,handle)
    folder,run=run_file(p,"outputs",config,root,s)
    assert run["status"]=="failed" and len(calls)==3
    assert (folder/"initial_candidate.json").exists()
    assert json.loads((folder/"result.json").read_text())["questions"]==[]
    s.close()


def test_required_argument_and_path_boundary(sample,tmp_path):
    root,p=sample
    with pytest.raises(SystemExit) as e: main([])
    assert e.value.code==2
    assert selected_input(p,root)[0]==p
    for value in [root/"outside.xlsx","sample_inputs/../outside.xlsx","sample_inputs/*.xlsx"]:
        with pytest.raises(ExtractionError): selected_input(value,root)
    legacy=p.with_suffix(".xls"); legacy.write_bytes(b"legacy")
    with pytest.raises(ExtractionError) as e: selected_input(legacy,root)
    assert e.value.code=="unsupported_format"


def test_symlink_escape(sample,monkeypatch):
    root,p=sample; outside=root/"outside.xlsx"; outside.write_bytes(b"outside")
    link=p.parent/"link.xlsx"
    try: link.symlink_to(outside)
    except OSError:
        # Windows without CreateSymbolicLink privilege: exercise resolved-path boundary.
        actual=Path.resolve
        monkeypatch.setattr(Path,"resolve",lambda self,*a,**kw:outside if self==link else actual(self,*a,**kw))
    with pytest.raises(ExtractionError): selected_input(link,root)


def test_configuration_fails_before_call_and_output_is_unique(sample,config):
    root,p=sample; config.api_key=""
    class NoService:
        calls=[]; diagnostics={}
        def call(self,*a): raise AssertionError("Must not call Azure")
    folder,run=run_file(p,"outputs",config,root,NoService())
    previous=(folder/"result.json").read_bytes()
    second,_=run_file(p,"outputs",config,root,NoService())
    assert folder!=second and (folder/"result.json").read_bytes()==previous
    assert run["error"]["code"]=="configuration"


@pytest.mark.parametrize("data",[{"review_status":"missing_found","missing_items":[],"limitations":[]},{"review_status":"no_missing_found","missing_items":[],"limitations":["partial"]},{"review_status":"unable_to_assess","missing_items":[],"limitations":[]}])
def test_review_consistency(data):
    with pytest.raises(ValidationError): Completeness.model_validate(data)


def test_actual_function_files_unchanged_except_module_documentation():
    import hashlib
    root=Path(__file__).resolve().parents[1]
    baseline=root/"outputs"/"baseline.json"
    if not baseline.exists(): pytest.skip("Implementation-session baseline not present in this checkout")
    for name,expected in json.loads(baseline.read_text()).items():
        content = (root/name).read_bytes()
        if name == "function_app.py" and content.startswith(b'"""'):
            content = content.split(b'"""', 2)[2].lstrip(b"\r\n")
        assert hashlib.sha256(content).hexdigest()==expected
