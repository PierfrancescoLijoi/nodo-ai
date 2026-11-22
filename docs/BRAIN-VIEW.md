# Knowledge Brain view

`/brain` is a desktop-first interactive 2D knowledge graph. It deliberately avoids anatomical or
generative brain imagery: the visual language is a calm navy technical canvas with small typed
nodes, fine evidence-backed edges and stable category clusters.

## Interaction

- Drag pans, wheel or pinch zooms, click selects and double click focuses a neighbourhood.
- Search focuses the first matching concept without changing stable coordinates.
- Type, relation and review-status filters affect only the visible view.
- Selecting a node fades non-neighbours and opens the evidence-oriented inspector.
- Proposed relations are warm and dashed; approved relations are cool and solid.
- Keyboard users navigate nodes with arrow keys and select with Enter or Space.
- Reduced motion removes the selection pulse. Mobile defaults to the accessible list view.

## Rendering and data

The Canvas renderer is client-only and linear in visible nodes plus edges. It caps device pixel
ratio at two, redraws on interaction and resize, and animates only the selected-node pulse. The
existing graph API remains the source of truth; expansion merges bounded neighbourhoods without
inventing data. The repository adapter preserves relation status, confidence and source references.

## Verification

Unit tests cover layout stability, filtering, camera math and repository mapping. The dedicated
Playwright journey checks desktop selection and focus, WCAG AA, reduced motion, mobile overflow and
the empty state. Production build, TypeScript and lint are required before release.
