/** Placeholder block matching the shape of the content it stands in for. */
export function Skeleton({ className = "" }: { className?: string }) {
  return <span className={`skeleton h-4 ${className.includes("w-") ? "" : "w-full"} ${className}`} aria-hidden="true" />;
}

export function SkeletonLines({ lines = 3 }: { lines?: number }) {
  return (
    <div className="flex flex-col gap-2.5" aria-busy="true" aria-label="Loading">
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={i === lines - 1 ? "w-3/5" : "w-full"} />
      ))}
    </div>
  );
}
