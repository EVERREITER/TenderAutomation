"""Define the pipeline's categorized application error type.

ExtractionError(code, message) carries a stable error category alongside a
human-readable explanation. Readers, configuration checks, and service adapters
raise it; the CLI and pipeline use it to report failures consistently. This
module has no executable entry point.
"""
class ExtractionError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)
