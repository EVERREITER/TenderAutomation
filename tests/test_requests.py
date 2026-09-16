"""Test serialized OpenAI requests and response handling without network access.

Pytest discovers test_* functions covering explicit/off caching, stable keys,
usage reporting, response hygiene, API errors, and strict schema limits.
mocked_service() connects the real SDK to an HTTP mock; response_body() supplies
synthetic Responses API payloads used here and by other tests.
"""
import copy
import json
import httpx2
import pytest
from openai import OpenAI
from tender_extraction.services import openai_client as module
from tender_extraction.services.openai_client import OpenAIService, build_request, usage_summary
from tender_extraction.errors import ExtractionError
from tender_extraction.schemas import Extraction, Completeness
from conftest import extraction


def response_body(value=None,**changes):
    text=(value or extraction()).model_dump_json()
    return {"id":"resp_test","object":"response","created_at":1,"status":"completed","model":"model-returned", "output":[{"type":"message","id":"msg_test","role":"assistant","status":"completed","content":[{"type":"output_text","text":text,"annotations":[]}]}],"usage":{"input_tokens":500,"output_tokens":50,"total_tokens":550,"input_tokens_details":{"cached_tokens":0},"output_tokens_details":{"reasoning_tokens":10}},**changes}


def mocked_service(config,handler):
    client=OpenAI(api_key=config.api_key,base_url=config.base_url,max_retries=0,http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    return OpenAIService(config,client)


@pytest.mark.parametrize("mode",["explicit","off"])
def test_actual_http_serialization(config,mode):
    config.cache_mode=mode
    bodies=[]
    def handle(req):
        bodies.append(json.loads(req.content)); return httpx2.Response(200,json=response_body())
    service=mocked_service(config,handle)
    for name in ["one.xlsx","two.xlsx"]:
        service.call("luna_extraction","excel",[{"type":"input_text","text":json.dumps({"filename":name,"run_id":name,"hash":name,"full_workbook":["sheet1","sheet2"]})}])
    a,b=bodies
    assert a["input"][0]==b["input"][0] and a["text"]==b["text"]
    assert a["input"][1]!=b["input"][1]
    assert a.get("prompt_cache_key")==b.get("prompt_cache_key")
    assert a["prompt_cache_options"]=={"mode":"explicit","ttl":"30m"}
    assert "instructions" not in a and "previous_response_id" not in a and a["store"] is False
    assert "prompt_cache_retention" not in a and "temperature" not in a and "tools" not in a
    count=json.dumps(a).count('"prompt_cache_breakpoint"')
    assert count==(1 if mode=="explicit" else 0)
    if mode=="explicit": assert a["prompt_cache_key"].startswith("ta:") and len(a["prompt_cache_key"])==51
    else: assert "prompt_cache_key" not in a
    assert service.calls[0]["cache_hit"] is False
    service.close()


def test_key_changes_with_actual_prompt_schema_reasoning(config,monkeypatch):
    def fingerprint(): return build_request(config,"luna_extraction","excel",[])[1]["cache_key"]
    original=fingerprint()
    real=module.prompt
    monkeypatch.setattr(module,"prompt",lambda stage:real(stage)+" changed")
    assert fingerprint()!=original
    monkeypatch.setattr(module,"prompt",real)
    real_schema=Extraction.model_json_schema
    monkeypatch.setattr(Extraction,"model_json_schema",lambda:dict(real_schema(),description="changed"))
    assert fingerprint()!=original
    monkeypatch.undo()
    config.reasoning["luna_extraction"]="high"
    assert fingerprint()!=original


@pytest.mark.parametrize("usage,hit,cached,writes",[(None,None,None,None),({},None,None,None),({"input_tokens_details":{"cached_tokens":0}},False,0,None),({"input_tokens_details":{"cached_tokens":1024}},True,1024,None),({"input_tokens_details":{"cache_write_tokens":1024}},None,None,1024)])
def test_usage(usage,hit,cached,writes):
    d=usage_summary(usage)
    assert (d["cache_hit"],d["cached_tokens"],d["cache_write_tokens"])==(hit,cached,writes)


@pytest.mark.parametrize("changes,code",[({"status":"incomplete","incomplete_details":{"reason":"max_output_tokens"}},"truncation"),({"output":[{"type":"message","id":"msg","role":"assistant","status":"completed","content":[{"type":"refusal","refusal":"No"}]}]},"refusal"),({"output":[]},"schema_error")])
def test_response_hygiene(config,changes,code):
    s=mocked_service(config,lambda req:httpx2.Response(200,json=response_body(**changes)))
    with pytest.raises(ExtractionError,match=".") as e: s.call("luna_extraction","excel",[])
    assert e.value.code==code
    assert len(s.calls)==1
    s.close()


@pytest.mark.parametrize("http_status,message,code",[(400,"Unsupported prompt_cache_options","cache_configuration_unsupported"),(401,"bad key","authentication"),(404,"unknown deployment","deployment_not_found"),(429,"quota exceeded","quota_or_rate_limit"),(400,"context_length_exceeded","input_too_large")])
def test_api_failures_no_fallback(config,http_status,message,code):
    seen=[]
    def handle(req): seen.append(req); return httpx2.Response(http_status,json={"error":{"message":message,"code":"test_error"}})
    s=mocked_service(config,handle)
    with pytest.raises(ExtractionError) as e: s.call("luna_extraction","excel",[])
    assert e.value.code==code and len(seen)==1
    s.close()


def test_strict_wire_schema_limits():
    for cls in (Extraction,Completeness):
        schema=cls.model_json_schema(); count=0
        def walk(v):
            nonlocal count
            if isinstance(v,dict):
                if v.get("type")=="object":
                    count+=len(v["properties"])
                    assert v["additionalProperties"] is False
                    assert set(v["required"])==set(v["properties"])
                for val in v.values(): walk(val)
            elif isinstance(v,list):
                for val in v: walk(val)
        walk(schema)
        assert count<=100
        def depth(v,level=0):
            if not isinstance(v,dict): return level
            if "$ref" in v: return depth(schema["$defs"][v["$ref"].split("/")[-1]],level)
            if v.get("type")=="object": return max([level+1]+[depth(x,level+1) for x in v["properties"].values()])
            if "items" in v: return depth(v["items"],level)
            return max([level]+[depth(x,level) for x in v.get("anyOf",[])])
        assert depth(schema)<=5
