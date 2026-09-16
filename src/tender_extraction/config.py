"""Load and validate configuration for the local extraction pipeline.

Config.from_env() reads the project .env and environment variables. The
Config.validate(suffix) method checks settings and format-specific prerequisites
before cloud calls; Config.public() returns configuration without API keys for
run manifests. The configuration includes deployments, reasoning budgets,
request limits, prompt caching, Document Intelligence, and LibreOffice.
"""
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv
from .errors import ExtractionError


@dataclass
class Config:
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    deployments: dict = field(default_factory=dict)
    reasoning: dict = field(default_factory=lambda: {"luna_extraction": "medium", "terra_extraction": "high", "terra_review": "high", "sol_reextraction": "high"})
    di_endpoint: str = ""
    di_key: str = field(default="", repr=False)
    di_version: str = "2024-11-30"
    timeout: float = 300
    retries: int = 2
    extraction_tokens: int = 32768
    completeness_tokens: int = 8192
    cache_mode: str = "explicit"
    cache_namespace: str = "tender-extraction-v1"
    cache_ttl: str = "30m"
    converter: str = "soffice"
    max_input_bytes: int = 20_000_000
    max_source_bytes: int = 100_000_000

    @classmethod
    def from_env(cls):
        load_dotenv(Path.cwd() / ".env", override=False)
        g = os.getenv
        try:
            return cls(base_url=g("AZURE_OPENAI_BASE_URL", ""), api_key=g("AZURE_OPENAI_API_KEY", ""),
                deployments={k:g("AZURE_OPENAI_DEPLOYMENT_"+k.upper(), "") for k in ("luna", "terra", "sol")},
                reasoning={k:g("REASONING_"+k.upper(), "medium" if k == "luna_extraction" else "high") for k in ("luna_extraction", "terra_extraction", "terra_review", "sol_reextraction")},
                di_endpoint=g("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", ""), di_key=g("AZURE_DOCUMENT_INTELLIGENCE_API_KEY", ""),
                di_version=g("AZURE_DOCUMENT_INTELLIGENCE_API_VERSION", "2024-11-30"),
                timeout=float(g("OPENAI_TIMEOUT_SECONDS", "300")), retries=int(g("OPENAI_MAX_RETRIES", "2")),
                extraction_tokens=int(g("EXTRACTION_MAX_OUTPUT_TOKENS", "32768")), completeness_tokens=int(g("COMPLETENESS_MAX_OUTPUT_TOKENS", "8192")),
                cache_mode=g("OPENAI_PROMPT_CACHE_MODE", "explicit"), cache_namespace=g("OPENAI_PROMPT_CACHE_NAMESPACE", "tender-extraction-v1"),
                cache_ttl=g("OPENAI_PROMPT_CACHE_TTL", "30m"), converter=g("LIBREOFFICE_PATH", "soffice"),
                max_input_bytes=int(g("MAX_INPUT_BYTES", "20000000")), max_source_bytes=int(g("MAX_SOURCE_BYTES", "100000000")))
        except ValueError as e:
            raise ExtractionError("configuration", "Ungültige numerische .env-Einstellung") from e

    def validate(self, suffix):
        u = urlsplit(self.base_url)
        if u.scheme != "https" or not u.netloc or u.username or u.password or u.query or u.fragment or u.path.rstrip("/") != "/openai/v1":
            raise ExtractionError("configuration", "AZURE_OPENAI_BASE_URL muss https://<resource>/openai/v1/ sein, kein Projektendpunkt")
        required = ["terra", "sol"] + (["luna"] if suffix == ".xlsx" else [])
        if not self.api_key or any(not self.deployments.get(k) for k in required):
            raise ExtractionError("configuration", "OpenAI-Schlüssel oder benötigte Deploymentnamen fehlen")
        if self.cache_mode not in ("explicit", "off") or self.cache_ttl != "30m" or not self.cache_namespace:
            raise ExtractionError("configuration", "Cachemodus explicit/off, TTL 30m und Namespace erforderlich")
        if any(v not in ("none", "minimal", "low", "medium", "high") for v in self.reasoning.values()):
            raise ExtractionError("configuration", "Nicht unterstützter Reasoning-Aufwand")
        if min(self.timeout, self.extraction_tokens, self.completeness_tokens, self.max_input_bytes, self.max_source_bytes) <= 0 or not 0 <= self.retries <= 5:
            raise ExtractionError("configuration", "Positive Limits und 0 bis 5 Retries erforderlich")
        if suffix != ".xlsx":
            di_url = urlsplit(self.di_endpoint)
            if not self.di_key or di_url.scheme != "https" or not di_url.netloc or di_url.username or di_url.password or di_url.query or di_url.fragment or not self.di_version:
                raise ExtractionError("configuration", "Gültige Document-Intelligence-Konfiguration für PDF/Word fehlt")
        if suffix == ".docx" and not shutil.which(self.converter):
            raise ExtractionError("converter_missing", "LibreOffice installieren: winget install TheDocumentFoundation.LibreOffice; LIBREOFFICE_PATH auf soffice.exe setzen")

    def public(self):
        return {k:v for k,v in vars(self).items() if k not in ("api_key", "di_key")}
