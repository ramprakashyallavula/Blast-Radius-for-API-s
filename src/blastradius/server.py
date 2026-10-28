"""HTTP API for the BlastRadius web playground.

    uvicorn blastradius.server:app --port 8787 --reload   # docs: http://127.0.0.1:8787/docs

Everything happens in memory: specs and consumer files arrive in the request body,
nothing is written to disk, and nothing is stored after the response is sent.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from . import __version__
from .impact import analyze
from .report import to_dict, to_markdown
from .scanner import SOURCE_SUFFIXES, scan_text
from .spec import Spec, SpecError

# The API is public, so every input has a size limit.
MAX_SPEC_CHARS = 500_000
MAX_FILE_CHARS = 200_000
MAX_FILES = 200
MAX_CONSUMERS = 20

app = FastAPI(
    title="BlastRadius API",
    version=__version__,
    description="Predict which services an API change will break, before it ships.",
)

app.add_middleware(
    CORSMiddleware,
    # Comma-separated list of frontend origins allowed to call the API.
    # "*" is fine here: the API is read-only and uses no cookies or credentials.
    allow_origins=os.environ.get("BLASTRADIUS_CORS_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# --- request and response models -------------------------------------------


class SourceFile(BaseModel):
    path: str = Field(max_length=300, examples=["billing/invoices.py"])
    content: str = Field(max_length=MAX_FILE_CHARS)


class Consumer(BaseModel):
    name: str = Field(min_length=1, max_length=100, examples=["billing-service"])
    files: list[SourceFile] = Field(max_length=MAX_FILES)


class AnalyzeRequest(BaseModel):
    base_spec: str = Field(
        min_length=1, max_length=MAX_SPEC_CHARS, description="The current spec, YAML or JSON."
    )
    head_spec: str = Field(
        min_length=1, max_length=MAX_SPEC_CHARS, description="The proposed spec, YAML or JSON."
    )
    consumers: list[Consumer] = Field(default_factory=list, max_length=MAX_CONSUMERS)


ImpactLevel = Literal["will_break", "may_break", "unaffected"]


class ChangeOut(BaseModel):
    kind: str
    severity: Literal["breaking", "warning", "safe"]
    method: str
    path: str
    field: str | None
    message: str
    before: Any = None
    after: Any = None
    certain: bool = Field(
        description="False for a change inside a oneOf/anyOf branch: whether a consumer gets "
        "or sends that branch can't be known, so its impact is at most may_break."
    )


class ImpactOut(BaseModel):
    consumer: str
    file: str
    line: int
    impact: ImpactLevel
    reason: str
    code: str


class ChangeImpactOut(ChangeOut):
    worst_impact: ImpactLevel
    impacts: list[ImpactOut]


class UnmatchedOut(BaseModel):
    consumer: str
    file: str
    line: int
    method: str
    url: str


class SummaryOut(BaseModel):
    breaking_changes: int
    consumers: list[str]
    will_break: list[str] = Field(description="Consumers with at least one call that will break.")
    may_break: list[str] = Field(
        description="Consumers with a call that may break and none that will break. "
        "The two lists never overlap."
    )
    call_sites: int
    unmatched_call_sites: int


class AnalyzeResponse(BaseModel):
    version: str
    summary: SummaryOut
    changes: list[ChangeImpactOut] = Field(description="Breaking and warning changes, worst first.")
    safe_changes: list[ChangeOut]
    unmatched: list[UnmatchedOut] = Field(description="Calls to endpoints not in the spec.")
    markdown: str = Field(description="The same report as a pull-request comment.")


class Health(BaseModel):
    status: Literal["ok"]
    version: str


# --- endpoints -------------------------------------------------------------


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/docs")


@app.get("/health")
def health() -> Health:
    return Health(status="ok", version=__version__)


@app.get("/demo")
def demo() -> AnalyzeRequest:
    """The README demo as a ready-made request: Orders API v1 -> v2 and three consumers."""
    examples = _examples_dir()
    service = examples / "orders-service"
    consumers = []
    for folder in sorted(p for p in (examples / "consumers").iterdir() if p.is_dir()):
        files = [
            SourceFile(path=f.relative_to(folder).as_posix(), content=f.read_text(encoding="utf-8"))
            for f in sorted(folder.rglob("*"))
            if f.is_file() and f.suffix in SOURCE_SUFFIXES
        ]
        consumers.append(Consumer(name=folder.name, files=files))
    return AnalyzeRequest(
        base_spec=(service / "openapi.v1.yaml").read_text(encoding="utf-8"),
        head_spec=(service / "openapi.v2.yaml").read_text(encoding="utf-8"),
        consumers=consumers,
    )


@app.post("/analyze", responses={422: {"description": "A spec couldn't be read."}})
def analyze_specs(request: AnalyzeRequest) -> AnalyzeResponse:
    """Diff two specs and report which consumers break."""
    if sum(len(c.files) for c in request.consumers) > MAX_FILES:
        raise HTTPException(422, f"Too many files: the limit is {MAX_FILES} in total.")

    base = _parse_spec(request.base_spec, "base spec")
    head = _parse_spec(request.head_spec, "head spec")
    sites = [
        site
        for consumer in request.consumers
        for f in consumer.files
        if Path(f.path).suffix in SOURCE_SUFFIXES
        for site in scan_text(f.content, consumer=consumer.name, file=f.path)
    ]
    try:
        analysis = analyze(base, head, sites, [c.name for c in request.consumers])
    except SpecError as e:  # e.g. a $ref that points nowhere
        raise HTTPException(422, str(e)) from e
    except RecursionError as e:
        raise HTTPException(422, "The spec is nested too deeply to analyze.") from e
    return AnalyzeResponse(**to_dict(analysis), markdown=to_markdown(analysis))


# --- helpers ---------------------------------------------------------------


def _parse_spec(text: str, label: str) -> Spec:
    try:
        # YAML aliases (*name) can expand a small document into a huge one
        # (the "billion laughs" attack). Real OpenAPI specs use $ref instead.
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.AliasEvent):
                raise HTTPException(
                    422, f"The {label} uses YAML aliases (*name), which aren't allowed."
                )
    except yaml.YAMLError as e:
        raise HTTPException(422, f"The {label} is not valid YAML/JSON: {e}") from e
    try:
        return Spec.from_text(text, label)
    except SpecError as e:
        raise HTTPException(422, str(e)) from e


def _examples_dir() -> Path:
    # Installed from a wheel, examples are bundled inside the package; in a source
    # checkout (or an editable install) they're at the repository root.
    for candidate in (Path(__file__).parent / "examples", Path(__file__).parents[2] / "examples"):
        if (candidate / "orders-service").is_dir():
            return candidate
    raise HTTPException(503, "Demo files are not available in this installation.")
