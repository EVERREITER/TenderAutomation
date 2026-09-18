"""Typed Excel extraction, validation, and result contracts."""
from typing import Literal
from pydantic import BaseModel, ConfigDict


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Address(Wire):
    document: Literal["original"]
    sheet: str | None
    cell_range: str | None


class SourceReference(Wire):
    id: str
    address: Address
    quote: str


class Question(Wire):
    id: str
    original: str
    normalized: str
    notes: str | None
    note_source_ids: list[str]
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
    control_type: Literal["excel_cell", "excel_dropdown", "printed", "unknown"]
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


class Check(Wire):
    subject: str
    code: str
    result: Literal["passed", "failed", "not_verifiable", "not_applicable"]
    severity: Literal["info", "warning", "error"] = "info"
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
