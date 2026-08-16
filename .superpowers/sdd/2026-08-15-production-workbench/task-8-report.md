# Task 8 Report: Stage Viewers, Video Preflight, and Job Recovery

## Status

Fix round 4 implemented and verified; scoped re-review pending.

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

## Fix Round 3

- Reloaded the authoritative video workspace whenever a tracked active job becomes terminal. Shot selection and generation controls remain locked in a neutral classification state until the server returns `poll_only`, `new_submission_required`, or historical recovery data; classification errors expose an explicit retry without enabling paid submission.
- Changed compact resume acknowledgements to restart `JobProgress` recovery immediately. The last failed snapshot remains visible with a recovering status while SSE and the five-second persisted GET fallback retry from the saved event cursor; a transient first read no longer invites a duplicate resume call.
- Made failed-job resume validation atomic with project ownership. Resume now claims the worker lease and uses `JobManager.resume()` to reserve the project, rereads the current job, and rebuilds the canonical request while ownership is held before dispatch. A changed revision transitions the recovery back to failed, releases worker/project ownership, and never reaches the renderer.
- Added deterministic tests for both live failure classifications, compact-resume read recovery, and a reserved review mutation completing between preliminary and final canonical comparisons.

## Fix Round 4

- Centralized every post-claim, pre-dispatch resume failure through terminal job cleanup. Expected canonical mismatches and unexpected final reread exceptions now fail the queued recovery and release project ownership before the original exception is re-raised.
- Added an independent job-store cleanup retry for interrupted failure persistence. If both cleanup paths fail, the worker lease is still released and the raised error explicitly warns that the project may remain reserved while retaining the original validation exception as its cause.
- Added deterministic fault injection for a successful preliminary comparison followed by a second `_video_job_recovery()` `OSError`, proving zero renderer calls, failed job state, worker release, and immediate success of the next reserved mutation. A separate cleanup-failure test covers the explicit reservation-risk path.

## Verification

- Round-3 focused frontend recovery/workspace matrix: 42 passed.
- All frontend Vitest: 86 passed.
- Round-4 focused failed-video/resume matrix: 11 passed.
- Round-4 adjacent backend workbench/API/jobs/preflight/provider-batch matrix: 191 passed (one existing Starlette/httpx deprecation warning).
- Ruff and `git diff --check`: passed.
- The round-1 full backend pytest result remains 2,871 passed; it was not repeated in later rounds because the focused and adjacent backend matrices cover all changed services.
- TypeScript typecheck: passed.
- ESLint: passed.
- Vite production build: passed.
- Playwright desktop/mobile: 2 passed.

## Concerns

- Unknown, malformed, or unbound timing schemas intentionally fall back to honest native media controls.
- Browser media is deterministic and network-free; Playwright verifies rendered geometry and interactions rather than relying on platform codec decoding.
- The full backend run reports the existing FastAPI TestClient `httpx` deprecation warning; it is unrelated to Task 8 behavior.
- No provider, paid API, `.env`, or external network call was made during round 2.
- No provider, paid API, `.env`, or external network call was made during round 3.
- Round 4 changed no frontend files, so the verified round-3 frontend, build, and Playwright results were not rerun.
- No provider, paid API, `.env`, or external network call was made during round 4.
