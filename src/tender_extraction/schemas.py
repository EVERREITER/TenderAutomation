"""Define typed model-response, review, validation, and result contracts.

Extraction and Completeness are the main structured-output entry points:
model_json_schema() produces their API schemas and model_validate_json()
validates responses. Completeness also checks review-status consistency.
Result, FinalQuestion, FinalField, and Check represent locally enriched output.
Wire models reject extra properties and use explicit nullable fields.
"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, model_validator


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Address(Wire):
    document: Literal["original", "rendered_pdf"]
    sheet: str | None
    cell_range: str | None
    page: int | None
    # Unrotated PDF media-box points, top-left origin; 1-based page.
    bbox: list[float] | None
    word_path: str | None
    form_field: str | None


class SourceReference(Wire):
    id: str
    address: Address
    quote: str


class Question(Wire):
    id: str
    original: str
    normalized: str
    language: str | None
    kind: str | None
    section: str | None
    number: str | None
    order: int
    source_ids: list[str]
    context_status: Literal["extracted", "unknown"]
    context_source_ids: list[str]
    position_ids: list[str]
    subquestion: str | None
    expected_answer: str | None
    confidence: float | None


class AnswerField(Wire):
    id: str
    question_id: str
    label: str | None
    role: Literal["main_answer", "comment", "number", "evidence_reference", "unknown"]
    semantic_type: Literal["text", "number", "date", "yes_no", "single_choice", "multi_choice", "document", "unknown"]
    control_type: Literal["excel_cell", "excel_dropdown", "pdf_text", "pdf_choice", "pdf_button", "word_control", "printed", "unknown"]
    target_status: Literal["located", "not_present", "unresolved"]
    address: Address | None
    order: int
    source_ids: list[str]


class Option(Wire):
    id: str
    field_id: str
    label: str
    value: str | float | int | bool | None
    meaning: str | None
    order: int
    source_ids: list[str]


class Position(Wire):
    id: str
    lot: str | None
    product: str | None
    strength: str | None
    pack: str | None
    form: str | None
    source_ids: list[str]


class Attribute(Wire):
    owner_type: Literal["document", "question", "field"]
    owner_id: str  # 'document' or the local question/field id
    name: Literal["title", "tender_reference", "customer", "market", "answer_language", "deadline_original", "document_kind", "product", "lot", "supplier", "company", "site", "period", "row_context", "column_context", "condition", "answer_instruction", "required", "max_length", "unit", "minimum", "maximum", "found_value", "validation_rule", "shared_target_group"]
    value: str | float | int | bool | None
    source_ids: list[str]


class Extraction(Wire):
    questions: list[Question]
    answer_fields: list[AnswerField]
    options: list[Option]
    source_references: list[SourceReference]
    positions: list[Position]
    attributes: list[Attribute]
    limitations: list[str]


class MissingItem(Wire):
    quote: str
    source_reference: Address
    context: str
    reason: str


class Completeness(Wire):
    review_status: Literal["no_missing_found", "missing_found", "unable_to_assess"]
    missing_items: list[MissingItem]
    limitations: list[str]

    @model_validator(mode="after")
    def consistent(self):
        if self.missing_items:
            if self.review_status != "missing_found" or any(not x.quote.strip() or not x.reason.strip() or not x.context.strip() for x in self.missing_items):
                raise ValueError("Belegte Lücken erfordern missing_found und vollständige Belege")
            for item in self.missing_items:
                a = item.source_reference
                if not ((a.sheet and a.cell_range) or (a.page is not None and a.page > 0) or a.word_path):
                    raise ValueError("Lücke ohne konkrete Fundstelle")
        elif self.review_status == "missing_found":
            raise ValueError("missing_found ohne Lücke")
        if self.review_status == "no_missing_found" and self.limitations:
            raise ValueError("no_missing_found mit Beurteilungseinschränkungen")
        if self.review_status == "unable_to_assess" and not self.limitations:
            raise ValueError("unable_to_assess benötigt Begründung")
        return self


class Check(Wire):
    subject: str
    code: str
    result: Literal["passed", "failed", "not_verifiable", "not_applicable"]
    observed: str
    claim: str
    reason: str


class FinalField(AnswerField):
    key: str
    options: list[dict]
    attributes: list[Attribute]
    checks: list[Check]


class FinalQuestion(Question):
    key: str
    answer_fields: list[FinalField]
    attributes: list[Attribute]
    checks: list[Check]


class Result(Wire):
    schema_version: str
    document: dict
    run: dict
    questions: list[FinalQuestion]
    positions: list[Position]
    source_references: list[SourceReference]
    document_metadata: list[Attribute]
    limitations: list[str]
