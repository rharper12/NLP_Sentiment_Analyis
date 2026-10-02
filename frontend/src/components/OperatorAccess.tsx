import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import { api, AUTH_REQUIRED, isAbort } from "../api/client";
import { toError } from "../hooks/useAsync";
import { Notice } from "./ui/Notice";
import { AppSkeleton } from "./ui/Skeleton";

/** An operator signs in once; only the temporary session survives in browser memory. */
export function OperatorAccess({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [required, setRequired] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const expired = () => setRequired(true);
    window.addEventListener(AUTH_REQUIRED, expired);
    void api.health(controller.signal).then((health) => {
      setRequired(health.auth_required);
      setReady(!health.auth_required);
    }).catch((e: unknown) => { if (!isAbort(e)) setError(toError(e)); });
    return () => { controller.abort(); window.removeEventListener(AUTH_REQUIRED, expired); };
  }, []);

  const login = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const input = form.elements.namedItem("key");
    if (!(input instanceof HTMLInputElement)) return;
    const key = input.value;
    form.reset();
    setBusy(true);
    setError(null);
    try { await api.login(key); setRequired(false); setReady(true); }
    catch (e) { setError(toError(e)); }
    finally { setBusy(false); }
  };

  if (!ready && !required && !error) return <AppSkeleton />;

  return <>
    {ready && <div inert={required}>{children}</div>}
    {(!ready || required) && <div className="fixed inset-0 z-50 flex items-center justify-center bg-surface p-6">
      <section className="glass-panel w-full max-w-md p-6" aria-label="Operator access">
        <h1 className="text-xl font-semibold">Sentiment Prep</h1>
        {required ? <form onSubmit={(e) => void login(e)} className="mt-4 flex flex-col gap-4">
          <p>Sign in with your operator key. Sessions expire after one hour.</p>
          <label htmlFor="operator-key">Operator key</label>
          <input id="operator-key" name="key" type="password" autoComplete="off" required disabled={busy} />
          <button className="btn-primary" disabled={busy} type="submit">{busy ? "Signing in…" : "Sign in"}</button>
        </form> : <p className="mt-4">{error ? "Unable to connect. Reload to try again." : "Connecting…"}</p>}
        <Notice error={error} />
      </section>
    </div>}
  </>;
}
