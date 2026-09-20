import { act, renderHook } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { useAsync } from "./useAsync";

function deferred() {
  let resolve!: (value: string) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<string>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

it.each(["reset", "cancel", "unmount"])("invalidates success and caller follow-up after %s", async (action) => {
  const task = deferred(), follow = vi.fn();
  const { result, unmount } = renderHook(() => useAsync<string>());
  let operation!: Promise<string | null>;
  let signal!: AbortSignal;
  act(() => { operation = result.current.run((s) => { signal = s; return task.promise; }); });
  const completion = operation.then((value) => { if (value) follow(value); });
  act(() => { if (action === "unmount") unmount(); else if (action === "reset") result.current.reset(); else result.current.cancel(); });
  expect(signal.aborted).toBe(true);
  await act(async () => { task.resolve("obsolete"); await completion; });
  expect(await operation).toBeNull();
  expect(follow).not.toHaveBeenCalled();
  expect(result.current.data).toBeNull();
});

it("ignores rejection after reset", async () => {
  const task = deferred();
  const { result } = renderHook(() => useAsync<string>());
  let operation!: Promise<string | null>;
  act(() => { operation = result.current.run(() => task.promise); });
  act(() => result.current.reset());
  await act(async () => { task.reject(new Error("obsolete")); await operation; });
  expect(result.current).toMatchObject({ data: null, error: null, loading: false });
});

it.each([true, false])("only accepts the newest request, newer completes first: %s", async (newFirst) => {
  const old = deferred(), current = deferred(), follow = vi.fn();
  const { result } = renderHook(() => useAsync<string>());
  let a!: Promise<string | null>, b!: Promise<string | null>;
  act(() => { a = result.current.run(() => old.promise); });
  act(() => { b = result.current.run(() => current.promise); });
  const done = a.then((value) => { if (value) follow(value); });
  await act(async () => { if (newFirst) current.resolve("current"); else old.resolve("old"); });
  if (!newFirst) expect(result.current.loading).toBe(true);
  await act(async () => { if (newFirst) old.resolve("old"); else current.resolve("current"); await Promise.all([done, b]); });
  expect(result.current.data).toBe("current");
  expect(await a).toBeNull();
  expect(follow).not.toHaveBeenCalled();
});

it("supports rapid cancel and restart without a late error changing the new result", async () => {
  const old = deferred();
  const { result } = renderHook(() => useAsync<string>());
  let first!: Promise<string | null>;
  act(() => { first = result.current.run(() => old.promise); result.current.cancel(); });
  await act(async () => { await result.current.run(async () => "new"); });
  await act(async () => { old.reject(new Error("late")); await first; });
  expect(result.current).toMatchObject({ data: "new", loading: false, error: null });
});
