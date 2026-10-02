import { stagesFor, type Stage } from "./Stepper";

/**
 * Sets the document title from the current stage.
 *
 * React 19 hoists `<title>` rendered anywhere in the tree into `<head>`, so this needs no effect
 * and no cleanup. A tab that says "Label · Sentiment Prep" tells someone with several tabs open
 * where they left off, and satisfies WCAG 2.4.2 (Page Titled) for a single-page app whose title
 * would otherwise never change.
 */
export function DocumentTitle({
  stage,
  records,
  consumer = false,
}: {
  stage: Stage;
  records: number | null;
  consumer?: boolean;
}) {
  const label = stagesFor(consumer).find((s) => s.id === stage)?.label ?? "";
  const count = records ? ` (${records.toLocaleString()} posts)` : "";
  return <title>{`${label}${count} · Sentiment Prep`}</title>;
}
