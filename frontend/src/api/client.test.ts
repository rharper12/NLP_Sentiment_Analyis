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
  it("opens a cloud download directly without forwarding the API token or fetching S3", async () => {
    const url = "https://bucket.s3.us-east-1.amazonaws.com/_downloads/id/export.csv?X-Amz-Signature=test";
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ url, filename: "export.csv", expires_in: 300 }), { headers: { "Content-Type": "application/vnd.sentiment-prep.download+json" } }));
    await api.download("dataset", "csv");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(clicked).toEqual([{ name: "export.csv", href: url }]);
    expect(create).not.toHaveBeenCalled();
    expect(document.querySelector("a")).toBeNull();
  });

  it.each(["javascript:alert(1)", "http://insecure.test/file", "broken"])("rejects an invalid cloud URL: %s", async (url) => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ url, filename: "export.csv", expires_in: 300 }), { headers: { "Content-Type": "application/vnd.sentiment-prep.download+json" } }));
    await expect(api.download("dataset", "csv")).rejects.toThrow("Unexpected response");
    expect(clicked).toEqual([]);
  });
  it("sends the local timezone and encodes a custom stem while keeping the CSV route", async () => {
    fetchMock.mockResolvedValueOnce(new Response("contents", { headers: { "Content-Disposition": 'attachment; filename="My results.csv"' } }));
    await api.download("dataset", "csv", undefined, "My results");
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/dataset/dataset/export.csv?filename=My+results");
    expect(new Headers(options?.headers).get("X-Time-Zone")).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
    expect(clicked[0].name).toBe("My results.csv");
  });
  it.each(["csv", "xlsx", "parquet", "md", "original.json"] as const)("downloads %s only after success with the server filename and exact bytes", async (kind) => {
    const bytes = new Uint8Array([1, 7, 255]);
    fetchMock.mockResolvedValueOnce(new Response(bytes, { headers: {
      "Content-Disposition": `attachment; filename="server-file.${kind}"`,
      "Content-Type": "application/octet-stream",
    } }));
    await api.download("dataset", kind);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe(kind === "original.json" ? "/api/dataset/dataset/original.json" : kind === "md" ? "/api/dataset/dataset/report.md" : `/api/dataset/dataset/export.${kind}`);
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

  it.each(["csv", "xlsx", "parquet", "md", "original.json"] as const)("uses the intended %s filename if the server omits it", async (kind) => {
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

it("continues after short byte-limited record pages without dropping rows", async () => {
  const item = (id: string) => ({ original: { id, text: id, source_type: "csv" }, processed: null });
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ total: 3, offset: 0, items: [item("1"), item("2")] })));
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ total: 3, offset: 2, items: [item("3")] })));
  expect((await api.allRecords("dataset", 3)).map((r) => r.original.id)).toEqual(["1", "2", "3"]);
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    "/api/dataset/dataset/records?offset=0&limit=1000", "/api/dataset/dataset/records?offset=2&limit=1000",
  ]);
});

it("rejects an empty records page before the reported end instead of looping", async () => {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ total: 3, offset: 0, items: [] })));
  await expect(api.allRecords("dataset", 3)).rejects.toThrow("Empty page");
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("restores a selected local file through the authenticated endpoint", async () => {
  const summary = { dataset_id: "fresh", source_type: "x", query: null, truncated_reason: null, record_count: 500, labelled_count: 0, preview: [], partial: false };
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(summary)));
  const restored = await api.restoreLocal("saved-id");
  const [url, options] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/local-datasets/saved-id/restore");
  expect(options?.method).toBe("POST");
  expect(options?.body).toBeUndefined();
  expect(new Headers(options?.headers).get("Authorization")).toBe("Bearer temporary-test-session");
  expect(restored.dataset_id).toBe("fresh");
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
