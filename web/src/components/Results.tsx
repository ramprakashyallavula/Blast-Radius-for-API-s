import type { AnalyzeResponse, ImpactLevel } from "../types";
import { InlineCode } from "./InlineCode";

const IMPACT_LABEL: Record<ImpactLevel, string> = {
  will_break: "Will break",
  may_break: "May break",
  unaffected: "Unaffected",
};

// A first version of the results; Step 3 turns this into the full report UI.
export function Results({ result }: { result: AnalyzeResponse }) {
  const { summary } = result;
  const affected = result.changes.filter((c) => c.impacts.some((i) => i.impact !== "unaffected"));

  return (
    <section className="results" aria-live="polite">
      <h2>
        {summary.will_break.length
          ? `💥 ${summary.will_break.length} consumer(s) will break: ${summary.will_break.join(", ")}`
          : summary.may_break.length
            ? "No consumer is certain to break, but some need a look."
            : "✅ No consumer is affected by this change."}
      </h2>
      <p className="muted">
        {summary.breaking_changes} breaking changes in the spec · {summary.call_sites} call sites in{" "}
        {summary.consumers.length} consumers
      </p>

      {affected.map((change, i) => (
        <article key={i} className="change">
          <h3>
            <code>
              {change.method} {change.path}
            </code>{" "}
            <InlineCode text={change.message} />
          </h3>
          <ul>
            {change.impacts.map((impact, j) => (
              <li key={j} className={`impact ${impact.impact}`}>
                <strong>{IMPACT_LABEL[impact.impact]}</strong> {impact.consumer}{" "}
                <code>
                  {impact.file}:{impact.line}
                </code>{" "}
                — <InlineCode text={impact.reason} />
              </li>
            ))}
          </ul>
        </article>
      ))}
    </section>
  );
}
