// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, AUTH_REQUIRED } from "./client";

const fetchMock = vi.fn<typeof fetch>();
const create = vi.fn<(blob: Blob) => string>(() => "blob:download");
const revoke = vi.fn();
let clicked: { name: string; href: string }[] = [];

beforeEach(async () => {
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL = create;
    static revokeObjectURL = revoke;
  });
  clicked = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    clicked.push({ name: this.download, href: this.href });
  });
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ token: "temporary-test-session", expires_in: 3600 })));
  await api.login("test-only-key");
  fetchMock.mockClear();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.runAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("authenticated downloads", () => {
  it.each(["csv", "xlsx", "parquet", "md"] as const)("downloads %s only after success with the server filename and exact bytes", async (kind) => {
    const bytes = new Uint8Array([1, 7, 255]);
    fetchMock.mockResolvedValueOnce(new Response(bytes, { headers: {
      "Content-Disposition": `attachment; filename="server-file.${kind}"`,
      "Content-Type": "application/octet-stream",
    } }));
    await api.download("dataset", kind);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe(kind === "md" ? "/api/dataset/dataset/report.md" : `/api/dataset/dataset/export.${kind}`);
    expect(new Headers(options?.headers).get("Authorization")).toBe("Bearer temporary-test-session");
    expect(String(url)).not.toContain("test-only-key");
    expect(clicked).toEqual([{ name: `server-file.${kind}`, href: "blob:download" }]);
    expect(create).toHaveBeenCalledTimes(1);
    const blob = create.mock.calls[0][0];
    expect(new Uint8Array(await blob.arrayBuffer())).toEqual(bytes);
    expect(blob.type).toBe("application/octet-stream");
    expect(document.querySelector("a")).toBeNull();
    vi.runAllTimers();
    expect(revoke).toHaveBeenCalledWith("blob:download");
  });

  it.each(["csv", "xlsx", "parquet", "md"] as const)("uses the intended %s filename if the server omits it", async (kind) => {
    fetchMock.mockResolvedValueOnce(new Response("contents"));
    await api.download("dataset", kind);
    expect(clicked[0].name).toBe(kind === "md" ? "dataset-report.md" : `dataset.${kind}`);
  });

  it.each([401, 500])("does not create a download for HTTP %s", async (status) => {
    const expired = vi.fn();
    window.addEventListener(AUTH_REQUIRED, expired);
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ error: "request failed" }), { status }));
    await expect(api.download("dataset", "csv")).rejects.toMatchObject({ status, message: "request failed" });
    expect(create).not.toHaveBeenCalled();
    expect(clicked).toEqual([]);
    expect(expired).toHaveBeenCalledTimes(status === 401 ? 1 : 0);
    window.removeEventListener(AUTH_REQUIRED, expired);
    if (status === 401) {
      fetchMock.mockResolvedValueOnce(new Response(""));
      await api.download("dataset", "csv");
      expect(new Headers(fetchMock.mock.calls[1][1]?.headers).has("Authorization")).toBe(false);
    }
  });
});

it("reuses the collection identity after an interrupted request and exposes partial progress", async () => {
  const summary = { dataset_id: "x-stable-request", source_type: "x", query: "topic", record_count: 100, labelled_count: 0, truncated_reason: "upstream failure", partial: true, resume_request_id: "stable-request", preview: [] };
  fetchMock.mockRejectedValueOnce(new TypeError("network interrupted"));
  await expect(api.load("x", 500, "topic", undefined, undefined, "stable-request")).rejects.toThrow();
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(summary)));
  const result = await api.load("x", 500, "topic", undefined, undefined, "stable-request");
  expect(result.partial).toBe(true);
  const bodies = fetchMock.mock.calls.map(([, options]) => JSON.parse(String(options?.body)) as { request_id: string });
  expect(bodies.map((body) => body.request_id)).toEqual(["stable-request", "stable-request"]);
});
