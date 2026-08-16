# Task 8 Report: Stage Viewers, Video Preflight, and Job Recovery

## Status

Implemented and verified.

## Delivered

- Added a registry-driven `StageViewer` with bounded text/JSON loading, contain-framed image inspection, coordinated audio playback, candidate video inspection controls, structured evaluation rendering, and safe unsupported-file fallback.
- Extended Task 5 artifact descriptors with path-free `kind` and viewer metadata. Audio dialogue timing is projected only when a registered `motion-comic-factory.audio.v1` manifest resolves to the same opaque artifact ID.
- Integrated `StageViewer` into the Task 7 artifact column while preserving the desktop review layout and mobile artifact-before-review order.
- Added `VideoPreflight` against the canonical Task 5 preflight, confirm, test, and generate methods. Confirmation tokens stay in component memory, are invalidated by input/preflight identity changes, and are synchronously consumed once.
- Added a bounded fetch-stream SSE parser with multiline field handling, comments, abort support, and `Last-Event-ID` reconnect headers.
- Added `JobProgress` with persisted-state-first recovery, persisted event cursors, bounded reconnect backoff, five-second GET fallback, StrictMode-safe cleanup, terminal shutdown, and resume-state verification.
- Added desktop/mobile Playwright coverage with API/media interception and deterministic browser media behavior. No provider, backend, paid, `.env`, or external media calls are made.

## Verification

- Focused Vitest: 22 passed.
- All frontend Vitest: 64 passed.
- Backend workbench service/API: 43 passed (one existing Starlette/httpx deprecation warning).
- TypeScript typecheck: passed.
- ESLint: passed.
- Vite production build: passed.
- Playwright desktop/mobile: 2 passed.

## Concerns

- Dialogue controls intentionally appear only for the registered generic audio manifest schema. Unknown or absent timing schemas fall back to honest native media controls.
- Playwright uses the locally installed Chrome channel because the browser CDN download timed out in this environment.
