# Novel Motion Comic Factory Design

## Goal

Build a local, iterative "novel to voiced motion comic" factory in `/Users/tml/Desktop/漫剧工厂`.
The first useful version should take a short novel excerpt and produce a reviewable episode package:
structured script, character sheet, storyboard, audio plan, OpenMontage compose plan, and eventually an MP4.

## Recommended Architecture

Use LumenX as the main production engine, AIComicBuilder as a reference implementation for character and storyboard structure, and the existing local OpenMontage checkout as a post-production/rendering backend.

The factory project stays small and owns orchestration only. Upstream projects live under `external/` or are referenced by absolute path. This avoids merging three fast-moving codebases into one fragile fork.

## Components

### Factory Control Layer

Path: `factory/`

Responsibilities:

- Normalize user inputs into a project folder under `runs/<project_id>/`.
- Store one canonical JSON package per episode.
- Run adapters for LumenX, AIComicBuilder-inspired planning, and OpenMontage.
- Verify generated files before moving to the next stage.

### Source Repositories

Path: `external/lumenx`, `external/AIComicBuilder`

Responsibilities:

- Provide the real UI/services for LumenX when configured.
- Provide reference prompts, schemas, and pipeline ideas from AIComicBuilder.
- Stay as shallow upstream clones until a specific patch is needed.

### OpenMontage Adapter

Existing path: `/Users/tml/Desktop/洋葱样片/OpenMontage`

Responsibilities:

- Use available FFmpeg, TTS, subtitle, audio, video post, and character animation tools.
- Receive a compose package from the factory.
- Produce preview clips, stitched videos, subtitles, and validation reports.

## Data Flow

1. Novel excerpt enters the factory as plain text.
2. Factory creates `runs/<project_id>/episode.json`.
3. Episode JSON stores:
   - characters
   - scenes
   - shots
   - dialogue
   - narration
   - image/video prompts
   - audio cues
   - OpenMontage compose plan
4. LumenX is used for production when its services and API keys are available.
5. AIComicBuilder is used as a schema/prompt reference, not as a runtime dependency in the first iteration.
6. OpenMontage handles post-production and quality checks.

## First MVP

The first MVP should not depend on paid image/video generation. It should prove the pipeline shape:

- Generate a structured episode package from a short sample text.
- Assign two main characters.
- Create 6 to 10 shots.
- Generate a voice/subtitle plan.
- Create an OpenMontage-ready package.
- Run validation tests locally.

After that works, add real generation through LumenX and available providers.

## Error Handling

Every stage writes a status file into `runs/<project_id>/status.json`.

Failure policy:

- Missing upstream repo: show setup command and continue with fallback reference mode.
- Missing API key: keep dry-run package generation working.
- Missing OpenMontage tool: mark post-production blocked, but preserve all prior artifacts.
- Invalid episode JSON: fail early before calling external tools.

## Testing

Minimum tests:

- Sample novel text creates a valid episode package.
- Episode package has two main characters.
- Shot list has monotonically increasing indexes.
- Every dialogue line references an existing character or narrator.
- OpenMontage package points to valid local output paths.

## Licensing Notes

- Prefer MIT/Apache-2.0 sources for product code.
- Treat CC BY-NC-SA and commercial-restricted repos as references only unless the user explicitly approves that license path.
- OpenMontage is AGPL-3.0, so use it as a local tool/backend and review compliance before productizing a hosted service.
