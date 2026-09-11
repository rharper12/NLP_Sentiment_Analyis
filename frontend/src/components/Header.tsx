import { SpendChip } from "./SpendChip";

interface Props {
  theme: "light" | "dark";
  onToggleTheme: () => void;
  onOpenHistory: () => void;
  spendVersion: number;
}

export function Header({ theme, onToggleTheme, onOpenHistory, spendVersion }: Props) {
  const next = theme === "dark" ? "light" : "dark";
  return (
    <header className="glass-bar sticky top-0 z-30 border-b border-rule">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <div className="flex items-baseline gap-3">
          <h1 className="text-lg font-semibold tracking-tight">Sentiment Prep</h1>
          <p className="hidden text-sm text-muted lg:block">Collect posts on any topic, clean them for NLP, measure what changed.</p>
        </div>
        <div className="flex items-center gap-2">
          <SpendChip refreshKey={spendVersion} />
          <button type="button" className="btn hidden sm:inline-flex" onClick={onOpenHistory}>History</button>
          <button type="button" onClick={onToggleTheme} aria-label={`Switch to ${next} mode`} title={`Switch to ${next} mode`}
            className="grid size-9 place-items-center rounded-full border border-rule bg-surface hover:bg-surface-2">
            {theme === "dark" ? (
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>
            ) : (
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>
            )}
          </button>
        </div>
      </div>
    </header>
  );
}
