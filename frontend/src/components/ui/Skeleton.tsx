/** Placeholder block matching the shape of the content it stands in for. */
export function Skeleton({ className = "" }: { className?: string }) {
  return <span className={`skeleton h-4 ${className.includes("w-") ? "" : "w-full"} ${className}`} aria-hidden="true" />;
}

export function SkeletonLines({ lines = 3 }: { lines?: number }) {
  return (
    <div className="flex flex-col gap-2.5" role="group" aria-busy="true" aria-label="Loading">
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={i === lines - 1 ? "w-3/5" : "w-full"} />
      ))}
    </div>
  );
}

/** Keep the app's header and main content visible while startup checks finish. */
export function AppSkeleton() {
  return (
    <div className="min-h-screen bg-bg text-ink">
      <header className="glass-bar border-b border-rule px-6 py-5 font-semibold">Sentiment Prep</header>
      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6" aria-busy="true">
        <p role="status" className="mb-5 text-sm text-muted">Loading Sentiment Prep…</p>
        <div aria-hidden="true" className="flex flex-col gap-6">
          <div className="flex gap-4">{Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-8 flex-1" />)}</div>
          <div className="glass-panel flex flex-col gap-5 p-6 sm:p-8">
            <Skeleton className="h-8 w-2/3" /><Skeleton className="w-4/5" />
            <div className="grid gap-4 sm:grid-cols-2"><Skeleton className="h-12" /><Skeleton className="h-12" /></div>
            <Skeleton className="h-32" /><Skeleton className="h-11 w-40" />
          </div>
        </div>
      </main>
    </div>
  );
}
