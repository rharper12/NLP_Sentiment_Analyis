/** Constants and helpers shared by the Label stage's panels. */

import type { SentimentLabel } from "../../api/types";

/** Posts sent to Comprehend per request; each slice is saved before the next begins. */
export const SLICE = 250;
/** Reviewer decisions buffered before a save, so a closed tab loses at most this many. */
export const FLUSH_EVERY = 10;
/** Review records fetched per page. */
export const REVIEW_PAGE = 200;
export const PRICING_URL = "https://aws.amazon.com/comprehend/pricing/";

/** Keyboard shortcuts: digits match the on-screen order, letters are mnemonics. */
export const KEYS: { [key: string]: SentimentLabel } = {
  "1": "positive", p: "positive",
  "2": "negative", n: "negative",
  "3": "neutral", u: "neutral",
  "4": "mixed", m: "mixed",
};

/** Money for display. Four decimals below a cent so tiny estimates do not read as $0.00. */
export const usd = (n: number) => `$${n.toFixed(n < 0.01 && n > 0 ? 4 : 2)}`;
