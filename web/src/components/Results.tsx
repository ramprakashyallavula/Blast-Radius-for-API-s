import { useMemo, useState } from "react";

import type { AnalyzeResponse, Change, ChangeImpact, Impact, ImpactLevel, Severity } from "../types";
import { InlineCode } from "./InlineCode";

const IMPACT_LABEL: Record<ImpactLevel, string> = {
  will_break: "Will break",
  may_break: "May break",
  unaffected: "Unaffected",
};

const SEVERITY_LABEL: Record<Severity, string> = {
  breaking: "Breaking",
  warning: "Warning",
  safe: "Safe",
};

const RANK: Record<ImpactLevel, number> = { will_break: 0, may_break: 1, unaffected: 2 };

const UNCERTAIN_HELP =
  "Inside a oneOf/anyOf branch: whether a consumer gets (or sends) that shape can't be known " +
  "from the code, so this change is never reported as more than \"may break\".";

type View = "change" | "consumer";
type Filter = "affected" | "will_break" | "all";

const FILTER_LABEL: Record<Filter, string> = {
  affected: "Will or may break",
  will_break: "Will break only",
  all: "Everything",
};

export function Results({ result }: { result: AnalyzeResponse }) {
  const [view, setView] = useState<View>("change");
  const [filter, setFilter] = useState<Filter>("affected");
  const { summary } = result;

  const visible = useMemo(() => {
    const keep = (i: Impact) =>
      filter === "all" || (filter === "will_break" ? i.impact === "will_break" : i.impact !== "unaffected");
    return result.changes
      .map((change) => ({ ...change, impacts: change.impacts.filter(keep) }))
      .filter((change) => change.impacts.length > 0);
  }, [result, filter]);

  const quiet = result.changes.filter((c) => c.impacts.every((i) => i.impact === "unaffected"));
  // The API lists each consumer once, under its worst result.
  const safeConsumers = summary.consumers.filter(
    (c) => !summary.will_break.includes(c) && !summary.may_break.includes(c),
  );
  const uncertain = [...result.changes, ...result.safe_changes].filter((c) => !c.certain).length;

  return (
    <section className="results" aria-labelledby="results-title">
      <Verdict result={result} />

      <div className="stat-grid">
        <Stat tone="danger" label="Will break" names={summary.will_break} />
        <Stat tone="warn" label="May break" names={summary.may_break} hint="needs a look" />
        <Stat tone="ok" label="Not affected" names={safeConsumers} />
      </div>
      <p className="muted small">
        {plural(summary.breaking_changes, "breaking change")} in the spec ·{" "}
        {plural(summary.call_sites, "call site")} found in {plural(summary.consumers.length, "consumer")}
        {summary.unmatched_call_sites > 0 &&
          ` · ${plural(summary.unmatched_call_sites, "call")} to endpoints not in the spec`}
        {uncertain > 0 && ` · ${plural(uncertain, "uncertain change")} (inside a oneOf/anyOf)`}
      </p>

      <div className="toolbar">
        <div className="segmented" role="group" aria-label="Group results">
          <button type="button" aria-pressed={view === "change"} onClick={() => setView("change")}>
            By change
          </button>
          <button type="button" aria-pressed={view === "consumer"} onClick={() => setView("consumer")}>
            By consumer
          </button>
        </div>
        <label className="select">
          <span className="visually-hidden">Show</span>
          <select value={filter} onChange={(e) => setFilter(e.target.value as Filter)} aria-label="Show">
            {(Object.keys(FILTER_LABEL) as Filter[]).map((f) => (
              <option key={f} value={f}>
                {FILTER_LABEL[f]}
              </option>
            ))}
          </select>
        </label>
        <div className="toolbar-actions">
          <CopyButton text={result.markdown} />
          <DownloadButton result={result} />
        </div>
      </div>

      {visible.length === 0 ? (
        <p className="empty-results">
          {result.changes.length === 0
            ? "No breaking changes or warnings in this spec change."
            : "Nothing to show with this filter."}
        </p>
      ) : view === "change" ? (
        <ByChange changes={visible} />
      ) : (
        <ByConsumer changes={visible} />
      )}

      <div className="extras">
        {quiet.length > 0 && (
          <details>
            <summary>
              {plural(quiet.length, "breaking change or warning", "breaking changes or warnings")} no
              consumer is affected by
            </summary>
            <ul className="plain-list">
              {quiet.map((c, i) => (
                <li key={i}>
                  <SeverityBadge severity={c.severity} /> <UncertainBadge change={c} /> <code>{endpoint(c)}</code>{" "}
                  <InlineCode text={c.message} />
                </li>
              ))}
            </ul>
          </details>
        )}
        {result.safe_changes.length > 0 && (
          <details>
            <summary>{plural(result.safe_changes.length, "safe change")}</summary>
            <ul className="plain-list">
              {result.safe_changes.map((c, i) => (
                <li key={i}>
                  <UncertainBadge change={c} /> <code>{endpoint(c)}</code> <InlineCode text={c.message} />
                </li>
              ))}
            </ul>
          </details>
        )}
        {result.unmatched.length > 0 && (
          <details>
            <summary>
              {plural(result.unmatched.length, "call")} to endpoints not in this spec
            </summary>
            <p className="muted small">Calls to other APIs, or to endpoints the spec doesn't document.</p>
            <ul className="plain-list">
              {result.unmatched.map((u, i) => (
                <li key={i}>
                  {u.consumer} <code>{`${u.file}:${u.line}`}</code> <code>{`${u.method} ${u.url}`}</code>
                </li>
              ))}
            </ul>
          </details>
        )}
        <details>
          <summary>Full report (Markdown)</summary>
          <p className="muted small">The same report the GitHub Action posts on a pull request.</p>
          <pre className="snippet report">{result.markdown}</pre>
        </details>
      </div>
    </section>
  );
}

function Verdict({ result }: { result: AnalyzeResponse }) {
  const { will_break, may_break } = result.summary;
  const [tone, title, text] = will_break.length
    ? ["danger", `💥 ${plural(will_break.length, "consumer")} will break`, will_break.join(", ")]
    : may_break.length
      ? ["warn", "Nothing is certain to break", "but some consumers need a look."]
      : ["ok", "✅ No consumer is affected", "This change looks safe to ship for the consumers scanned."];
  return (
    <div className={`verdict ${tone}`}>
      <h2 id="results-title">{title}</h2>
      <p>{text}</p>
    </div>
  );
}

function Stat({
  tone,
  label,
  names,
  hint,
}: {
  tone: string;
  label: string;
  names: string[];
  hint?: string;
}) {
  return (
    <div className={`stat ${tone}`}>
      <div className="stat-number">{names.length}</div>
      <div className="stat-label">
        {label}
        {hint && names.length > 0 && <span className="muted"> · {hint}</span>}
      </div>
      {names.length > 0 && <div className="stat-names">{names.join(", ")}</div>}
    </div>
  );
}

function ByChange({ changes }: { changes: ChangeImpact[] }) {
  return (
    <div className="cards">
      {changes.map((change, i) => (
        <article key={i} className="card">
          <header className="card-header">
            <SeverityBadge severity={change.severity} />
            <UncertainBadge change={change} />
            <code className="endpoint">{endpoint(change)}</code>
          </header>
          <p className="card-title">
            <InlineCode text={change.message} />
          </p>
          <ul className="impact-list">
            {sortImpacts(change.impacts).map((impact, j) => (
              <ImpactRow key={j} impact={impact} label={impact.consumer} />
            ))}
          </ul>
        </article>
      ))}
    </div>
  );
}

function ByConsumer({ changes }: { changes: ChangeImpact[] }) {
  const groups = new Map<string, { change: ChangeImpact; impact: Impact }[]>();
  for (const change of changes) {
    for (const impact of change.impacts) {
      groups.set(impact.consumer, [...(groups.get(impact.consumer) ?? []), { change, impact }]);
    }
  }
  const ordered = [...groups.entries()].sort(
    ([, a], [, b]) => worst(a.map((x) => x.impact)) - worst(b.map((x) => x.impact)),
  );

  return (
    <div className="cards">
      {ordered.map(([consumer, items]) => (
        <article key={consumer} className="card">
          <header className="card-header">
            <ImpactBadge level={sortImpacts(items.map((x) => x.impact))[0].impact} />
            <span className="consumer-title">{consumer}</span>
          </header>
          <ul className="impact-list">
            {items
              .sort((a, b) => RANK[a.impact.impact] - RANK[b.impact.impact])
              .map(({ change, impact }, j) => (
                <ImpactRow
                  key={j}
                  impact={impact}
                  label={
                    <>
                      <UncertainBadge change={change} /> <code>{endpoint(change)}</code>{" "}
                      <InlineCode text={change.message} />
                    </>
                  }
                />
              ))}
          </ul>
        </article>
      ))}
    </div>
  );
}

function ImpactRow({ impact, label }: { impact: Impact; label: React.ReactNode }) {
  return (
    <li className={`impact-row ${impact.impact}`}>
      <div className="impact-main">
        <ImpactBadge level={impact.impact} />
        <span className="impact-label">{label}</span>
        <code className="location">{`${impact.file}:${impact.line}`}</code>
      </div>
      <p className="impact-reason">
        <InlineCode text={impact.reason} />
      </p>
      {impact.code && <pre className="snippet">{impact.code}</pre>}
    </li>
  );
}

function ImpactBadge({ level }: { level: ImpactLevel }) {
  return <span className={`badge ${level}`}>{IMPACT_LABEL[level]}</span>;
}

function UncertainBadge({ change }: { change: Change }) {
  if (change.certain) return null;
  return (
    <span className="badge uncertain" title={UNCERTAIN_HELP} aria-label={`Uncertain: ${UNCERTAIN_HELP}`}>
      Uncertain
    </span>
  );
}

function SeverityBadge({ severity }: { severity: Severity }) {
  return <span className={`badge sev-${severity}`}>{SEVERITY_LABEL[severity]}</span>;
}

function CopyButton({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
    setTimeout(() => setState("idle"), 2000);
  }
  return (
    <button type="button" className="button small" onClick={copy}>
      {state === "copied" ? "Copied!" : state === "failed" ? "Couldn't copy" : "Copy as PR comment"}
    </button>
  );
}

function DownloadButton({ result }: { result: AnalyzeResponse }) {
  function download() {
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "blastradius-report.json";
    link.click();
    URL.revokeObjectURL(url);
  }
  return (
    <button type="button" className="button small" onClick={download}>
      Download JSON
    </button>
  );
}

function endpoint(change: { method: string; path: string }) {
  return `${change.method} ${change.path}`;
}

function sortImpacts(impacts: Impact[]) {
  return [...impacts].sort((a, b) => RANK[a.impact] - RANK[b.impact] || a.consumer.localeCompare(b.consumer));
}

function worst(impacts: Impact[]) {
  return Math.min(...impacts.map((i) => RANK[i.impact]));
}

function plural(n: number, word: string, many = `${word}s`) {
  return `${n} ${n === 1 ? word : many}`;
}
