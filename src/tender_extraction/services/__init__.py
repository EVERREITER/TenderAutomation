"""Group explicitly invoked Azure service adapters.

OpenAIService in openai_client handles structured extraction and review calls;
document_intelligence.analyze() obtains PDF layout evidence. This package
initializer has no callable entry point and does not create clients or issue
network requests on import.
"""
