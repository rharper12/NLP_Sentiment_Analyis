// Light/dark theme. Follows the OS until the person chooses; the choice persists in localStorage.

import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";
const KEY = "sentiment-prep.theme";

function systemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Storage throws in private browsing and in webviews with storage disabled; never let that
 *  blank the app, since this runs during the first render. */
function readStored(): Theme | null {
  try {
    const value = localStorage.getItem(KEY);
    return value === "dark" || value === "light" ? value : null;
  } catch {
    return null;
  }
}

function writeStored(theme: Theme): void {
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* preference simply does not persist */
  }
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(
    () => readStored() ?? systemTheme(),
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((t) => {
      const next: Theme = t === "dark" ? "light" : "dark";
      writeStored(next);
      return next;
    });
  }, []);

  return { theme, toggle };
}
