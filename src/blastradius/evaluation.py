"""Measure how accurate BlastRadius is against hand-labeled cases.

Each case folder has a `case.yaml` with the two specs, the consumers, and a
hand-written list of the call sites that *really* break. We run the analysis and
compare its verdicts with that ground truth:

    precision = of the call sites we flagged, how many really break?
    recall    = of the call sites that really break, how many did we flag?

Two modes, because "may break" is a hedge:
    strict   only "will break" counts as a flag
    lenient  "will break" or "may break" counts as a flag
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .impact import analyze
from .models import Impact
from .scanner import scan_consumer
from .spec import Spec

Key = tuple[str, str, str, str | None]  # (consumer, file:line, endpoint, field)


@dataclass
class Score:
    true_positives: set[Key] = field(default_factory=set)
    false_positives: set[Key] = field(default_factory=set)
    false_negatives: set[Key] = field(default_factory=set)

    @property
    def precision(self) -> float:
        flagged = len(self.true_positives) + len(self.false_positives)
        return len(self.true_positives) / flagged if flagged else 1.0

    @property
    def recall(self) -> float:
        real = len(self.true_positives) + len(self.false_negatives)
        return len(self.true_positives) / real if real else 1.0

    def __add__(self, other: Score) -> Score:
        return Score(
            self.true_positives | other.true_positives,
            self.false_positives | other.false_positives,
            self.false_negatives | other.false_negatives,
        )


@dataclass
class CaseResult:
    name: str
    description: str
    strict: Score
    lenient: Score


def score(truth: set[Key], flagged: set[Key]) -> Score:
    return Score(truth & flagged, flagged - truth, truth - flagged)


def run_case(folder: Path) -> CaseResult:
    case = yaml.safe_load((folder / "case.yaml").read_text(encoding="utf-8"))
    base = Spec.load(folder / case["base"])
    head = Spec.load(folder / case["head"])
    consumers_dir = folder / case["consumers_dir"]
    dirs = sorted(p for p in consumers_dir.iterdir() if p.is_dir())
    analysis = analyze(base, head, [s for d in dirs for s in scan_consumer(d)])

    truth: set[Key] = {
        (b["consumer"], b["location"], b["endpoint"], b.get("field"))
        for b in case.get("breaks") or []
    }
    verdicts: dict[Impact, set[Key]] = {impact: set() for impact in Impact}
    for ci in analysis.impacts:
        for i in ci.impacts:
            key = (i.site.consumer, i.site.location, ci.change.endpoint, ci.change.field)
            verdicts[i.impact].add(key)

    # A case lists every real break, so any other flag is a false positive.
    return CaseResult(
        name=folder.name,
        description=" ".join(str(case.get("description", "")).split()),
        strict=score(truth, verdicts[Impact.WILL_BREAK]),
        lenient=score(truth, verdicts[Impact.WILL_BREAK] | verdicts[Impact.MAY_BREAK]),
    )


def run_all(cases_dir: Path) -> list[CaseResult]:
    return [run_case(p) for p in sorted(cases_dir.iterdir()) if (p / "case.yaml").is_file()]


def total(results: list[CaseResult], mode: str) -> Score:
    out = Score()
    for r in results:
        # Keys don't collide across cases in practice; prefix them to be safe.
        s: Score = getattr(r, mode)
        out = out + Score(
            {(r.name, *k) for k in s.true_positives},
            {(r.name, *k) for k in s.false_positives},
            {(r.name, *k) for k in s.false_negatives},
        )
    return out
