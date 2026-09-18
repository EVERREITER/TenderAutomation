"""Extract a complete Excel workbook with one Sol request and validate its sources."""
import hashlib
import json
import shutil
from time import perf_counter
from tempfile import TemporaryDirectory
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from . import VERSION, SCHEMA_VERSION, PROMPT_VERSION
from .errors import ExtractionError
from .readers.excel import read_excel, model_manifest
from .services.openai_client import OpenAIService
from .validation import validate, enrich, validation_status


def progress(message):
    print(f"[tender-extraction] {message}", flush=True)


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
    if resolved.suffix.lower() != ".xlsx":
        raise ExtractionError("unsupported_format", "Unterstützt: .xlsx. Bitte eine Excel-Arbeitsmappe auswählen.")
    return resolved, resolved.relative_to(samples).as_posix()


def extract_excel(manifest, document, service, run, save):
    compact = model_manifest(manifest)
    run["final_extractor"] = "sol_extraction"
    run["final_candidate_completeness"] = "Keine unabhängige semantische Vollständigkeitsprüfung durchgeführt"
    payload = {"document": document, "source_manifest": compact}
    source_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    run["excel_source"] = {"sheets": len(compact["sheets"]),
                           "content_cells": sum(len(s["cells"]) for s in compact["sheets"]),
                           "payload_bytes": len(source_text.encode("utf-8"))}
    progress(f"Excel-Inhalt für einen Sol-Aufruf: {run['excel_source']['sheets']} Blätter, "
             f"{run['excel_source']['content_cells']} Inhaltszellen, {run['excel_source']['payload_bytes']:,} Bytes")
    run["stages"].append("sol_extraction")
    candidate = service.call("sol_extraction", "excel", [{"type": "input_text", "text": source_text}])
    save("sol_questions.json", candidate.model_dump())
    return candidate


def run_file(input_value, output_dir, config, project_root=None, service=None):
    progress(f"Starte Verarbeitung: {input_value}")
    root = Path(project_root or Path.cwd()).resolve()
    path,family = selected_input(input_value,root)
    progress(f"Eingabe validiert: {path.name} ({path.suffix.lower()})")
    output = Path(output_dir)
    output = (root/output).resolve() if not output.is_absolute() else output.resolve()
    if output.is_relative_to(root/"sample_inputs"):
        raise ExtractionError("output_path", "Ausgaben dürfen nicht in sample_inputs/ liegen")
    run_id = uuid4().hex
    folder = output/run_id
    folder.mkdir(parents=True,exist_ok=False)
    progress(f"Aktiver Ausgabeordner: {folder}")
    run = {"run_id":run_id,"started_at":now(),"ended_at":None,"status":"failed","pipeline_version":VERSION,"schema_version":SCHEMA_VERSION,
        "prompt_version":PROMPT_VERSION,"configuration":config.public(),"calls":[],"stages":[],"final_extractor":None,
        "question_count":0,"final_candidate_completeness":"not_assessed","input_hash":digest(path)}
    document = {"filename":path.name,"format":path.suffix.lower()[1:],"family":family,"sha256":run["input_hash"]}
    def save(name,value):
        text = json.dumps(value,ensure_ascii=False,indent=2,default=str,allow_nan=False)
        for secret in (config.api_key,):
            if secret: text = text.replace(secret,"[REDACTED]")
        try:
            (folder/name).write_text(text+"\n",encoding="utf-8")
        except FileNotFoundError:
            # The output folder may have been removed while waiting for Sol.
            # Retry the local write only; never repeat the model request.
            folder.mkdir(parents=True, exist_ok=True)
            progress(f"Ausgabeordner fehlte und wurde wiederhergestellt: {folder}")
            (folder/name).write_text(text+"\n",encoding="utf-8")
    checks, result, owned = [], None, service is None
    workspace = TemporaryDirectory(prefix="tender-extraction-")
    try:
        progress("Konfiguration wird geprüft")
        config.validate(path.suffix.lower())
        progress("Konfiguration ist gültig")
        if path.stat().st_size > config.max_source_bytes:
            raise ExtractionError("input_too_large", "Datei überschreitet MAX_SOURCE_BYTES; keine Teilverarbeitung")
        # Analyze a stable byte snapshot, not a file that could change between calls.
        snapshot = Path(workspace.name)/("source"+path.suffix.lower())
        progress("Erstelle unveränderliche Quelldatei (Snapshot)")
        shutil.copyfile(path,snapshot)
        if digest(snapshot) != run["input_hash"]:
            raise ExtractionError("source_changed", "Originaldatei wurde während der Aufnahme verändert")
        progress("Lese Excel-Datei und erstelle Quellenmanifest")
        read_started = perf_counter()
        manifest = read_excel(snapshot)
        run["excel_read_seconds"] = round(perf_counter() - read_started, 3)
        progress(f"Excel-Datei gelesen in {run['excel_read_seconds']:.2f}s")
        service = service or OpenAIService(config)
        candidate = extract_excel(manifest, document, service, run, save)
        run["stages"].append("python_validation")
        progress("Starte lokale Quellenprüfung und Ergebnisanreicherung")
        checks = validate(candidate,manifest)
        if not candidate.questions and candidate.limitations:
            raise ExtractionError("empty_extraction",
                "Sol hat keine Fragen extrahiert und Einschränkungen gemeldet. "
                "Die Modellbegründung steht in sol_questions.json unter limitations; "
                "dies ist kein verwertbarer Fragenkatalog.")
        result = enrich(candidate,manifest,document,run,checks)
        progress("Lokale Prüfung abgeschlossen")
        run["question_count"] = len(candidate.questions)
        run["status"] = validation_status(checks)
        run["validation_policy"] = "material_errors_only_v1"
        if digest(path) != run["input_hash"]:
            raise ExtractionError("source_changed", "Originaldatei wurde während des Laufs extern verändert; Ergebnis verworfen")
    except Exception as e:
        progress(f"Verarbeitung fehlgeschlagen: {type(e).__name__}")
        run["status"] = "failed"
        run["error"] = {"code":e.code if isinstance(e,ExtractionError) else "local_processing_error", "message":str(e) if isinstance(e,ExtractionError) else type(e).__name__+": lokale Verarbeitung fehlgeschlagen"}
        result = None  # A previous candidate is never promoted on failure.
    finally:
        run["ended_at"] = now()
        if service:
            run["calls"] = service.calls
            if owned: service.close()
        run["validation_checks"] = [c.model_dump() for c in checks]
        try:
            if result:
                result.run = run
                save("result.json",result.model_dump())
            else:
                save("result.json",{"schema_version":SCHEMA_VERSION,"document":document,"run":run,"questions":[],"candidate_only_artifacts":True})
        finally:
            workspace.cleanup()
        progress(f"Lauf beendet: {run['status']}; Ergebnisse: {folder}")
    return folder,run
