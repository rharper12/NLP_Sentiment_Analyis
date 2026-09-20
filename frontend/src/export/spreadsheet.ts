/** Spreadsheet CSV only; raw records and Parquet must keep their original text. */
export function spreadsheetText(value: string): string {
  return /^[\p{White_Space}\p{Cc}\p{Cf}]*[=+\-@]/u.test(value) ? `'${value}` : value;
}
