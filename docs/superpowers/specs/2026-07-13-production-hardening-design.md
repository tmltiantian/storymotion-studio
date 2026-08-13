# Motion Comic Factory Production Hardening Design

## Goal

Turn the current sample-oriented factory into a restart-safe local production
system that can accept arbitrary novel jobs, preserve paid generation work,
report the real gateway state, and publish only validated media.

## Architecture

The factory remains the orchestration owner. Paid providers expose explicit
submit and resume operations, while the factory persists one signed state file
per generated asset before waiting for completion. A deterministic signature
binds provider, model or voice, text or prompt, and relevant source assets.
Completed matching assets are reused; ambiguous `submitting` states fail closed
instead of issuing another billable request.

Media writers render to a sibling temporary path. `ffprobe` verifies the
required stream and duration before an atomic `Path.replace` publishes the
artifact. Existing good outputs survive any failed render or validation.

The control plane treats either a successful LumenX generation report or a
successful gateway video batch as real visual generation. Commands that change
generation or preview state refresh readiness, operator handoff, and dashboard
artifacts together.

A generic job manifest and local worker provide the unattended entry point.
The worker claims queued jobs atomically, runs one project at a time, and writes
terminal job state without embedding credentials. The existing sample script
becomes a thin wrapper around the generic command.

## TTS Contract

- Add optional `DOUBAO_SPEECH_VOICE_MAP` JSON keyed by narrator ID, character
  ID, character name, or one-based role position (`character_1`,
  `character_2`).
- Keep `DOUBAO_SPEECH_VOICE_TYPE` as the default voice.
- Persist per-cue signature, request ID, task ID, provider status, selected
  voice ID, output path, and sanitized error.
- Reuse only a matching completed state with a valid audio stream.
- A cloud failure may use local fallback only when fallback is enabled; reports
  must identify `doubao`, `local`, or `mixed` from actual clip counts.
- A configured but incomplete multi-role voice map is visible as a warning; it
  is never reported as successful role differentiation.

## Readiness Contract

- Gateway generation passes only when the current report is successful and all
  planned jobs are completed or validly skipped.
- Preview readiness reads the hybrid preview and provider report when present.
- Environment readiness is computed from current configuration rather than a
  stale report file.
- Every state-changing command refreshes the derived control-plane artifacts.

## Media Contract

- MP4 validation requires at least one decodable video stream.
- Audio validation requires at least one decodable audio stream.
- Final preview, hybrid preview, muxed preview, and downloaded provider media
  are published atomically.
- Validation errors are converted into concise CLI reports without traceback or
  deletion of the last good artifact.

## Job Service Contract

- `factory_cli.py run-project` accepts input path, project ID, title, shot count,
  and optional reviewed-character manifest.
- `factory_cli.py enqueue` writes a queued job manifest.
- `factory_cli.py worker` processes queued jobs once or continuously with a
  configurable poll interval.
- A repository-local virtual environment and pinned dependency manifest replace
  hard-coded dependence on another project's Python executable.
- No live paid generation runs in tests. End-to-end tests use fakes and real
  FFmpeg/ffprobe only for local media.

## Acceptance

1. Re-running an unchanged TTS job performs zero new provider submissions.
2. A submitted TTS task resumes from its task ID after interruption.
3. Narrator and configured characters receive the expected cloud voice IDs.
4. Reports never label an all-local or mixed render as pure Doubao.
5. A successful gateway batch removes the obsolete real-generation blocker.
6. A failed caption render or FFmpeg fallback preserves the previous final MP4.
7. A box-only MP4 with no streams is rejected and falls back to a shot card.
8. An arbitrary novel can be queued and processed by the local worker.
9. Full tests, compile checks, and an end-to-end sample pass from the repository
   environment.
