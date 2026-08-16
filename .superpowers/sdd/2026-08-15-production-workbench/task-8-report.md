# Task 8 Report: Stage Viewers, Video Preflight, and Job Recovery

## Status

Fix round 2 implemented and verified; scoped re-review pending.

## Delivered

- Added a registry-driven `StageViewer` with bounded text/JSON loading, contain-framed image inspection, coordinated audio playback, candidate video inspection controls, structured evaluation rendering, and safe unsupported-file fallback.
- Extended Task 5 artifact descriptors with path-free `kind` and viewer metadata. Audio dialogue timing is projected only when a registered `motion-comic-factory.audio.v1` manifest resolves to the same opaque artifact ID.
- Integrated `StageViewer` into the Task 7 artifact column while preserving the desktop review layout and mobile artifact-before-review order.
- Added `VideoPreflight` against the canonical Task 5 preflight, confirm, test, and generate methods. Confirmation tokens stay in component memory, are invalidated by input/preflight identity changes, and are synchronously consumed once.
- Added a bounded fetch-stream SSE parser with multiline field handling, comments, abort support, and `Last-Event-ID` reconnect headers.
- Added `JobProgress` with persisted-state-first recovery, persisted event cursors, bounded reconnect backoff, five-second GET fallback, StrictMode-safe cleanup, terminal shutdown, and resume-state verification.
- Added desktop/mobile Playwright coverage with API/media interception and deterministic browser media behavior. No provider, backend, paid, `.env`, or external media calls are made.

## Fix Round 1

- Integrated `VideoPreflight` and `JobProgress` into the real video-stage workspace. A new path-free `motion-comic-factory.video-workspace.v1` projection supplies authoritative shots and the newest persisted video job; remounts recover that job without submitting generation again.
- Added authoritative shot selection, one-to-three-shot test gating, and exact canonical Task 5 request submission. Active and failed jobs lock new generation while completed jobs remain inspectable.
- Grouped candidate videos only when descriptors share the same registered `viewer.shot_id`; different shots and ungrouped videos render independently.
- Projected video dialogue windows only when registered storyboard, audio, and video manifests all validate and bind the artifact to a shot. Absolute audio timing is converted to the clip-local window without inventing timing.
- Completed the SSE parser contract: persistent/resettable IDs, NUL rejection, EOF dispatch, CR/LF/CRLF and split UTF-8 handling, bounded lines/events, content-type validation, bounded retry hints, and cancellation during reads. `JobProgress` honors retry hints while retaining five-second persisted GET fallback and cleanup guarantees.
- Routed the current-time video issue action into the existing review form with authoritative shot ID, candidate artifact ID, and exact displayed time prefilled. The existing request-changes API persists it, and success is shown only after the server mutation and canonical reload succeed.
- Hardened text response MIME/length validation and audio rejection/ended/error/unmount ownership cleanup.
- Expanded unit, backend, workspace integration, and desktop/mobile browser tests for every review finding while retaining the verified in-memory one-shot paid token behavior.

## Fix Round 2

- Classified persisted failed video jobs against a newly built canonical Task 5 request for the current approved revisions and authoritative shot selection. Only exact same-revision jobs with a complete provider-task set expose poll-only resume; stale jobs are historical, and pre-submit or partial-task failures require a new one-shot confirmation.
- Kept queued/running jobs as the unconditional generation lock while exposing fresh preflight for historical and new-submission-required failures. Resume revalidates the canonical request before claiming a worker, preventing old outputs from being attached after a project revision changes.
- Preserved the last persisted `JobProgress` snapshot across transient event-triggered and silence-fallback GET failures. Recovery timers re-arm after failed reads, initial read failures expose an explicit retry, and existing StrictMode, abort, and terminal cleanup behavior remains covered.
- Validated every video manifest binding before publishing any shot/dialogue projection. Unknown storyboard shots, duplicate clip bindings, invalid artifacts, or ambiguous mappings now suppress shot metadata honestly.
- Preserved unsent review descriptions and append an idempotent, clearly delimited video timecode block containing the authoritative shot ID, candidate artifact ID, and exact media time.
- Retained the prior path/secret-free descriptor contract and in-memory, one-shot paid confirmation behavior.

## Verification

- Round-2 focused frontend recovery/workspace matrix: 39 passed.
- All frontend Vitest: 83 passed.
- Round-2 adjacent backend workbench/API/jobs/preflight/provider-batch matrix: 188 passed (one existing Starlette/httpx deprecation warning).
- Final strengthened backend recovery check: 2 passed; Ruff and `git diff --check` passed.
- The round-1 full backend pytest result remains 2,871 passed; it was not repeated in round 2 because the focused and adjacent backend matrices cover all changed services.
- TypeScript typecheck: passed.
- ESLint: passed.
- Vite production build: passed.
- Playwright desktop/mobile: 2 passed.

## Concerns

- Unknown, malformed, or unbound timing schemas intentionally fall back to honest native media controls.
- Browser media is deterministic and network-free; Playwright verifies rendered geometry and interactions rather than relying on platform codec decoding.
- The full backend run reports the existing FastAPI TestClient `httpx` deprecation warning; it is unrelated to Task 8 behavior.
- No provider, paid API, `.env`, or external network call was made during round 2.
