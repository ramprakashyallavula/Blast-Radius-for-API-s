import { describe, expect, it } from "vitest";

import { consumerFromFolder, isSourcePath, MAX_FILE_BYTES, type PickedFile } from "./files";

function picked(relativePath: string, content = "x", size = content.length): PickedFile {
  return { relativePath, size, read: async () => content };
}

describe("isSourcePath", () => {
  it("accepts Python and JS/TS files", () => {
    expect(isSourcePath("app/client.py")).toBe(true);
    expect(isSourcePath("src/api/orders.ts")).toBe(true);
  });

  it("rejects other files and dependency folders", () => {
    expect(isSourcePath("README.md")).toBe(false);
    expect(isSourcePath("node_modules/axios/index.js")).toBe(false);
    expect(isSourcePath(".venv/lib/site.py")).toBe(false);
  });
});

describe("consumerFromFolder", () => {
  it("names the consumer after the folder and keeps only source files", async () => {
    const { consumer, skippedLarge } = await consumerFromFolder([
      picked("billing-service/billing/invoices.py", "code"),
      picked("billing-service/README.md"),
      picked("billing-service/node_modules/lib/x.js"),
      picked("billing-service/app.ts", "more"),
    ]);

    expect(consumer).toEqual({
      name: "billing-service",
      files: [
        { path: "app.ts", content: "more" },
        { path: "billing/invoices.py", content: "code" },
      ],
    });
    expect(skippedLarge).toEqual([]);
  });

  it("skips files over the size limit", async () => {
    const { consumer, skippedLarge } = await consumerFromFolder([
      picked("svc/small.py"),
      picked("svc/huge.js", "x", MAX_FILE_BYTES + 1),
    ]);

    expect(consumer?.files.map((f) => f.path)).toEqual(["small.py"]);
    expect(skippedLarge).toEqual(["huge.js"]);
  });

  it("returns no consumer when there's no source code", async () => {
    const { consumer } = await consumerFromFolder([picked("docs/guide.md")]);

    expect(consumer).toBeNull();
  });
});
