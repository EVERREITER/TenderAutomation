"""Test Excel input boundaries, configuration failures, and isolated outputs."""
import json
from pathlib import Path
import pytest
from tender_extraction.cli import main
from tender_extraction.pipeline import run_file,selected_input
from tender_extraction.errors import ExtractionError


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


@pytest.mark.parametrize("suffix", [".pdf", ".docx", ".xls"])
def test_only_xlsx_is_accepted(sample, suffix):
    root, original = sample
    path = original.with_suffix(suffix)
    path.write_bytes(b"unsupported input")
    with pytest.raises(ExtractionError) as error:
        selected_input(path, root)
    assert error.value.code == "unsupported_format"


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


@pytest.mark.parametrize("model_fails", [False, True])
def test_deleted_active_output_folder_is_recreated_without_model_retry(sample, config, model_fails):
    from conftest import extraction
    root, source = sample
    class Service:
        calls = []
        def call(self, *args):
            self.calls.append({"stage":"sol_extraction"})
            folders = list((root / "outputs").iterdir())
            assert len(folders) == 1
            # The active directory is still empty during the model request.
            folders[0].rmdir()
            (root / "outputs").rmdir()
            if model_fails:
                raise ExtractionError("truncation", "Original model failure")
            return extraction()
    service = Service()
    folder, run = run_file(source, "outputs", config, root, service)
    result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    assert len(service.calls) == 1
    if model_fails:
        assert result["run"]["error"] == {"code":"truncation", "message":"Original model failure"}
        assert result["questions"] == []
    else:
        assert run["status"] != "failed"
        assert result["questions"]
        assert (folder / "sol_questions.json").is_file()


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
