// One in-flight async action with cancellation. Obsolete runs resolve to null so callers
// cannot apply their results to configuration, navigation or downstream versions. `run` hands the task an AbortSignal; calling
// `run` again or `cancel` aborts the previous request. Aborts are not reported as errors.

import { useCallback, useEffect, useRef, useState } from "react";

import { isAbort } from "../api/client";

interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: Error | null;
}

/** Normalise anything thrown into an Error, so callers can always read `.message`. */
export function toError(value: unknown): Error {
  return value instanceof Error ? value : new Error(String(value));
}

export function useAsync<T>() {
  const [state, setState] = useState<AsyncState<T>>({ data: null, loading: false, error: null });
  const controller = useRef<AbortController | null>(null);

  const mounted = useRef(true);
  const invalidate = useCallback(() => {
    const previous = controller.current;
    controller.current = null;
    previous?.abort();
  }, []);

  const cancel = useCallback(() => {
    invalidate();
    setState((s) => ({ ...s, loading: false }));
  }, [invalidate]);

  const run = useCallback(async (task: (signal: AbortSignal) => Promise<T>): Promise<T | null> => {
    if (!mounted.current) return null;
    invalidate();
    const own = new AbortController();
    controller.current = own;
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await task(own.signal);
      if (controller.current !== own || own.signal.aborted) return null;
      setState({ data, loading: false, error: null });
      return data;
    } catch (error) {
      // `catch` gives `unknown`; a thrown non-Error (a string, a rejected value) must not become
      // a fake Error whose `.message` is undefined at render time.
      if (controller.current === own && !own.signal.aborted && !isAbort(error))
        setState((s) => ({ ...s, loading: false, error: toError(error) }));
      return null;
    }
  }, [invalidate]);

  const reset = useCallback(() => {
    invalidate();
    setState({ data: null, loading: false, error: null });
  }, [invalidate]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; invalidate(); };
  }, [invalidate]);

  return { ...state, run, cancel, reset };
}
