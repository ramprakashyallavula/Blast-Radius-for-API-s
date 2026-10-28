import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import type { AnalyzeRequest, AnalyzeResponse } from "./types";

const DEMO: AnalyzeRequest = {
  base_spec: "openapi: 3.0.3\npaths: {}",
  head_spec: "openapi: 3.0.3\npaths: {}\n# v2",
  consumers: [{ name: "billing-service", files: [{ path: "invoices.py", content: "..." }] }],
};

const RESULT: AnalyzeResponse = {
  version: "0.1.0",
  summary: {
    breaking_changes: 1,
    consumers: ["billing-service"],
    will_break: ["billing-service"],
    may_break: [],
    call_sites: 1,
    unmatched_call_sites: 0,
  },
  changes: [
    {
      kind: "response_field_removed",
      severity: "breaking",
      method: "GET",
      path: "/orders/{orderId}",
      field: "discount_code",
      message: "Response field `discount_code` was removed",
      worst_impact: "will_break",
      impacts: [
        {
          consumer: "billing-service",
          file: "invoices.py",
          line: 11,
          impact: "will_break",
          reason: "reads `discount_code`, which was removed",
          code: "resp = requests.get(...)",
        },
      ],
    },
  ],
  safe_changes: [],
  unmatched: [],
  markdown: "## report",
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function stubApi() {
  const fetchMock = vi.fn(async (url: string) =>
    Response.json(url.endsWith("/demo") ? DEMO : RESULT),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("App", () => {
  it("can't analyze until both specs are filled in", () => {
    render(<App />);

    expect((screen.getByRole("button", { name: "Analyze impact" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Add both specs to analyze.")).toBeTruthy();
  });

  it("loads the demo, analyzes it, and shows who breaks", async () => {
    const fetchMock = stubApi();
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "Load the demo" }));
    expect(await screen.findByText("billing-service")).toBeTruthy();
    expect((screen.getByLabelText("Current spec") as HTMLTextAreaElement).value).toBe(DEMO.base_spec);

    fireEvent.click(screen.getByRole("button", { name: "Analyze impact" }));

    expect(await screen.findByText(/1 consumer\(s\) will break: billing-service/)).toBeTruthy();
    expect(screen.getByText("invoices.py:11")).toBeTruthy();
    const body = JSON.parse((fetchMock.mock.calls[1] as unknown as [string, RequestInit])[1].body as string);
    expect(body.consumers[0].name).toBe("billing-service");
  });

  it("shows API errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ detail: "The base spec is not valid YAML/JSON" }, { status: 422 })),
    );
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "Load the demo" }));

    expect((await screen.findByRole("alert")).textContent).toContain("not valid YAML/JSON");
  });
});
