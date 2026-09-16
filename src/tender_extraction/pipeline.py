"""Orchestrate extraction, completeness review, and source validation.

run_file() is the main entry point. It validates a selected input, snapshots the
source, prepares Excel or PDF/Word input, runs the prescribed Azure model chain,
and writes isolated run artifacts. Only a missing-question review triggers a
complete Sol replacement. selected_input() enforces the sample_inputs boundary;
digest() calculates file fingerprints. Failed runs never promote old candidates.
"""
import base64
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from . import VERSION, SCHEMA_VERSION, PROMPT_VERSION
from .errors import ExtractionError
from .readers.excel import read_excel
from .readers.documents import read_pdf, word_index, convert_word
from .services.openai_client import OpenAIService
from .services.document_intelligence import analyze
from .validation import validate, enrich


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()


def selected_input(value, project_root):
    root = Path(project_root).resolve()
    samples = root/"sample_inputs"
    # A symlink/junction used as the sample root must not redefine the boundary.
    if samples.resolve() != samples or not samples.is_dir():
        raise ExtractionError("input_path", "sample_inputs/ fehlt oder verweist auf einen anderen Pfad")
    p = Path(value)
    if ".." in p.parts:
        raise ExtractionError("input_path", "Pfadtraversal ist nicht erlaubt")
    p = p if p.is_absolute() else root/p
    resolved = p.resolve()
    if not resolved.is_relative_to(samples) or not resolved.is_file():
        raise ExtractionError("input_path", "Genau eine vorhandene Datei innerhalb sample_inputs/ auswählen")
    if resolved.suffix.lower() not in (".xlsx",".docx",".pdf"):
        raise ExtractionError("unsupported_format", "Unterstützt: .xlsx, .docx, .pdf. .xls/.doc benötigen eine gesonderte Konvertierungserweiterung")
    return resolved, resolved.relative_to(samples).as_posix()


def run_file(input_value, output_dir, config, project_root=None, service=None, di_analyze=analyze, converter=convert_word):
    root = Path(project_root or Path.cwd()).resolve()
    path,family = selected_input(input_value,root)
    output = Path(output_dir)
    output = (root/output).resolve() if not output.is_absolute() else output.resolve()
    if output.is_relative_to(root/"sample_inputs"):
        raise ExtractionError("output_path", "Ausgaben dürfen nicht in sample_inputs/ liegen")
    run_id = uuid4().hex
    folder = output/run_id
    folder.mkdir(parents=True,exist_ok=False)
    run = {"run_id":run_id,"started_at":now(),"ended_at":None,"status":"failed","pipeline_version":VERSION,"schema_version":SCHEMA_VERSION,
        "prompt_version":PROMPT_VERSION,"configuration":config.public(),"calls":[],"stages":[],"escalation_reason":None,"final_extractor":None,
        "question_count":0,"prior_completeness_review":None,"final_candidate_completeness":"not_assessed","input_hash":digest(path)}
    document = {"filename":path.name,"format":path.suffix.lower()[1:],"family":family,"sha256":run["input_hash"],"rendered_pdf":None}
    def save(name,value):
        text = json.dumps(value,ensure_ascii=False,indent=2,default=str,allow_nan=False)
        for secret in (config.api_key,config.di_key):
            if secret: text = text.replace(secret,"[REDACTED]")
        (folder/name).write_text(text+"\n",encoding="utf-8")
    checks, result, owned = [], None, service is None
    try:
        config.validate(path.suffix.lower())
        if path.stat().st_size > config.max_source_bytes:
            raise ExtractionError("input_too_large", "Datei überschreitet MAX_SOURCE_BYTES; keine Teilverarbeitung")
        # Analyze a stable byte snapshot, not a file that could change between calls.
        snapshot = folder/("source"+path.suffix.lower())
        shutil.copyfile(path,snapshot)
        if digest(snapshot) != run["input_hash"]:
            raise ExtractionError("source_changed", "Originaldatei wurde während der Aufnahme verändert")
        if path.suffix.lower() == ".xlsx":
            manifest = read_excel(snapshot)
            variant = "excel"
            content = [{"type":"input_text","text":json.dumps({"document":document,"source_manifest":manifest},ensure_ascii=False)}]
            initial = "luna_extraction"
            pdf = None
        else:
            pdf = converter(snapshot,folder,config.converter,config.timeout) if path.suffix.lower()==".docx" else snapshot
            manifest = read_pdf(pdf)
            variant = "word_pdf" if path.suffix.lower()==".docx" else "pdf"
            if variant == "word_pdf":
                manifest["word_index"] = word_index(snapshot)
                document["rendered_pdf"] = {"filename":pdf.name,"sha256":digest(pdf),"source_sha256":document["sha256"]}
            if pdf.stat().st_size > config.max_input_bytes:
                raise ExtractionError("input_too_large", "Erzeugte PDF überschreitet MAX_INPUT_BYTES")
            content = [{"type":"input_text","text":json.dumps({"document":document,"word_index":manifest.get("word_index"),"pdf_coordinates":manifest["coordinate_system"]},ensure_ascii=False)},
                {"type":"input_file","filename":pdf.name,"file_data":"data:application/pdf;base64,"+base64.b64encode(pdf.read_bytes()).decode("ascii")}]
            initial = "terra_extraction"
        save("source_manifest.json",manifest)
        service = service or OpenAIService(config)
        run["stages"].append(initial)
        candidate = service.call(initial,variant,content)
        save("initial_candidate.json",candidate.model_dump())
        run["stages"].append("terra_review")
        review = service.call("terra_review",variant,content,candidate)
        save("completeness_review.json",review.model_dump())
        run["prior_completeness_review"] = review.model_dump()
        run["final_extractor"] = initial
        run["final_candidate_completeness"] = review.review_status
        if review.review_status == "missing_found":
            run["escalation_reason"] = "missing_found"
            run["stages"].append("sol_reextraction")
            run["final_extractor"] = "sol_reextraction"
            run["final_candidate_completeness"] = "Keine erneute semantische Vollständigkeitsprüfung durchgeführt"
            candidate = service.call("sol_reextraction",variant,content)
            save("sol_candidate.json",candidate.model_dump())
        di = None
        if pdf:
            run["stages"].append("document_intelligence")
            di,di_record = di_analyze(pdf,config)
            run["document_intelligence"] = di_record
            save("document_intelligence.json",di)
        run["stages"].append("python_validation")
        checks = validate(candidate,manifest,di)
        result = enrich(candidate,manifest,document,run,checks)
        run["question_count"] = len(candidate.questions)
        run["status"] = "needs_review" if review.review_status == "unable_to_assess" or review.limitations or result.limitations or any(c.result in ("failed","not_verifiable") for c in checks) else "completed"
        if digest(path) != run["input_hash"]:
            raise ExtractionError("source_changed", "Originaldatei wurde während des Laufs extern verändert; Snapshot bleibt nachvollziehbar")
    except Exception as e:
        run["status"] = "failed"
        run["error"] = {"code":e.code if isinstance(e,ExtractionError) else "local_processing_error", "message":str(e) if isinstance(e,ExtractionError) else type(e).__name__+": lokale Verarbeitung fehlgeschlagen"}
        result = None  # A previous candidate is never promoted on failure.
    finally:
        run["ended_at"] = now()
        if service:
            run["calls"] = service.calls
            for stage,raw in service.diagnostics.items(): save(stage+"_response_diagnostic.json",raw)
            if owned: service.close()
        save("run_manifest.json",run)
        save("validation_report.json",{"checks":[c.model_dump() for c in checks],"scope":"Technische Quellenprüfung; keine semantische Freigabe oder Vollständigkeitsgarantie"})
        if result:
            result.run = run
            save("result.json",result.model_dump())
        else:
            save("result.json",{"schema_version":SCHEMA_VERSION,"document":document,"run":run,"questions":[],"candidate_only_artifacts":True})
    return folder,run
