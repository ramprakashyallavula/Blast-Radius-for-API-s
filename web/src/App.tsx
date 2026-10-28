import { useEffect, useRef, useState } from "react";

import { analyze, ApiError, getDemo } from "./api";
import { ConsumersPanel } from "./components/ConsumersPanel";
import { Results } from "./components/Results";
import { SpecEditor } from "./components/SpecEditor";
import type { AnalyzeResponse, Consumer } from "./types";

const REPO_URL = "https://github.com/ramprakashyallavula/Blast-Radius-for-API-s";

type Busy = "demo" | "analyze" | null;

export default function App() {
  const [baseSpec, setBaseSpec] = useState("");
  const [headSpec, setHeadSpec] = useState("");
  const [consumers, setConsumers] = useState<Consumer[]>([]);
  const [result, setResult] = useState<AnalyzeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const resultsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (result) resultsRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  }, [result]);

  async function run(kind: Busy, task: () => Promise<void>) {
    setBusy(kind);
    setError(null);
    try {
      await task();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(null);
    }
  }

  const loadDemo = () =>
    run("demo", async () => {
      const demo = await getDemo();
      setBaseSpec(demo.base_spec);
      setHeadSpec(demo.head_spec);
      setConsumers(demo.consumers);
      setResult(null);
    });

  const runAnalysis = () =>
    run("analyze", async () => {
      setResult(await analyze({ base_spec: baseSpec, head_spec: headSpec, consumers }));
    });

  const ready = baseSpec.trim() !== "" && headSpec.trim() !== "";

  return (
    <div className="page">
      <header className="site-header">
        <div className="brand">
          <span className="logo" aria-hidden>
            💥
          </span>
          <span className="brand-name">BlastRadius</span>
        </div>
        <a className="header-link" href={REPO_URL} target="_blank" rel="noreferrer">
          GitHub
        </a>
      </header>

      <main>
        <section className="hero">
          <h1>See which services an API change will break, before it ships.</h1>
          <p>
            Paste the current and proposed OpenAPI specs, add the services that call the API, and
            BlastRadius points to the exact lines of code that will break.
          </p>
          <button type="button" className="button" onClick={loadDemo} disabled={busy !== null}>
            {busy === "demo" ? "Loading…" : "Load the demo"}
          </button>
        </section>

        <div className="spec-grid">
          <SpecEditor
            label="Current spec"
            hint="What's live today (base)"
            value={baseSpec}
            onChange={setBaseSpec}
          />
          <SpecEditor
            label="Proposed spec"
            hint="The change you want to ship (head)"
            value={headSpec}
            onChange={setHeadSpec}
          />
        </div>

        <ConsumersPanel consumers={consumers} onChange={setConsumers} />

        <div className="action-bar">
          <button
            type="button"
            className="button primary large"
            onClick={runAnalysis}
            disabled={!ready || busy !== null}
          >
            {busy === "analyze" ? "Analyzing…" : "Analyze impact"}
          </button>
          {!ready && <span className="muted">Add both specs to analyze.</span>}
        </div>

        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}

        <div ref={resultsRef}>{result && <Results result={result} />}</div>
      </main>

      <footer className="site-footer">
        Everything runs in memory: your specs and code are not stored. ·{" "}
        <a href={REPO_URL} target="_blank" rel="noreferrer">
          Source code
        </a>
      </footer>
    </div>
  );
}
