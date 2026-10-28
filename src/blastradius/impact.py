"""Decide, for every breaking change, which call sites actually break.

The key distinction is *reading* vs. *sending* a field:
    order["total"], order.get("total"), order.total     -> reads `total`
    {"currency": "USD"}, { currency }, currency="USD"   -> sends `currency`
Response changes only matter to code that reads the field; request changes only
matter to code that sends it (or fails to send a newly required one).

Reads only count when they're on the response: the analysis follows the variables
that hold it (resp -> order = resp.json() -> for item in order["items"]) instead of
matching any `.total` in the function. When the response leaves the function, or a
read can't be tied to it, the answer is "may break", never "unaffected".
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from .diff import _TOKEN, diff_specs, leaf_name
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
from .scanner import OPERATION_PREFIX
from .spec import Spec, normalize_path

# Fallback when the response isn't stored in a variable:
# `return resp.json()`, `return data`, `return res.data;`.
_RETURNS_RESPONSE = re.compile(r"^\s*return\s+(?:await\s+)?[\w.]+(?:\(\))?\s*;?\s*$", re.MULTILINE)
_SENDS_BODY = re.compile(r"\b(?:json|data|body|content)\s*[=:]")
# Signs that the body is sent or the response is read as JSON.
_SENDS_JSON = re.compile(r"\bjson\s*=|JSON\.stringify|\baxios\.(?:post|put|patch)\b")
_READS_JSON = re.compile(r"\.json\(\)|\b(?:res|resp|response)\.data\b|\{\s*data\s*\}")
_CHECKS_SUCCESS_CODE = re.compile(r"\b20\d\b")

_UNCERTAIN_SUFFIX = " (inside a oneOf/anyOf branch, so it depends on which shape this call gets)"

_ASSIGNMENT = re.compile(
    r"^\s*(?:(?:const|let|var)\s+)?(?P<target>\{[^}]*\}|\[[^\]]*\]|[A-Za-z_$][\w$.]*)"
    r"\s*(?::[^=]+)?=(?![=>])\s*(?P<value>.*)$"
)
# A body passed as a variable: json=payload, body: JSON.stringify(order), axios.post(url, data)
_BODY_VARIABLE = re.compile(
    r"\b(?:json|data|body)\s*[=:]\s*(?:JSON\.stringify\(\s*)?(?P<a>[A-Za-z_$][\w$]*)\s*[,)}\n]"
    r"|\baxios\.(?:post|put|patch)\s*\([^,]+,\s*(?P<b>[A-Za-z_$][\w$]*)\s*[,)]"
)


def reads_field(context: str, name: str) -> bool:
    """Any read of `name` in the code, on any variable."""
    n = re.escape(name)
    pattern = rf"""\[\s*['"]{n}['"]\s*\]|\.get\(\s*['"]{n}['"]|\??\.{n}\b"""
    return re.search(pattern, context) is not None


def sends_field(context: str, name: str) -> bool:
    n = re.escape(name)
    pattern = (
        rf"""['"]{n}['"]\s*:"""  # {"currency": ...}
        rf"""|[{{,]\s*{n}\s*[,}}:]"""  # { currency } or { currency: x } (JS)
        rf"""|\b{n}\s*=(?!=)"""  # currency="USD" (keyword argument)
        rf"""|[?&]{n}="""  # ?currency=USD
        rf"""|\[\s*['"]{n}['"]\s*\]\s*=(?!=)"""  # payload["currency"] = ...
    )
    return re.search(pattern, context) is not None


def _alternation(names: Iterable[str]) -> str:
    return "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))


def _destructured(target: str) -> list[str]:
    """`{ data }` -> [data]; `{ name: n, sku }` -> [n, sku]; `[a, b]` -> [a, b]."""
    inner = target[1:-1]
    return [alias or key for key, alias in re.findall(r"([\w$]+)\s*(?::\s*([\w$]+))?", inner)]


def response_names(context: str) -> set[str]:
    """Variables that hold the response, or data taken from it.

    The call's own line gives the first name (`resp = requests.get(...)`), then any
    variable assigned from, iterated over, or destructured out of a known one joins.
    """
    lines = context.splitlines()
    if not lines:
        return set()
    names: set[str] = set()
    first = _ASSIGNMENT.match(lines[0])
    if first:
        target = first.group("target")
        names.update(_destructured(target) if target[0] in "{[" else [target])
    changed = True
    while changed and names:
        changed = False
        known = _alternation(names)
        chain = rf"(?:await\s+)?\(?\s*(?:await\s+)?(?:{known})\b[^\s;,]*"
        for line in lines[1:]:
            new: list[str] = []
            assigned = _ASSIGNMENT.match(line)
            if assigned and re.fullmatch(rf"\s*{chain}\s*\)?\s*;?\s*", assigned.group("value")):
                target = assigned.group("target")
                new += _destructured(target) if target[0] in "{[" else [target]
            # for item in order["items"] / for (const item of order.items)
            new += re.findall(
                rf"\bfor\s*\(?\s*(?:const\s+|let\s+|var\s+)?([\w$]+)\s+(?:in|of)\s+{chain}", line
            )
            # order.items.map((item) => ...), .forEach(item => ...)
            new += re.findall(
                rf"\b(?:{known})\b[^\s;]*\.(?:map|forEach|filter|find|some|every|flatMap)"
                r"\(\s*(?:async\s*)?\(?\s*([\w$]+)",
                line,
            )
            for name in new:
                if name not in names:
                    names.add(name)
                    changed = True
    return names


def field_use(context: str, path: str | None, names: set[str]) -> str | None:
    """How the code reads field `path` of the response.

    "direct"    read on the response or data derived from it
    "unclear"   something called `path` is read, but not visibly from this response
    None        not read at all
    """
    tokens = [t for t in _TOKEN.findall(path or "") if t != "[]"]
    if not tokens:
        return None
    leaf, parent = tokens[-1], (tokens[-2] if len(tokens) > 1 else None)
    if not reads_field(context, leaf) and not re.search(
        rf"\{{[^}}]*\b{re.escape(leaf)}\b[^}}]*\}}\s*=", context
    ):
        return None
    if not names:
        return "direct"  # the call's result isn't stored: any read is likely on it
    known = _alternation(names)
    n = re.escape(leaf)
    access = rf"""(?:\[\s*['"]{n}['"]\s*\]|\.get\(\s*['"]{n}['"]|\??\.{n}\b)"""
    on_response = re.search(rf"(?<![\w$.])(?:{known})\b[^\s=;,]*?{access}", context)
    destructured = re.search(
        rf"\{{[^}}]*\b{n}\b[^}}]*\}}\s*=\s*[^;\n]*(?:\b(?:{known})\b|\(|await)", context
    )
    if not (on_response or destructured or (leaf in names)):
        return "unclear"
    if parent and not (reads_field(context, parent) or parent in names):
        return "unclear"  # a `name` is read, but not visibly from `customer`
    return "direct"


def passes_on(context: str, names: set[str]) -> bool:
    """Does the response leave this code (returned, passed to a function, stored)?"""
    if not names:
        return _RETURNS_RESPONSE.search(context) is not None
    known = _alternation(names)
    return any(
        re.search(pattern, context, re.MULTILINE)
        for pattern in (
            rf"^\s*return\s+(?:await\s+)?\(?(?:{known})(?:\.json\(\)|\.data)?\)?\s*;?\s*$",
            # some_function(order), save(resp.json()), render(...data)
            rf"[(,]\s*(?:\.\.\.|\*\*?)?(?:await\s+)?(?:{known})(?:\.json\(\)|\.data)?\s*[,)]",
            rf"\b(?:self|this)\.[\w$]+\s*=\s*(?:{known})\b",
            rf"\byield\s+(?:{known})\b",
        )
    )


def _returns(context: str, names: set[str]) -> bool:
    if not names:
        return _RETURNS_RESPONSE.search(context) is not None
    known = _alternation(names)
    pattern = rf"^\s*return\s+(?:await\s+)?\(?(?:{known})(?:\.json\(\)|\.data)?\)?\s*;?\s*$"
    return re.search(pattern, context, re.MULTILINE) is not None


def _body_built_elsewhere(site: CallSite) -> bool:
    """The call sends a body variable whose contents aren't visible in this function."""
    match = _BODY_VARIABLE.search("\n".join(site.context.splitlines()[:8]))
    if not match:
        return False
    name = match.group("a") or match.group("b")
    assigned = re.search(
        rf"(?:^|[\s(,])(?:const\s+|let\s+|var\s+)?{re.escape(name)}\s*(?::[^=]+)?=(?![=>])",
        site.preamble,
        re.MULTILINE,
    )
    return assigned is None


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
        return _assess_read(change, site, *_ON_READ[kind])

    sent = site.preamble + "\n" + ctx  # payloads are often built before the call
    if kind in _MUST_SEND:
        if sends_field(sent, f):
            return result(Impact.UNAFFECTED, "already sends `{f}`")
        if _body_built_elsewhere(site):
            return result(
                Impact.MAY_BREAK, "sends a body built elsewhere; couldn't check it includes `{f}`"
            )
        return result(Impact.WILL_BREAK, "does not send `{f}`, which is now required")

    if kind in _ON_SEND:
        if sends_field(sent, f):
            return result(Impact.MAY_BREAK, _ON_SEND[kind])
        if _body_built_elsewhere(site):
            return result(
                Impact.MAY_BREAK,
                "sends a body built elsewhere; couldn't check whether it has `{f}`",
            )
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


def _assess_read(change: Change, site: CallSite, impact: Impact, reason: str) -> ConsumerImpact:
    f = leaf_name(change.field)

    def result(level: Impact, text: str) -> ConsumerImpact:
        return ConsumerImpact(site, level, text.format(f=f))

    names = response_names(site.context)
    use = field_use(site.context, change.field, names)
    if use == "direct":
        return result(impact, reason)

    # The function hands the response back: look at what its callers in this file read.
    if site.function and _returns(site.context, names):
        callers_pass_on = not site.callers
        for line, code in site.callers:
            caller_names = response_names(code)
            if field_use(code, change.field, caller_names) == "direct":
                return result(
                    impact, reason + f" (via `{site.function}()`'s result at line {line})"
                )
            callers_pass_on = callers_pass_on or passes_on(code, caller_names)
        if callers_pass_on:
            return result(
                Impact.MAY_BREAK,
                f"returns the response from `{site.function}()`, and the code using it "
                "couldn't all be traced",
            )
        return result(Impact.UNAFFECTED, f"`{site.function}()`'s callers never read `{{f}}`")

    if use == "unclear":
        return result(
            Impact.MAY_BREAK,
            "reads a `{f}` field, but couldn't confirm it comes from this response",
        )
    if passes_on(site.context, names):
        return result(
            Impact.MAY_BREAK,
            "passes the response on to other code, so use of `{f}` couldn't be traced",
        )
    return result(Impact.UNAFFECTED, "uses the response but never reads `{f}`")


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

    matched = {s for group in matches.by_operation.values() for s in group}
    return Analysis(
        changes=changes,
        impacts=impacts,
        consumers=sorted(set(consumers) | {s.consumer for s in sites}),
        # Generated-client candidates only count once they match an operationId.
        sites=[s for s in sites if not s.url.startswith(OPERATION_PREFIX) or s in matched],
        unmatched=matches.unmatched,
    )
