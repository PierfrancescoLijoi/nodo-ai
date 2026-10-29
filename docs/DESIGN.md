# NODO design system

Tokens live in `apps/web/src/app/globals.css` (CSS custom properties, the web interface) and `apps/mobile/src/theme/tokens.ts` (Android); the values are the same. Screens never contain raw colours, font names or user-facing literals.

## Direction

Calm, precise, trust-first. The product handles recordings of people, so the interface never hides recording state or missing evidence. Dark and light themes share one palette logic (system default, user override in Settings).

## Rules

| Area | Rule |
|---|---|
| Accent | One accent only (teal). Warning amber is reserved for legal notice and "not enough evidence". Never use colour alone to carry meaning. |
| Type | Geist for text, Geist Mono for timestamps and the recording timer. Sizes from the `type` scale only. |
| Shape | Cards 16, controls 12, pills for tags only. No other radii. |
| Space | 4-point scale in `space`. Screen gutter 20. Content max width 640 so tablets and web stay readable. |
| Touch | 48 px minimum target, including icon-only buttons (with an accessibility label). |
| Icons | Phosphor only, one weight per role. No emoji, no text glyphs, no hand-drawn icons. |
| Surfaces | Cards only when they group a unit of meaning (summary, action, answer). Otherwise space and rows. |
| Copy | Plain, concrete verbs. No em or en dashes in any locale (enforced by test). |
| Motion | Functional only (skeleton, live level bars). Everything honours the system reduce-motion setting. |
| States | Every async surface has loading, empty and error handling. Unanswerable is a designed state, not an error. |
| Graph | The entity and relation list is primary; the map is secondary and its nodes are real buttons. |

## Contrast

Measured ratios in both themes: body text at least 16:1, muted text at least 5.5:1, text on the accent fill at least 4.5:1 (light) and 9:1 (dark), input borders (`borderStrong`) at least 3:1. Decorative card borders (`border`) are intentionally subtle. Re-check when changing a token.

## Adding a language

1. Copy `src/i18n/locales/en.ts`, translate, add the plural forms the language needs (Polish: one, few, many, other).
2. Register it in `LOCALES` in `src/i18n/index.ts` and add content to `src/data/mock/content.ts`.
3. `npm test` fails until key parity, plural forms and placeholders match English.
4. Legal copy (`capture.notice`, `settings.legalBody`) needs a human review before release.

## Web interface

The web app follows the same rules with the browser's means. Layout: a 248 px sidebar and a content column of at most 920 px on a window; a bottom bar and a 16 px gutter under 860 px. Text and chrome are plain CSS (custom properties, CSS Modules); nothing is styled inline, because the content policy forbids it. Controls are native elements first (button, input, dialog, progress, meter), with the keyboard behaviour of the platform (arrow keys in a segmented control, Escape in a dialog). A skip link leads to the content, focus moves to the page on every route change and never on the first load, and focus rings are 3 px in the accent colour. The map of the graph draws every node as a focusable button. Automated checks (axe, WCAG 2.2 AA rules) run in both themes on every screen, and a test fails on sideways scrolling at 375 px.
