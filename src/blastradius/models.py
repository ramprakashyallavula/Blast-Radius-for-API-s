"""Shared data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    BREAKING = "breaking"  # old clients will fail
    WARNING = "warning"  # old clients might fail, depending on how they're written
    SAFE = "safe"  # old clients keep working


class ChangeKind(str, Enum):
    ENDPOINT_REMOVED = "endpoint_removed"
    ENDPOINT_ADDED = "endpoint_added"

    PARAM_ADDED_REQUIRED = "param_added_required"
    PARAM_ADDED_OPTIONAL = "param_added_optional"
    PARAM_BECAME_REQUIRED = "param_became_required"
    PARAM_TYPE_CHANGED = "param_type_changed"
    PARAM_ENUM_RESTRICTED = "param_enum_restricted"
    PARAM_REMOVED = "param_removed"
    PATH_PARAM_TYPE_CHANGED = "path_param_type_changed"
    PATH_PARAM_ENUM_RESTRICTED = "path_param_enum_restricted"

    REQUEST_BODY_BECAME_REQUIRED = "request_body_became_required"
    REQUEST_FIELD_ADDED_REQUIRED = "request_field_added_required"
    REQUEST_FIELD_ADDED_OPTIONAL = "request_field_added_optional"
    REQUEST_FIELD_BECAME_REQUIRED = "request_field_became_required"
    REQUEST_FIELD_TYPE_CHANGED = "request_field_type_changed"
    REQUEST_ENUM_VALUE_REMOVED = "request_enum_value_removed"
    REQUEST_FIELD_NO_LONGER_NULLABLE = "request_field_no_longer_nullable"
    REQUEST_UNION_VARIANT_REMOVED = "request_union_variant_removed"
    REQUEST_MEDIA_TYPE_REMOVED = "request_media_type_removed"
    REQUEST_FIELD_REMOVED = "request_field_removed"

    RESPONSE_FIELD_REMOVED = "response_field_removed"
    RESPONSE_FIELD_ADDED = "response_field_added"
    RESPONSE_FIELD_TYPE_CHANGED = "response_field_type_changed"
    RESPONSE_FIELD_BECAME_OPTIONAL = "response_field_became_optional"
    RESPONSE_ENUM_VALUE_ADDED = "response_enum_value_added"
    RESPONSE_FIELD_BECAME_NULLABLE = "response_field_became_nullable"
    RESPONSE_UNION_VARIANT_ADDED = "response_union_variant_added"
    RESPONSE_MEDIA_TYPE_REMOVED = "response_media_type_removed"
    RESPONSE_STATUS_REMOVED = "response_status_removed"
    RESPONSE_STATUS_ADDED = "response_status_added"


@dataclass
class Change:
    """One difference between two versions of an API."""

    kind: ChangeKind
    severity: Severity
    method: str
    path: str
    message: str
    field: str | None = None  # field path (e.g. "items[].sku") or parameter name
    before: Any = None
    after: Any = None
    # False when the change is inside a oneOf/anyOf branch: a consumer that uses the
    # field may never receive (or send) that branch, so impact is at most "may break".
    certain: bool = True

    @property
    def endpoint(self) -> str:
        return f"{self.method} {self.path}"


@dataclass(frozen=True)
class CallSite:
    """One place in a consumer's code that makes an HTTP request."""

    consumer: str  # service name, e.g. "billing-service"
    file: str  # path relative to the consumer's root
    line: int  # 1-based line of the call
    method: str  # "GET", "POST", ...
    url: str  # normalized URL template, e.g. "/{}/orders/{}"
    snippet: str  # the source line, for reports
    context: str  # the call plus the code after it, used to see which fields it touches
    # The code before the call in the same function (request payloads are often built there).
    preamble: str = ""
    function: str | None = None  # name of the function the call is in, if any
    # If that function hands the response back to its callers: the code after each call
    # to it in the same file, as (line, code). Used to follow fields read one level up.
    callers: tuple[tuple[int, str], ...] = ()

    @property
    def location(self) -> str:
        return f"{self.file}:{self.line}"


class Impact(str, Enum):
    WILL_BREAK = "will_break"  # we saw the code use the thing that changed
    MAY_BREAK = "may_break"  # we couldn't tell for sure; a human should look
    UNAFFECTED = "unaffected"  # calls the endpoint but doesn't touch what changed


@dataclass
class ConsumerImpact:
    """How one change affects one call site, and why."""

    site: CallSite
    impact: Impact
    reason: str


@dataclass
class ChangeImpact:
    """One breaking or warning change, with its effect on every call to that endpoint."""

    change: Change
    impacts: list[ConsumerImpact] = field(default_factory=list)

    @property
    def affected(self) -> list[ConsumerImpact]:
        return [i for i in self.impacts if i.impact is not Impact.UNAFFECTED]

    @property
    def worst(self) -> Impact:
        for level in (Impact.WILL_BREAK, Impact.MAY_BREAK):
            if any(i.impact is level for i in self.impacts):
                return level
        return Impact.UNAFFECTED
