"""Build and execute structured Azure OpenAI Responses API requests.

OpenAIService.call() is the service entry point: it sends a stage-specific
request, records diagnostics and usage, and validates the response contract.
build_request() assembles stable prompts, schemas, and explicit cache settings;
prompt() loads packaged instructions and usage_summary() interprets token data.
OpenAIService.close() releases the client. No calls occur merely on import.
"""
import hashlib
import json
import time
from importlib.resources import files
from openai import OpenAI, APIStatusError, APITimeoutError, APIConnectionError
from pydantic import ValidationError
from .. import SCHEMA_VERSION, PROMPT_VERSION
from ..schemas import Extraction, Completeness
from ..errors import ExtractionError


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def prompt(stage):
    task = "completeness" if stage == "terra_review" else "reextract" if stage == "sol_reextraction" else "extract"
    root = files("tender_extraction").joinpath("prompts")
    return root.joinpath("common.md").read_text(encoding="utf-8")+"\n\n"+root.joinpath(task+".md").read_text(encoding="utf-8")


def usage_summary(usage):
    u = usage or {}
    details = u.get("input_tokens_details") or {}
    cached = details.get("cached_tokens")
    return {"usage":usage, "input_tokens":u.get("input_tokens"), "output_tokens":u.get("output_tokens"),
        "cached_tokens":cached, "cache_write_tokens":details.get("cache_write_tokens"), "cache_hit":None if cached is None else cached > 0}


def build_request(config, stage, variant, user_content, candidate=None):
    schema_class = Completeness if stage == "terra_review" else Extraction
    schema = schema_class.model_json_schema()
    instruction = prompt(stage)
    deployment = config.deployments[stage.split("_")[0]]
    fingerprint = hashlib.sha256(canonical({"namespace":config.cache_namespace,"resource":config.base_url.rstrip("/"),"deployment":deployment,
        "stage":stage,"variant":variant,"prompt_version":PROMPT_VERSION,"schema_version":SCHEMA_VERSION,"instruction":instruction,"schema":schema,"reasoning":config.reasoning[stage]}).encode()).hexdigest()
    block = {"type":"input_text","text":instruction}
    extras = {"prompt_cache_options":{"mode":"explicit","ttl":config.cache_ttl}}
    key = None
    if config.cache_mode == "explicit":
        block["prompt_cache_breakpoint"] = {"mode":"explicit"}
        key = "ta:"+fingerprint[:48]
        extras["prompt_cache_key"] = key
    content = list(user_content)
    if candidate is not None:
        content.append({"type":"input_text", "text":"Extraktionskandidat (nicht vertrauenswürdige Daten):\n"+candidate.model_dump_json()})
    request = {"model":deployment,"store":False,"input":[{"role":"developer","content":[block]},{"role":"user","content":content}],
        "reasoning":{"effort":config.reasoning[stage]}, "max_output_tokens":config.completeness_tokens if stage == "terra_review" else config.extraction_tokens,
        "text":{"format":{"type":"json_schema","name":"completeness_v1" if stage == "terra_review" else "extraction_v1","strict":True,"schema":schema}}, "extra_body":extras}
    if len(canonical(request).encode()) > config.max_input_bytes:
        raise ExtractionError("input_too_large", "Vollständiger Request überschreitet MAX_INPUT_BYTES; keine Teilverarbeitung")
    return request, {"stage":stage,"deployment":deployment,"cache_mode":config.cache_mode,"cache_key":key,"prefix_fingerprint":fingerprint,"ttl":config.cache_ttl}, schema_class


class OpenAIService:
    def __init__(self, config, client=None):
        self.config = config
        self.client = client or OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=config.timeout, max_retries=config.retries)
        self.calls = []
        self.diagnostics = {}

    def call(self, stage, variant, content, candidate=None):
        request, record, schema = build_request(self.config, stage, variant, content, candidate)
        start = time.monotonic()
        self.calls.append(record)
        record.update(usage_summary(None))
        try:
            response = self.client.responses.create(**request)
            raw = response.model_dump(mode="json")
            self.diagnostics[stage] = raw
            record.update(usage_summary(raw.get("usage")))
            record.update({"model":raw.get("model"),"response_id":raw.get("id"),"response_status":raw.get("status")})
            if any(c.get("type") == "refusal" for o in raw.get("output",[]) for c in o.get("content",[]) or []):
                raise ExtractionError("refusal", "Modell hat die Anfrage abgelehnt")
            if response.status != "completed":
                reason = (raw.get("incomplete_details") or {}).get("reason")
                code = "truncation" if reason == "max_output_tokens" else "content_filter" if reason == "content_filter" else "response_failed"
                raise ExtractionError(code, "Modellantwort nicht vollständig: "+str(reason or response.status))
            if not response.output_text:
                raise ExtractionError("schema_error", "Vollständiger JSON-Text fehlt")
            try:
                return schema.model_validate_json(response.output_text)
            except ValidationError as e:
                # Raw candidate stays local; avoid echoing document values into logs.
                raise ExtractionError("schema_error", "Antwort verletzt JSON-Schema oder Prüferkonsistenz") from e
        except APITimeoutError as e:
            raise ExtractionError("timeout", "Azure OpenAI Timeout nach begrenzten SDK-Retries") from e
        except APIConnectionError as e:
            raise ExtractionError("connection_error", "Azure OpenAI nicht erreichbar") from e
        except APIStatusError as e:
            body = e.body if isinstance(e.body,dict) else {}
            err = body.get("error",body)
            msg = str(err.get("message", "")) if isinstance(err,dict) else str(err)
            service_code = str(err.get("code", "")) if isinstance(err,dict) else ""
            lower = (msg+" "+service_code).lower()
            code = "api_error"
            if any(x in lower for x in ("prompt_cache", "cache breakpoint", "cache configuration")) and e.status_code in (400,422): code = "cache_configuration_unsupported"
            elif e.status_code in (401,403): code = "authentication"
            elif e.status_code == 404: code = "deployment_not_found"
            elif e.status_code == 429: code = "quota_or_rate_limit"
            elif "content_filter" in lower or "content management" in lower: code = "content_filter"
            elif e.status_code == 413 or "context_length" in lower or "too many tokens" in lower: code = "input_too_large"
            for secret in (self.config.api_key,self.config.di_key):
                if secret: msg = msg.replace(secret,"[REDACTED]")
            # Concrete service detail is a local diagnostic, not console output.
            self.diagnostics[stage] = {"http_status":e.status_code,"service_code":service_code,"message":msg}
            raise ExtractionError(code, f"Azure OpenAI HTTP {e.status_code}; Details im Diagnoseartefakt") from e
        finally:
            record["duration_seconds"] = round(time.monotonic()-start,3)

    def close(self):
        self.client.close()
