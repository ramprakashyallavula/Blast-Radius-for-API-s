"""Connect call sites found in code to the API operations they hit.

A call site's URL looks like "/{}/v1/orders/{}" and an operation's path looks like
"/orders/{orderId}". We line them up from the right-hand end, segment by segment:

    call:       {}   v1   orders   {}
    operation:             orders   {orderId}
                └─prefix─┘ └─ must match ─┘

The prefix may only contain things that look like a base URL ({}, "api", "v1", or
words from the spec's `servers`). Otherwise "/customers/{}/orders" would wrongly
match "/orders".
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from .models import CallSite
from .spec import Operation

_BASE_SEGMENT = re.compile(r"\{\}|api|v\d+(?:\.\d+)?")


@dataclass
class MatchResult:
    by_operation: dict[tuple[str, str], list[CallSite]] = field(default_factory=dict)
    unmatched: list[CallSite] = field(default_factory=list)

    @property
    def matched_count(self) -> int:
        return sum(len(sites) for sites in self.by_operation.values())


def match_call_sites(
    sites: Iterable[CallSite],
    operations: Iterable[Operation],
    base_segments: set[str] = frozenset(),
) -> MatchResult:
    """Group call sites by the operation they call. Calls to other APIs end up in `unmatched`."""
    operations = list(operations)
    result = MatchResult()
    for site in sites:
        op = match_operation(site, operations, base_segments)
        if op is None:
            result.unmatched.append(site)
        else:
            result.by_operation.setdefault(op.key, []).append(site)
    return result


def match_operation(
    site: CallSite, operations: Iterable[Operation], base_segments: set[str] = frozenset()
) -> Operation | None:
    """The most specific operation this call could be hitting, or None."""
    call = _segments(site.url)
    best, best_score = None, -1
    for op in operations:
        if op.method != site.method:
            continue
        template = _segments(op.path)
        if not template or len(template) > len(call):
            continue

        split = len(call) - len(template)
        prefix, tail = call[:split], call[split:]
        if not all(s in base_segments or _BASE_SEGMENT.fullmatch(s) for s in prefix):
            continue

        score = _score(template, tail)
        if score is not None and score > best_score:
            best, best_score = op, score
    return best


def _segments(url: str) -> list[str]:
    return [s for s in url.split("/") if s]


def _score(template: list[str], call: list[str]) -> int | None:
    """Higher is more specific. None means the call can't be this operation.

    An exact word match scores 2 and a {param} scores 1, so "/orders/search" beats
    "/orders/{id}" for a call to "/orders/search".
    """
    score = 0
    for expected, actual in zip(template, call, strict=True):
        if "{" in expected:
            score += 1  # a {param} accepts any value, literal or dynamic
        elif "{}" in actual or expected != actual:
            return None  # a fixed word must appear exactly
        else:
            score += 2
    return score
