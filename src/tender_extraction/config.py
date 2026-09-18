"""Load and validate configuration for the local extraction pipeline.

Config.from_env() reads the project .env and environment variables. The
Config.validate(suffix) method checks settings and format-specific prerequisites
before cloud calls; Config.public() returns configuration without API keys for
run manifests. The configuration includes deployments, reasoning budgets,
request limits and prompt caching for Sol Excel extraction.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import dotenv_values
from .errors import ExtractionError


@dataclass
class Config:
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    deployments: dict = field(default_factory=dict)
    reasoning: dict = field(default_factory=lambda: {"sol_extraction": "high"})
    timeout: float = 900
    retries: int = 2
    extraction_tokens: int = 125000
    cache_mode: str = "explicit"
    cache_namespace: str = "tender-extraction-v1"
    cache_ttl: str = "30m"
    max_input_bytes: int = 20_000_000
    max_source_bytes: int = 100_000_000

    @classmethod
    def from_env(cls):
        # Read afresh on every invocation. Local project settings take precedence
        # over inherited terminal values without mutating os.environ.
        local = dotenv_values(Path.cwd() / ".env")
        values = {**os.environ, **{k:v for k,v in local.items() if v is not None}}
        g = values.get
        try:
            return cls(base_url=g("AZURE_OPENAI_BASE_URL", ""), api_key=g("AZURE_OPENAI_API_KEY", ""),
                deployments={"sol":g("AZURE_OPENAI_DEPLOYMENT_SOL", "")},
                reasoning={"sol_extraction":g("REASONING_SOL_EXTRACTION", "high")},
                timeout=float(g("OPENAI_TIMEOUT_SECONDS", "900")), retries=int(g("OPENAI_MAX_RETRIES", "2")),
                extraction_tokens=int(g("EXTRACTION_MAX_OUTPUT_TOKENS", "125000")),
                cache_mode=g("OPENAI_PROMPT_CACHE_MODE", "explicit"), cache_namespace=g("OPENAI_PROMPT_CACHE_NAMESPACE", "tender-extraction-v1"),
                cache_ttl=g("OPENAI_PROMPT_CACHE_TTL", "30m"),
                max_input_bytes=int(g("MAX_INPUT_BYTES", "20000000")), max_source_bytes=int(g("MAX_SOURCE_BYTES", "100000000")))
        except ValueError as e:
            raise ExtractionError("configuration", "Ungültige numerische .env-Einstellung") from e

    def validate(self, suffix):
        u = urlsplit(self.base_url)
        if u.scheme != "https" or not u.netloc or u.username or u.password or u.query or u.fragment or u.path.rstrip("/") != "/openai/v1":
            raise ExtractionError("configuration", "AZURE_OPENAI_BASE_URL muss https://<resource>/openai/v1/ sein, kein Projektendpunkt")
        if suffix != ".xlsx":
            raise ExtractionError("unsupported_format", "Unterstützt: .xlsx")
        if not self.api_key or not self.deployments.get("sol"):
            raise ExtractionError("configuration", "OpenAI-Schlüssel oder benötigte Deploymentnamen fehlen")
        if self.cache_mode not in ("explicit", "off") or self.cache_ttl != "30m" or not self.cache_namespace:
            raise ExtractionError("configuration", "Cachemodus explicit/off, TTL 30m und Namespace erforderlich")
        if any(v not in ("none", "minimal", "low", "medium", "high") for v in self.reasoning.values()):
            raise ExtractionError("configuration", "Nicht unterstützter Reasoning-Aufwand")
        if min(self.timeout, self.extraction_tokens, self.max_input_bytes, self.max_source_bytes) <= 0 or not 0 <= self.retries <= 5:
            raise ExtractionError("configuration", "Positive Limits und 0 bis 5 Retries erforderlich")

    def public(self):
        return {k:v for k,v in vars(self).items() if k not in ("api_key",)}
