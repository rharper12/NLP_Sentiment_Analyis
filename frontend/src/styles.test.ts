import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

const css = readFileSync("src/styles.css", "utf8");
const luminance = (hex: string) => {
  const rgb = hex
    .replace("#", "")
    .match(/../g)!
    .map((part) => parseInt(part, 16) / 255)
    .map((value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4));
  return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
};
const contrast = (a: string, b: string) => {
  const [low, high] = [luminance(a), luminance(b)].sort((x, y) => x - y);
  return (high + 0.05) / (low + 0.05);
};

it.each(["light", "dark"])(
  "%s theme guarantees contrast on opaque control and glass surfaces",
  (theme) => {
    const selector = theme === "light" ? ":root {" : ':root[data-theme="dark"] {';
    const block = css.slice(css.indexOf(selector)).split("}")[0];
    const tokens = Object.fromEntries(
      [...block.matchAll(/--t-([\w-]+):\s*(#[a-f\d]{6})/gi)].map((m) => [m[1], m[2]]),
    );
    const surfaces = ["bg", "surface", "surface-2", "glass-bg", "accent-soft"];
    expect(tokens["glass-bg"]).toMatch(/^#[a-f\d]{6}$/i);
    for (const ink of ["ink", "muted", "accent", "removed", "introduced"]) {
      for (const surface of surfaces)
        expect(
          contrast(tokens[ink], tokens[surface]),
          `${ink} on ${surface}`,
        ).toBeGreaterThanOrEqual(4.5);
    }
    for (const ink of ["control", "focus-ring"]) {
      for (const surface of surfaces)
        expect(
          contrast(tokens[ink], tokens[surface]),
          `${ink} on ${surface}`,
        ).toBeGreaterThanOrEqual(3);
    }
    for (const kind of ["accent", "warn", "error"]) {
      expect(
        contrast(tokens[`${kind}-ink`], tokens[kind === "accent" ? kind : `${kind}-bg`]),
      ).toBeGreaterThanOrEqual(4.5);
    }
  },
);

it.each(["light", "dark"])("%s text meets contrast against the page gradient bounds", (theme) => {
  const selector = theme === "light" ? ":root {" : ':root[data-theme="dark"] {';
  const block = css.slice(css.indexOf(selector)).split("}")[0];
  const tokens = Object.fromEntries(
    [...block.matchAll(/--t-([\w-]+):\s*(#[a-f\d]{6})/gi)].map((m) => [m[1], m[2]]),
  );
  const gradient = block.match(/--t-page-gradient:([\s\S]*?);/)![1];
  const colors = [tokens.bg, ...gradient.matchAll(/#[a-f\d]{6}/gi)].map((value) => {
    const hex = typeof value === "string" ? value : value[0];
    return hex
      .slice(1)
      .match(/../g)!
      .map((channel) => parseInt(channel, 16));
  });
  // Every layered gradient blend stays within these per-channel bounds. Use the darkest
  // possible background for the light theme, and the lightest for the dark theme.
  const bound =
    "#" +
    [0, 1, 2]
      .map((channel) => {
        const values = colors.map((color) => color[channel]);
        return (theme === "light" ? Math.min(...values) : Math.max(...values))
          .toString(16)
          .padStart(2, "0");
      })
      .join("");
  for (const ink of ["ink", "muted", "accent"]) {
    expect(
      contrast(tokens[ink], bound),
      `${ink} against gradient bound ${bound}`,
    ).toBeGreaterThanOrEqual(4.5);
  }
});
