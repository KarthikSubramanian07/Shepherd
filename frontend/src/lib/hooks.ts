"use client";

import { useEffect, useState } from "react";

export interface AsyncState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  setData: (d: T) => void;
}

/** Minimal data-fetching hook for client pages. Swap for SWR/React Query later. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fn()
      .then((d) => {
        if (active) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => active && setError(e as Error))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading, setData };
}
