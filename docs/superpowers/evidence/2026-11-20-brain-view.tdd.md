# Brain view TDD evidence

## Source and journeys

The journeys were derived from the Brain view request in this session.

- A user explores the whole knowledge graph, filters it and focuses a concept without losing context.
- A keyboard or screen reader user reaches the same concepts and details through a list or roving map controls.
- A motion-sensitive user receives a static, instant version of the same information.
- A user opens meeting evidence or the review inbox from a concept connection.

## RED and GREEN

The RED command was:

```text
npx.cmd vitest run src/features/brain/brain.test.ts src/features/brain/BrainExplorer.test.tsx src/lib/api/repository.test.ts
```

It exited 1. The new suites could not resolve the missing Brain modules, and the repository assertion received `depth=1&limit=40` instead of `depth=2&limit=200`.

The focused GREEN command added the locale parity suite and exited 0 with 32 passing tests:

```text
npx.cmd vitest run src/features/brain/brain.test.ts src/features/brain/BrainExplorer.test.tsx src/lib/api/repository.test.ts src/lib/i18n/locales.test.ts
```

The full Vitest command later exited 0 with 197 passing tests across 23 files.

## Test specification

| Guarantee                                                                 | Evidence                                    | Type       | Result |
| ------------------------------------------------------------------------- | ------------------------------------------- | ---------- | ------ |
| Layout is deterministic, viewport bounded and valid at 2,000 nodes        | `src/features/brain/brain.test.ts`          | Unit       | PASS   |
| Camera zoom preserves the pointer world position and clamps scale         | `src/features/brain/brain.test.ts`          | Unit       | PASS   |
| Search, type, relation and review filters retain complete visible edges   | `src/features/brain/brain.test.ts`          | Unit       | PASS   |
| Focus expansions merge without duplicate nodes or relations               | `src/features/brain/brain.test.ts`          | Unit       | PASS   |
| The list opens details and meeting evidence                               | `src/features/brain/BrainExplorer.test.tsx` | Component  | PASS   |
| Arrow keys, Enter and Escape operate map nodes and details                | `src/features/brain/BrainExplorer.test.tsx` | Component  | PASS   |
| Mobile defaults to the list and reduced motion disables selection pulses  | `src/features/brain/BrainExplorer.test.tsx` | Component  | PASS   |
| The browser focus flow is axe clean and phone width has no overflow       | `e2e/local.brain.spec.ts`                   | End to end | PASS   |

## Coverage and known gaps

`npx.cmd vitest run --coverage` exited 0 with 85.53% statements, 82.55% functions and 87.25% lines overall. Brain logic reached 96.42% statements, 100% functions and 95.89% lines.

The Canvas drawing result is validated in a real Chrome run rather than by mocking pixel calls in jsdom. `playwright.brain.config.ts` serves the sealed static export and the spec intercepts only graph API calls, keeping the browser journey deterministic and independent of an API process.

The final implementation intentionally renders a sparse 2D knowledge graph. It contains no anatomical scaffold, generated neurons or perspective projection.
