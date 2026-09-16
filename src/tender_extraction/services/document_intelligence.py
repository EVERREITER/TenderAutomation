"""Obtain OCR and layout evidence for the exact PDF used during extraction.

analyze(path, config) is the public entry point. It invokes Azure Document
Intelligence prebuilt-layout, waits within the configured timeout, and returns
the analysis dictionary plus service-run metadata. Service and transport errors
are translated into categorized ExtractionError exceptions. Calling analyze()
uses a billable Azure service; importing this module does not make requests.
"""
import time
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError, ServiceRequestError
from ..errors import ExtractionError


def analyze(path, config):
    start = time.monotonic()
    try:
        with DocumentIntelligenceClient(config.di_endpoint, AzureKeyCredential(config.di_key), api_version=config.di_version,
                retry_total=config.retries, connection_timeout=config.timeout, read_timeout=config.timeout) as client:
            with path.open("rb") as data:
                poller = client.begin_analyze_document("prebuilt-layout", body=data, content_type="application/pdf")
                result = poller.result(timeout=config.timeout)
                if not poller.done() or result is None:
                    raise ExtractionError("di_timeout", "Document Intelligence nicht innerhalb des Timeouts abgeschlossen")
        return result.as_dict(), {"service":"document_intelligence","model":"prebuilt-layout","api_version":config.di_version,"duration_seconds":round(time.monotonic()-start,3)}
    except HttpResponseError as e:
        code = "di_authentication" if e.status_code in (401,403) else "di_quota" if e.status_code == 429 else "di_api_error"
        raise ExtractionError(code, f"Document Intelligence HTTP {e.status_code}") from e
    except (ServiceRequestError, TimeoutError) as e:
        raise ExtractionError("di_transport_error", "Document Intelligence Transportfehler/Timeout") from e
