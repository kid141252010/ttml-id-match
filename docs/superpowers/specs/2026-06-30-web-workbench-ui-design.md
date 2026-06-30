# Web Workbench UI Design

## Goal

Optimize the Vue web interface for batch TTML metadata matching efficiency across upload, preview, and result review.

## Current Problems

- The current UI already has visual polish, but it spends too much attention on glass effects, gradients, hover motion, and large cards.
- Upload, preview, and result pages feel like separate surfaces instead of one continuous batch workflow.
- Candidate review is the highest-risk step, but the current candidate cards use vertical space heavily and make cross-candidate scanning slower.
- Result review currently prioritizes a success message over failure inspection and per-file follow-up.
- Several components rely on inline styles, which makes layout consistency harder to maintain.

## Chosen Direction

Use a batch workbench layout with restrained tool-focused styling.

The app keeps the existing Vue Router routes and Pinia session store. The shell becomes a stable workbench: a left rail for workflow and progress, a main task area for the active route, and a right/context area when the route needs current-file or write-preview context.

This direction was chosen over a simple visual refresh because the product is a repeated-use metadata tool, not a landing page. It was chosen over a full single-page rewrite because the current route/store structure is serviceable and should not be replaced just to improve layout.

## Layout Requirements

- Keep the three workflow steps: upload, preview, result.
- Use a shared app shell so the user always sees workflow location and recent progress.
- On desktop, prioritize side-by-side scanning:
  - Upload: upload action and file table in the main area, pairing summary in the side area.
  - Preview: pair queue, source switcher, candidate rows, and write preview visible with minimal scrolling.
  - Result: result summary and per-file review table visible before decorative success copy.
- On tablet and mobile, collapse the workbench to a single column with stable ordering: page title, primary content, supporting summaries, progress.
- Avoid nested card-heavy page sections. Cards are acceptable for repeated items and panels, but the page should read as a dense work surface.

## Component Requirements

### App Shell

- Keep the brand, workflow navigation, theme switcher, and progress events.
- Make the sidebar calmer and more compact.
- Show step state with strong text and small status indicators, not large decorative gradients.
- Keep dark mode support.

### Upload

- Reduce the upload zone height so it acts as an entry control instead of dominating the page.
- Show TTML count, audio count, paired count, and unpaired count near the upload controls.
- Render uploaded files as compact rows with type, name, size, and state.
- Keep the `开始预览` action disabled until the store reports `canPreview`.

### Preview

- Render candidate options as dense rows rather than tall cards.
- Each candidate row must expose: selection state, score, title, artists, album, region/market, duration/release date, and id.
- High-confidence candidates should be visually prominent without large badges or animation.
- Source tabs remain available and show candidate counts.
- Keep `全部接受最佳`, `重新预览`, and `写入并生成结果` as the primary batch actions.
- Keep `DiffViewer`, but style it as a compact write preview.

### Result

- Prioritize summary metrics and per-file status over the success illustration.
- Show file, status, metadata written or error, and download action in compact result rows.
- Make failed or skipped files easy to scan.
- Keep ZIP download as the primary action.

## Visual Direction

- Use restrained operational styling: neutral surfaces, clear borders, stable spacing, minimal animation.
- Reduce decorative glow backgrounds and shimmer effects.
- Avoid large gradient text and excessive hover translation.
- Keep the teal accent because it is already established, but add neutral and status colors so the UI is not one-note.
- Maintain readable Chinese text and long filename wrapping.

## Testing Requirements

- Add component tests that mount key views/components with the existing mock API/store setup.
- Verify upload renders a workbench-oriented file summary after upload.
- Verify preview renders source tabs and candidate row content from mock preview data.
- Verify result renders summary metrics and per-file result rows after applying selections.
- Existing store workflow test must continue to pass.

## Out of Scope

- Do not redesign backend APIs.
- Do not add keyboard shortcuts.
- Do not add persistent sorting or search filters.
- Do not replace Naive UI.
- Do not rewrite the workflow into one monolithic route.
