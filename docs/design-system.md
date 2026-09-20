# Design system and accessibility

All visual values live in `frontend/src/styles.css`. Components use Tailwind v4 utilities that
resolve to those tokens (`bg-surface`, `text-muted`, `border-rule`) plus shared control and surface classes.

## Tokens

Light and dark values for every token are declared in the same file, one block after the other,
so a change is always made as a pair. The `@theme` block maps them into Tailwind:

| Group | Tokens | Notes |
|---|---|---|
| Colour | `--color-bg`, `--color-surface`, `--color-surface-2`, `--color-ink`, `--color-muted`, `--color-rule`, `--color-control`, `--color-accent`, `--color-accent-ink`, `--color-accent-soft`, semantic `removed` / `introduced` / `warn` / `error` | accent is the same blue as the author's other tools; dark mode lightens it for contrast |
| Glass | `--glass-bg`, `--glass-border`, `--glass-highlight`, `--glass-shadow` | see below |
| Focus | `--focus-ring` | used by every `:focus-visible` |

## The glass surface

`.glass-panel` and `.glass-bar` use opaque theme backgrounds, with or without backdrop-filter
support. Light is `#f9fafd`; dark is `#161e30`. Borders, highlights and shadows retain depth
without relying on an arbitrary underlay to meet contrast. Decorative rules use `--color-rule`;
interactive control boundaries use the stronger `--color-control`.

## Selected and focus states

`.selectable` is used for **every** choose-one control, without exception: source tabs,
review-mode cards, the posts/% unit toggle, and the four label buttons in the reviewer. The selected state is set by `aria-selected`, `aria-pressed` or `data-selected`
and renders as **three cues together**: accent-soft background, accent border, and a 1px accent
ring (`box-shadow`). That satisfies WCAG 1.4.11 without relying on colour alone, and the default
selection is visible on first paint, not only to assistive tech.

Focus uses a 2px outline with a surface-colored separation. Grid action and progress-strip
outlines are inset so their scroll containers cannot clip them. Selected controls retain their
selection ring. Off-step text stays opaque and uses strikethrough plus checkbox state.

## Verifying contrast

Permanent tests calculate WCAG contrast from the actual source colors. Minimum ratios across
the tested background, surface, secondary surface, glass and selected surfaces:

| Theme | Normal ink | Muted text | Control boundary | Focus color |
|---|---:|---:|---:|---:|
| light | 13.49:1 | 5.18:1 | 3.28:1 | 4.64:1 |
| dark | 10.46:1 | 5.54:1 | 3.46:1 | 5.10:1 |

These calculations and automated checks do not constitute a full manual WCAG audit.

`tools/a11y_audit.py` drives all five stages in both themes (and a 390px phone viewport) and
runs axe-core's WCAG A/AA rules, off-step/fallback checks, and native record-diff keyboard/focus tests. It exits non-zero on any violation. Run it before
shipping and whenever a token changes:

```bash
make dev-api      # terminal 1
make preview      # terminal 2 — builds and serves the production bundle
make audit-a11y   # terminal 3 (needs: pip install playwright && playwright install chromium)
```

The audit runs against the **production bundle**, not the dev server, because that is the artefact
that ships. `make preview` builds it with an explicit API origin exactly as a deployment does.

Lighthouse in Chrome DevTools is a fine second opinion; axe is what the script runs because it can
be automated.

## Data grid

The records table is **AG Grid Community** (`ag-grid-community` + `ag-grid-react`; the enterprise
package is deliberately not installed, so nothing can accidentally depend on it). It uses the
client-side row model: `/dataset/{id}/records` is fetched in full once per run and the grid sorts,
filters, paginates and virtualises rows in the browser. Community features only: sorting,
filtering, pagination, cell rendering, row selection and CSV export of the current view.

The grid is themed with AG Grid's Theming API from the same tokens as everything else
(`themeQuartz.withParams({ foregroundColor: "var(--color-ink)", … })`) plus `colorSchemeLight` /
`colorSchemeDark`, so it follows the theme toggle without a second stylesheet.

One note on the audit: axe's best-practice rule `focus-order-semantics` fires on AG Grid's
roving-tabindex row DOM. That is the standard accessible-grid pattern and not a WCAG requirement,
which is why `tools/a11y_audit.py` runs the WCAG 2.0/2.1 A and AA **tag sets** rather than a
hand-picked rule list. Running the full AA set is stricter overall: it caught an invalid `<p>`
inside a `<dl>` that the narrower list had missed.

## Operator-only UI

Anything that reveals deployment internals (checkpoint paths, whether Comprehend/Bedrock are
enabled, the runtime) renders only when `/health` reports `diagnostics: true`. That is the local
runtime by default; the `DIAGNOSTICS` setting overrides it. The values are not merely hidden with
CSS: the API omits protected fields and requires diagnostics authorization for checkpoint APIs.
