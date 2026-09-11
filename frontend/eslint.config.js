// Flat config. The react-hooks rules are the point of this file: exhaustive-deps and the rules of
// hooks are what catch effect bugs the type checker cannot see. jsx-a11y complements the axe run
// in tools/a11y_audit.py by catching accessibility mistakes at edit time rather than at review.
import js from "@eslint/js";
import a11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import ts from "typescript-eslint";

export default [
  { ignores: ["dist/**", "node_modules/**", "src/api/schema.d.ts"] },
  js.configs.recommended,
  ...ts.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { "jsx-a11y": a11y, "react-hooks": reactHooks },
    languageOptions: {
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { window: "readonly", document: "readonly", localStorage: "readonly",
                 navigator: "readonly", console: "readonly", fetch: "readonly",
                 setTimeout: "readonly", clearTimeout: "readonly", AbortController: "readonly",
                 DOMException: "readonly", FormData: "readonly", URLSearchParams: "readonly",
                 KeyboardEvent: "readonly", HTMLDialogElement: "readonly", HTMLInputElement: "readonly" },
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      ...a11y.flatConfigs.recommended.rules,
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "@typescript-eslint/no-explicit-any": "error",
    },
  },
];
