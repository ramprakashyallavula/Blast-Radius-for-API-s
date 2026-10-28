import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AnalyzeResponse, ChangeImpact } from "../types";
import { Results } from "./Results";

function change(over: Partial<ChangeImpact>): ChangeImpact {
  return {
    kind: "response_field_removed",
    severity: "breaking",
    method: "GET",
    path: "/orders/{id}",
    field: "total",
    message: "Response field `total` was removed",
    certain: true,
    worst_impact: "will_break",
    impacts: [],
    ...over,
  };
}

const RESULT: AnalyzeResponse = {
  version: "0.2.0",
  summary: {
    breaking_changes: 2,
    consumers: ["billing", "shipping", "web"],
    will_break: ["billing"],
    may_break: ["web"],
    call_sites: 4,
    unmatched_call_sites: 1,
  },
  changes: [
    change({
      impacts: [
        { consumer: "billing", file: "inv.py", line: 11, impact: "will_break", reason: "reads `total`", code: "r = get()" },
        { consumer: "web", file: "o.ts", line: 3, impact: "may_break", reason: "passes it on", code: "" },
        { consumer: "shipping", file: "s.py", line: 7, impact: "unaffected", reason: "never reads `total`", code: "" },
      ],
    }),
    change({
      field: "legacy",
      message: "Response field `legacy` was removed",
      worst_impact: "unaffected",
      impacts: [{ consumer: "shipping", file: "s.py", line: 7, impact: "unaffected", reason: "x", code: "" }],
    }),
  ],
  safe_changes: [{ kind: "endpoint_added", severity: "safe", method: "POST", path: "/refunds", field: null, message: "New endpoint", certain: true }],
  unmatched: [{ consumer: "web", file: "o.ts", line: 9, method: "POST", url: "/{}/cancel" }],
  markdown: "## 💥 BlastRadius report",
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Results", () => {
  it("summarizes who breaks", () => {
    render(<Results result={RESULT} />);

    expect(screen.getByText("💥 1 consumer will break")).toBeTruthy();
    const stats = document.querySelector(".stat.danger") as HTMLElement;
    expect(within(stats).getByText("billing")).toBeTruthy();
    expect(screen.getByText(/1 call to endpoints not in the spec/)).toBeTruthy();
  });

  it("hides unaffected consumers by default and can show everything", () => {
    render(<Results result={RESULT} />);
    expect(screen.queryByText("s.py:7")).toBeNull();

    fireEvent.change(screen.getByLabelText("Show"), { target: { value: "all" } });

    expect(screen.getAllByText("s.py:7").length).toBeGreaterThan(0);
  });

  it("can show only certain breakages", () => {
    render(<Results result={RESULT} />);

    fireEvent.change(screen.getByLabelText("Show"), { target: { value: "will_break" } });

    expect(screen.getByText("inv.py:11")).toBeTruthy();
    expect(screen.queryByText("o.ts:3")).toBeNull();
  });

  it("groups by consumer", () => {
    render(<Results result={RESULT} />);

    fireEvent.click(screen.getByRole("button", { name: "By consumer" }));

    const titles = Array.from(document.querySelectorAll(".consumer-title")).map((e) => e.textContent);
    expect(titles).toEqual(["billing", "web"]);
  });

  it("lists quiet, safe and unmatched changes in collapsed sections", () => {
    render(<Results result={RESULT} />);

    expect(screen.getByText(/1 breaking change or warning no consumer is affected by/)).toBeTruthy();
    expect(screen.getByText("1 safe change")).toBeTruthy();
    expect(screen.getByText("1 call to endpoints not in this spec")).toBeTruthy();
  });

  it("marks uncertain union changes and explains them", () => {
    const union = change({
      field: "last4",
      message: "Response field `last4` was removed (in the `Card` branch)",
      certain: false,
      worst_impact: "may_break",
      impacts: [{ consumer: "web", file: "pay.ts", line: 4, impact: "may_break", reason: "reads `last4`", code: "" }],
    });
    render(<Results result={{ ...RESULT, changes: [union, ...RESULT.changes] }} />);

    const badges = screen.getAllByText("Uncertain");
    expect(badges).toHaveLength(1);
    expect(badges[0].getAttribute("title")).toMatch(/oneOf\/anyOf/);
    expect(screen.getByText(/1 uncertain change/)).toBeTruthy();
  });

  it("shows no uncertainty marks when every change is certain", () => {
    render(<Results result={RESULT} />);

    expect(screen.queryByText("Uncertain")).toBeNull();
    expect(screen.queryByText(/uncertain change/)).toBeNull();
  });

  it("shows the full Markdown report", () => {
    render(<Results result={{ ...RESULT, markdown: "## 💥 BlastRadius report\n\nfull text" }} />);

    expect(screen.getByText("Full report (Markdown)")).toBeTruthy();
    expect(document.querySelector("pre.report")?.textContent).toContain("full text");
  });

  it("uses the API's consumer groups as they are (they never overlap)", () => {
    render(<Results result={RESULT} />);

    const may = document.querySelector(".stat.warn") as HTMLElement;
    const fine = document.querySelector(".stat.ok") as HTMLElement;
    expect(within(may).getByText("web")).toBeTruthy();
    expect(within(fine).getByText("shipping")).toBeTruthy();
  });

  it("copies the PR comment", async () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(<Results result={RESULT} />);

    fireEvent.click(screen.getByRole("button", { name: "Copy as PR comment" }));

    expect(await screen.findByText("Copied!")).toBeTruthy();
    expect(writeText).toHaveBeenCalledWith("## 💥 BlastRadius report");
  });
});
