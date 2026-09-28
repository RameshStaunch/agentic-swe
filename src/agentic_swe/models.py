from typing import Literal

from pydantic import BaseModel, Field

Scope = Literal["greenfield", "brownfield"]
Mode = Literal["suggest", "auto-edit", "full-auto"]
TaskKind = Literal["design", "codegen", "test", "docs"]


class NormalizedRequirement(BaseModel):
    intent: str = Field(description="One-sentence restatement of what the user actually wants.")
    scope: Scope
    language: str = Field("python", description="Implementation language: the existing codebase's, or for a new project the one the "
                                                "requirement names or implies (python, typescript, go, ...). Default python.")
    ambiguities: list[str] = Field(description="Things that could reasonably be interpreted more than one way. Empty if none.")
    clarifying_questions: list[str] = Field(description="One question per ambiguity, answerable in a sentence.")
    assumptions: list[str] = Field(description="Defaults you would pick if nobody answers.")
    acceptance_criteria: list[str]


class Task(BaseModel):
    id: str = Field(description="Short slug, e.g. 'api-routes'.")
    title: str
    kind: TaskKind
    description: str
    depends_on: list[str] = Field(default_factory=list, description="Ids of tasks that must finish first.")
    files: list[str] = Field(description="Repo-relative paths this task may create or modify. Nothing else may be touched.")


class TaskGraph(BaseModel):
    rationale: str = Field(description="Why the work is split this way.")
    tasks: list[Task]


class ApiEndpoint(BaseModel):
    method: str
    path: str
    request: str
    response: str
    notes: str = ""


class DesignDoc(BaseModel):
    components: list[str]
    api_contract: list[ApiEndpoint]
    data_model: str = Field(description="SQL DDL or equivalent schema description.")
    key_decisions: list[str] = Field(description="Decision and the trade-off it makes.")


class ImpactAnalysis(BaseModel):
    summary: str
    impacted_files: list[str]
    impacted_apis: list[str]
    data_flow_changes: list[str]
    risk_notes: list[str]


class FileWrite(BaseModel):
    path: str = Field(description="Repo-relative path.")
    content: str = Field(description="Full new file content.")


class CodeChange(BaseModel):
    files: list[FileWrite]
    notes: str = Field(description="What changed and why, for the reviewer.")


class ValidationReport(BaseModel):
    recommendation: Literal["approve", "revise"]
    risks: list[str]
    trade_offs: list[str]
    failure_scenarios: list[str]
    guardrails: list[str] = Field(description="Checks or limits that keep the change safe in production.")


class EngineeringSummary(BaseModel):
    plan: str
    rationale: str
    risks: list[str]
    trade_offs: list[str]
    assumptions: list[str]
    limitations: list[str]
