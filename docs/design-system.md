# Design system and accessibility

All visual values live in `frontend/src/styles.css`. Components use Tailwind v4 utilities that
resolve to those tokens (`bg-surface`, `text-muted`, `border-rule`) plus two component classes.
Nothing else in the codebase hard-codes a colour, blur, shadow or radius.

## Tokens

Light and dark values for every token are declared in the same file, one block after the other,
so a change is always made as a pair. The `@theme` block maps them into Tailwind:

| Group | Tokens | Notes |
|---|---|---|
| Colour | `--color-bg`, `--color-surface`, `--color-surface-2`, `--color-ink`, `--color-muted`, `--color-rule`, `--color-accent`, `--color-accent-ink`, `--color-accent-soft`, semantic `removed` / `introduced` / `warn` / `error` | accent is the same blue as the author's other tools; dark mode lightens it for contrast |
| Glass | `--glass-bg`, `--glass-bg-fallback`, `--glass-border`, `--glass-highlight`, `--glass-blur`, `--glass-shadow` | see below |
| Focus | `--focus-ring` | used by every `:focus-visible` |

## The glass surface

`.glass-panel` is the only glass class. It sets an **opaque fallback background first**
(`--glass-bg-fallback`, the colour the translucent surface renders to over the page gradient),
then inside `@supports (backdrop-filter: …)` switches to the translucent `--glass-bg` with
`backdrop-filter: blur(var(--glass-blur)) saturate(160%)`. A 1px `--glass-border`, an inset
1px `--glass-highlight` for the top edge, a three-layer `--glass-shadow` and 16px radius complete
it. `.glass-bar` is the same recipe for the header and stepper, without radius or drop shadow.

Because components only ever use the class, changing the look is a token edit, and browsers
without `backdrop-filter` get a solid, legible surface instead of text over the page gradient.

## Selected and focus states

`.selectable` is used for **every** choose-one control, without exception: source tabs,
review-mode cards, the posts/% unit toggle, and the four label buttons in the reviewer. The selected state is set by `aria-selected`, `aria-pressed` or `data-selected`
and renders as **three cues together**: accent-soft background, accent border, and a 1px accent
ring (`box-shadow`). That satisfies WCAG 1.4.11 without relying on colour alone, and the default
selection is visible on first paint, not only to assistive tech.

Focus is a `box-shadow` ring (`2px surface + 2px --focus-ring`) on every focusable element; the
native outline is removed only because the ring replaces it. On a selected control the focus ring
stacks outside the selection ring so both remain visible (2.4.7).

## Verifying contrast

Contrast is measured against the **rendered** glass surface, not the token's raw alpha colour:

| Theme | Rendered glass | ink | muted | accent |
|---|---|---|---|---|
| light | ≈ `#f9fafd` | 14.9:1 | 5.9:1 | 5.3:1 |
| dark | ≈ `#161e30` | 14.6:1 | 7.3:1 | 6.9:1 |

`tools/a11y_audit.py` drives all five stages in both themes (and a 390px phone viewport) and
runs axe-core's colour-contrast and ARIA rules. It exits non-zero on any violation. Run it before
shipping and whenever a token changes:

```bash
make local-api                     # terminal 1
cd frontend && npm run build && npx vite preview --port 5173   # terminal 2
pip install playwright && playwright install chromium
python tools/a11y_audit.py         # terminal 3
```

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
CSS: the API returns them as `null`, so a deployed build never has them to render.
