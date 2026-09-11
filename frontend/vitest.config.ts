import { defineConfig } from "vitest/config";

/** Unit tests for the pure logic and hooks; the end-to-end path is covered by tools/a11y_audit.py. */
export default defineConfig({
  test: { environment: "jsdom", globals: true, include: ["src/**/*.test.{ts,tsx}"] },
});
