# Runtime Evaluation

Date: 2026-07-09

## LumenX

Path: `/Users/tml/Desktop/漫剧工厂/external/lumenx`

Status:

- Source is available through sparse checkout after GitHub transfer interruptions.
- README confirms the intended runtime: Python 3.11+, Node.js 18+, FFmpeg.
- Main command: `npm run dev`.
- Backend command: `./start_backend.sh`, expected API at `http://localhost:17177`.
- Frontend command: `cd frontend && npm run dev`, expected UI at `http://localhost:3008`.
- Required key for useful generation: `DASHSCOPE_API_KEY`.

Useful modules:

- `src/apps/comic_gen/pipeline.py`
- `src/apps/comic_gen/api.py`
- `src/apps/comic_gen/models.py`
- `src/apps/comic_gen/audio.py`
- `src/apps/comic_gen/storyboard.py`
- `src/apps/comic_gen/video.py`

Decision:

Use LumenX as the production runtime after the dry-run factory package is stable. Do not block the local MVP on DashScope keys.

## AIComicBuilder

Path: `/Users/tml/Desktop/漫剧工厂/external/AIComicBuilder`

Status:

- Source cloned successfully.
- Runtime: Next.js 16, React 19, SQLite, Drizzle, pnpm, FFmpeg.
- Main command: `pnpm install`, `pnpm drizzle-kit push`, `pnpm dev`.
- API/UI expected at `http://localhost:3000`.

Useful reference files:

- `src/lib/db/schema.ts`
- `src/lib/ai/prompts/script-parse.ts`
- `src/lib/ai/prompts/shot-split.ts`
- `src/lib/ai/prompts/character-extract.ts`
- `src/lib/pipeline/shot-split.ts`
- `src/lib/pipeline/video-assemble.ts`

Decision:

Use AIComicBuilder as a schema and workflow reference first. Do not run it as the primary service until the factory dry-run and LumenX handoff are stable.

## OpenMontage

Path: `/Users/tml/Desktop/洋葱样片/OpenMontage`

Status:

- Existing local virtualenv is usable: Python 3.12.13.
- Capability summary from prior probe:
  - `video_post`: 9 available
  - `subtitle`: 2 available
  - `audio_processing`: 2 available
  - `tts`: 2 available (`piper_tts`, `tts_selector`)
  - `character_animation`: 6 available
  - `image_generation`: 0 available
  - `avatar`: 0 available

Decision:

Use OpenMontage immediately for post-production package design and validation. Actual media rendering comes after the first dry-run package format is proven.
