import { afterEach, describe, expect, it, vi } from "vitest";

import { analyze, ApiError, formatDetail, getDemo } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

function mockFetch(impl: () => Promise<Response>) {
  const fn = vi.fn(impl);
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("formatDetail", () => {
  it("passes strings through", () => {
    expect(formatDetail("base spec: only OpenAPI 3.x")).toBe("base spec: only OpenAPI 3.x");
  });

  it("formats FastAPI validation errors", () => {
    const detail = [{ loc: ["body", "head_spec"], msg: "Field required" }];
    expect(formatDetail(detail)).toBe("head_spec: Field required");
  });

  it("returns null for anything else", () => {
    expect(formatDetail({ weird: true })).toBeNull();
  });
});

describe("requests", () => {
  it("posts the analysis request as JSON", async () => {
    const fetchMock = mockFetch(async () => Response.json({ summary: {} }));

    await analyze({ base_spec: "a", head_spec: "b", consumers: [] });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/analyze$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ base_spec: "a", head_spec: "b", consumers: [] });
  });

  it("turns API errors into readable messages", async () => {
    mockFetch(async () => Response.json({ detail: "The base spec is not valid YAML/JSON" }, { status: 422 }));

    await expect(getDemo()).rejects.toThrow("The base spec is not valid YAML/JSON");
  });

  it("explains when the API can't be reached", async () => {
    mockFetch(async () => {
      throw new TypeError("Failed to fetch");
    });

    const error = await getDemo().catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.message).toMatch(/Can't reach the BlastRadius API/);
  });
});
