import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { expect, it } from "vitest";

it("never includes a sentinel permanent credential in built public assets", () => {
  const output = mkdtempSync(join(tmpdir(), "sentiment-prep-assets-"));
  const sentinel = "TEST_ONLY_PERMANENT_SECRET_SENTINEL_594ac8d2";
  try {
    execFileSync(process.execPath, [resolve("node_modules/vite/bin/vite.js"), "build", "--outDir", output, "--emptyOutDir"], {
      cwd: process.cwd(),
      env: { ...process.env, API_KEY: sentinel, VITE_API_KEY: sentinel },
      stdio: "pipe",
    });
    const files = readdirSync(output, { recursive: true, withFileTypes: true }).filter((file) => file.isFile());
    expect(files.some((file) => file.name.endsWith(".js"))).toBe(true);
    for (const file of files) {
      expect(readFileSync(join(file.parentPath, file.name), "utf8")).not.toContain(sentinel);
    }
  } finally {
    rmSync(output, { recursive: true, force: true });
  }
}, 30000);
