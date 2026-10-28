"""Run the evaluation set and print precision/recall.

python eval/run_eval.py            # table + every miss and false alarm
python eval/run_eval.py --json     # machine-readable, e.g. to track over time
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from blastradius.evaluation import run_all, total

CASES = Path(__file__).resolve().parent / "cases"


def pct(x: float) -> str:
    return f"{x:.0%}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args()

    results = run_all(CASES)
    strict, lenient = total(results, "strict"), total(results, "lenient")

    if args.json:
        print(
            json.dumps(
                {
                    mode: {
                        "precision": round(s.precision, 3),
                        "recall": round(s.recall, 3),
                        "true_positives": len(s.true_positives),
                        "false_positives": len(s.false_positives),
                        "false_negatives": len(s.false_negatives),
                    }
                    for mode, s in (("strict", strict), ("lenient", lenient))
                },
                indent=2,
            )
        )
        return

    real = len(strict.true_positives) + len(strict.false_negatives)
    print(f"BlastRadius evaluation: {len(results)} cases, {real} labeled breaking call sites\n")
    print(f"{'case':22} {'strict P':>9} {'strict R':>9} {'lenient P':>10} {'lenient R':>10}")
    for r in results:
        print(
            f"{r.name:22} {pct(r.strict.precision):>9} {pct(r.strict.recall):>9} "
            f"{pct(r.lenient.precision):>10} {pct(r.lenient.recall):>10}"
        )
    print(
        f"{'TOTAL':22} {pct(strict.precision):>9} {pct(strict.recall):>9} "
        f"{pct(lenient.precision):>10} {pct(lenient.recall):>10}"
    )
    print("\nstrict = only 'will break' counts; lenient = 'will break' or 'may break' counts")

    for r in results:
        hedged = r.lenient.true_positives - r.strict.true_positives
        problems = (
            [("missed", k) for k in sorted(r.lenient.false_negatives, key=str)]
            + [("false alarm", k) for k in sorted(r.lenient.false_positives, key=str)]
            + [("only 'may break'", k) for k in sorted(hedged, key=str)]
        )
        if problems:
            print(f"\n{r.name}:")
            for label, (consumer, location, endpoint, fld) in problems:
                what = f"{endpoint} `{fld}`" if fld else endpoint
                print(f"  {label:17} {consumer} {location}  {what}")


if __name__ == "__main__":
    main()
