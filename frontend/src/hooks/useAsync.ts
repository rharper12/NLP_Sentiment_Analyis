// One in-flight async action with cancellation. `run` hands the task an AbortSignal; calling
// `run` again or `cancel` aborts the previous request. Aborts are not reported as errors.

import { useCallback, useEffect, useRef, useState } from "react";

import { isAbort } from "../api/client";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: Error | null;
}

export function useAsync<T>() {
  const [state, setState] = useState<AsyncState<T>>({ data: null, loading: false, error: null });
  const controller = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
    setState((s) => ({ ...s, loading: false }));
  }, []);

  const run = useCallback(async (task: (signal: AbortSignal) => Promise<T>): Promise<T | null> => {
    controller.current?.abort();
    const own = new AbortController();
    controller.current = own;
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await task(own.signal);
      if (controller.current === own) setState({ data, loading: false, error: null });
      return data;
    } catch (error) {
      if (controller.current === own && !isAbort(error))
        setState((s) => ({ ...s, loading: false, error: error as Error }));
      return null;
    }
  }, []);

  const reset = useCallback(() => {
    controller.current?.abort();
    setState({ data: null, loading: false, error: null });
  }, []);

  useEffect(() => () => controller.current?.abort(), []);

  return { ...state, run, cancel, reset };
}
