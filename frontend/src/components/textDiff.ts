export interface DiffPart { text: string; changed: boolean }

const tokens = (text: string) => text.match(/\s+|[\p{L}\p{M}\p{N}_]+|[^\s\p{L}\p{M}\p{N}_]/gu) ?? [];

/** Exact, ordered word/punctuation/whitespace comparison; never normalizes source text. */
export function textDiff(before: string, after: string): { original: DiffPart[]; processed: DiffPart[] } {
  const left = tokens(before), right = tokens(after);
  const original: DiffPart[] = [], processed: DiffPart[] = [];
  const append = (parts: DiffPart[], text: string, changed: boolean) => {
    if (!text) return;
    const previous = parts.at(-1);
    if (previous?.changed === changed) previous.text += text;
    else parts.push({ text, changed });
  };
  let prefix = 0, leftEnd = left.length, rightEnd = right.length;
  while (prefix < leftEnd && prefix < rightEnd && left[prefix] === right[prefix]) prefix++;
  while (leftEnd > prefix && rightEnd > prefix && left[leftEnd - 1] === right[rightEnd - 1]) {
    leftEnd--; rightEnd--;
  }
  append(original, left.slice(0, prefix).join(""), false);
  append(processed, right.slice(0, prefix).join(""), false);
  const a = left.slice(prefix, leftEnd), b = right.slice(prefix, rightEnd);
  // Bound quadratic work for unusually large documents. The fallback highlights the changed
  // middle as a whole while retaining exact text and the shared prefix/suffix.
  const width = b.length + 1, cells = (a.length + 1) * width;
  if (cells > 1_000_000) {
    append(original, a.join(""), true);
    append(processed, b.join(""), true);
  } else {
    const lengths = new Uint32Array(cells);
    for (let i = a.length - 1; i >= 0; i--) {
      for (let j = b.length - 1; j >= 0; j--) {
        lengths[i * width + j] = a[i] === b[j]
          ? 1 + lengths[(i + 1) * width + j + 1]
          : Math.max(lengths[(i + 1) * width + j], lengths[i * width + j + 1]);
      }
    }
    let i = 0, j = 0;
    while (i < a.length && j < b.length) {
      if (a[i] === b[j]) {
        append(original, a[i++], false); append(processed, b[j++], false);
      } else if (lengths[(i + 1) * width + j] >= lengths[i * width + j + 1]) {
        append(original, a[i++], true);
      } else {
        append(processed, b[j++], true);
      }
    }
    append(original, a.slice(i).join(""), true);
    append(processed, b.slice(j).join(""), true);
  }
  append(original, left.slice(leftEnd).join(""), false);
  append(processed, right.slice(rightEnd).join(""), false);
  return { original, processed };
}
