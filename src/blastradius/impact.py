"""Decide, for every breaking change, which call sites actually break.

The key distinction is *reading* vs. *sending* a field:
    order["total"], order.get("total"), order.total     -> reads `total`
    {"currency": "USD"}, { currency }, currency="USD"   -> sends `currency`
Response changes only matter to code that reads the field; request changes only
matter to code that sends it (or fails to send a newly required one).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from .diff import diff_specs, leaf_name
from .matcher import match_call_sites
from .models import (
    CallSite,
    Change,
    ChangeImpact,
    ConsumerImpact,
    Impact,
    Severity,
)
from .models import (
    ChangeKind as K,
)
from .spec import Spec, normalize_path

# `return resp.json()`, `return data`, `return res.data;`: the response leaves this
# function, so its fields may be used somewhere we didn't scan.
_RETURNS_RESPONSE = re.compile(r"^\s*return\s+(?:await\s+)?[\w.]+(?:\(\))?\s*;?\s*$", re.MULTILINE)
_SENDS_BODY = re.compile(r"\b(?:json|data|body|content)\s*[=:]")
# Signs that the body is sent or the response is read as JSON.
_SENDS_JSON = re.compile(r"\bjson\s*=|JSON\.stringify|\baxios\.(?:post|put|patch)\b")
_READS_JSON = re.compile(r"\.json\(\)|\b(?:res|resp|response)\.data\b|\{\s*data\s*\}")
_CHECKS_SUCCESS_CODE = re.compile(r"\b20\d\b")

_UNCERTAIN_SUFFIX = " (inside a oneOf/anyOf branch, so it depends on which shape this call gets)"


def reads_field(context: str, name: str) -> bool:
    n = re.escape(name)
    pattern = rf"""\[\s*['"]{n}['"]\s*\]|\.get\(\s*['"]{n}['"]|\.{n}\b"""
    return re.search(pattern, context) is not None


def sends_field(context: str, name: str) -> bool:
    n = re.escape(name)
    pattern = (
        rf"""['"]{n}['"]\s*:"""  # {"currency": ...}
        rf"""|[{{,]\s*{n}\s*[,}}:]"""  # { currency } or { currency: x } (JS)
        rf"""|\b{n}\s*=(?!=)"""  # currency="USD" (keyword argument)
        rf"""|[?&]{n}="""  # ?currency=USD
    )
    return re.search(pattern, context) is not None


def _returns_response(context: str) -> bool:
    return _RETURNS_RESPONSE.search(context) is not None


# --- the rules -------------------------------------------------------------

# Response changes: what happens to code that reads the field.
_ON_READ = {
    K.RESPONSE_FIELD_REMOVED: (Impact.WILL_BREAK, "reads `{f}`, which was removed"),
    K.RESPONSE_FIELD_TYPE_CHANGED: (Impact.WILL_BREAK, "reads `{f}`, whose type changed"),
    K.RESPONSE_FIELD_BECAME_OPTIONAL: (Impact.MAY_BREAK, "reads `{f}`, which may now be missing"),
    K.RESPONSE_ENUM_VALUE_ADDED: (
        Impact.MAY_BREAK,
        "reads `{f}`; check it handles the new value(s)",
    ),
    K.RESPONSE_FIELD_BECAME_NULLABLE: (Impact.MAY_BREAK, "reads `{f}`, which can now be null"),
    K.RESPONSE_UNION_VARIANT_ADDED: (
        Impact.MAY_BREAK,
        "reads `{f}`, which can now take a shape this code may not expect",
    ),
}

# Request changes where old code breaks if it does NOT send the field.
_MUST_SEND = {
    K.PARAM_ADDED_REQUIRED,
    K.PARAM_BECAME_REQUIRED,
    K.REQUEST_FIELD_ADDED_REQUIRED,
    K.REQUEST_FIELD_BECAME_REQUIRED,
}

# Request changes where old code might break if it DOES send the field.
_ON_SEND = {
    K.PARAM_TYPE_CHANGED: "sends `{f}`; check the value matches the new type",
    K.REQUEST_FIELD_TYPE_CHANGED: "sends `{f}`; check the value matches the new type",
    K.REQUEST_ENUM_VALUE_REMOVED: "sends `{f}`; check it only uses the values still allowed",
    K.PARAM_ENUM_RESTRICTED: "sends `{f}`; check it only uses the values still allowed",
    K.REQUEST_FIELD_NO_LONGER_NULLABLE: "sends `{f}`; check it never sends null",
    K.REQUEST_UNION_VARIANT_REMOVED: "sends `{f}`; check it doesn't use a shape that was removed",
    K.PARAM_REMOVED: "still sends `{f}`, which the API no longer declares",
    K.REQUEST_FIELD_REMOVED: "still sends `{f}`, which the API no longer declares",
}


def assess(change: Change, site: CallSite) -> ConsumerImpact:
    """How `change` affects the code at `site`."""
    result = _assess(change, site)
    if not change.certain and result.impact is Impact.WILL_BREAK:
        # We know the code uses the field, but not that it ever sees the changed branch.
        return ConsumerImpact(site, Impact.MAY_BREAK, result.reason + _UNCERTAIN_SUFFIX)
    return result


def _assess(change: Change, site: CallSite) -> ConsumerImpact:
    kind, ctx, f = change.kind, site.context, leaf_name(change.field)

    def result(impact: Impact, reason: str) -> ConsumerImpact:
        return ConsumerImpact(site, impact, reason.format(f=f))

    if kind is K.ENDPOINT_REMOVED:
        return result(Impact.WILL_BREAK, "calls this endpoint, which was removed")

    if kind in (K.PATH_PARAM_TYPE_CHANGED, K.PATH_PARAM_ENUM_RESTRICTED):
        return result(
            Impact.MAY_BREAK, "puts `{f}` in the URL; check the value it sends is still valid"
        )

    if kind is K.RESPONSE_UNION_VARIANT_ADDED and not change.field:
        return result(
            Impact.MAY_BREAK, "the response can now take a shape this code may not expect"
        )

    if kind is K.REQUEST_UNION_VARIANT_REMOVED and not change.field:
        return result(Impact.MAY_BREAK, "check the body it sends still matches an accepted shape")

    if kind in _ON_READ:
        impact, reason = _ON_READ[kind]
        if reads_field(ctx, f):
            return result(impact, reason)
        if _returns_response(ctx):
            return result(
                Impact.MAY_BREAK,
                "returns the response to other code, so use of `{f}` could not be traced",
            )
        return result(Impact.UNAFFECTED, "calls this endpoint but never reads `{f}`")

    if kind in _MUST_SEND:
        if sends_field(ctx, f):
            return result(Impact.UNAFFECTED, "already sends `{f}`")
        return result(Impact.WILL_BREAK, "does not send `{f}`, which is now required")

    if kind in _ON_SEND:
        if sends_field(ctx, f):
            return result(Impact.MAY_BREAK, _ON_SEND[kind])
        return result(Impact.UNAFFECTED, "does not send `{f}`")

    if kind is K.REQUEST_BODY_BECAME_REQUIRED:
        if _SENDS_BODY.search(ctx):
            return result(Impact.UNAFFECTED, "already sends a request body")
        return result(Impact.MAY_BREAK, "no request body detected, and one is now required")

    if kind is K.REQUEST_MEDIA_TYPE_REMOVED:
        if "json" in str(change.before) and _SENDS_JSON.search(ctx):
            return result(Impact.WILL_BREAK, "sends JSON, which is no longer accepted")
        return result(
            Impact.MAY_BREAK, f"check how it encodes the body: `{change.before}` was removed"
        )

    if kind is K.RESPONSE_MEDIA_TYPE_REMOVED:
        if "json" in str(change.before) and _READS_JSON.search(ctx):
            return result(Impact.WILL_BREAK, "reads the response as JSON, which is no longer sent")
        return result(
            Impact.MAY_BREAK, f"check how it reads the response: `{change.before}` was removed"
        )

    if kind is K.RESPONSE_STATUS_REMOVED:
        if str(change.before) in ctx:
            return result(Impact.MAY_BREAK, f"checks for status {change.before}, which was removed")
        return result(Impact.UNAFFECTED, "does not check for the removed status")

    if kind is K.RESPONSE_STATUS_ADDED:
        if _CHECKS_SUCCESS_CODE.search(ctx):
            return result(
                Impact.MAY_BREAK,
                f"checks for specific status codes; it may not handle {change.after}",
            )
        return result(
            Impact.MAY_BREAK,
            f"couldn't tell whether it handles the new {change.after} response",
        )

    # No specific rule: say so instead of claiming the code is safe.
    return result(Impact.MAY_BREAK, "no rule for this kind of change yet; please check by hand")


# --- putting it together ---------------------------------------------------


@dataclass
class Analysis:
    """The full answer: what changed, who calls it, and who breaks."""

    changes: list[Change]
    impacts: list[ChangeImpact]  # one per breaking/warning change
    consumers: list[str]
    sites: list[CallSite]
    unmatched: list[CallSite]

    @property
    def breaking(self) -> list[Change]:
        return [c for c in self.changes if c.severity is Severity.BREAKING]

    def consumers_with(self, impact: Impact) -> list[str]:
        return sorted(
            {i.site.consumer for ci in self.impacts for i in ci.impacts if i.impact is impact}
        )


def analyze(
    base: Spec, head: Spec, sites: Iterable[CallSite], consumers: Iterable[str] = ()
) -> Analysis:
    sites = list(sites)
    changes = diff_specs(base, head)

    # Match against the *current* API: that's what consumer code is written for,
    # and removed endpoints only exist there.
    matches = match_call_sites(
        sites, base.operations().values(), base.base_path_segments | head.base_path_segments
    )

    impacts = []
    for change in changes:
        if change.severity is Severity.SAFE:
            continue
        callers = matches.by_operation.get((change.method, normalize_path(change.path)), [])
        impacts.append(ChangeImpact(change, [assess(change, site) for site in callers]))

    return Analysis(
        changes=changes,
        impacts=impacts,
        consumers=sorted(set(consumers) | {s.consumer for s in sites}),
        sites=sites,
        unmatched=matches.unmatched,
    )
