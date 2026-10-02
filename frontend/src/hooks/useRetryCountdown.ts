import { useEffect, useState } from "react";

/** A provider deadline only enables a button; it never starts a request. */
export function useRetryCountdown(retryAt?: number | null) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!retryAt) return;
    const tick = () => {
      const current = Date.now();
      setNow(current);
      if (current >= retryAt * 1000) window.clearInterval(timer);
    };
    const first = window.setTimeout(tick, 0);
    const timer = window.setInterval(tick, 1000);
    return () => { window.clearTimeout(first); window.clearInterval(timer); };
  }, [retryAt]);
  return Math.max(0, Math.ceil(((retryAt ?? 0) * 1000 - now) / 1000));
}
