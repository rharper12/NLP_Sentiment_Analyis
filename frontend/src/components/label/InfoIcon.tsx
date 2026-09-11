/** Small informational glyph used beside captions that clarify what is on screen. */
export function InfoIcon() {
  return (
    <svg
      width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      className="mt-0.5 shrink-0" aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5M12 7.5h.01" strokeLinecap="round" />
    </svg>
  );
}
