# Iteration Log

## 2026-07-09 — v0.1 Dry-Run Package

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py plan --input samples/sample_novel.txt --project sample_episode --title 旧城来信 --shots 8
```

Result:

- `5 passed`.
- Generated `runs/sample_episode/episode.json`.
- Generated `runs/sample_episode/openmontage_package.json`.
- Generated `runs/sample_episode/status.json`.

Finding:

- The first planner version over-merged short text into one shot.
- Root cause: `split_story_beats` grouped sentences by character count even when sentence count was already below target shot count.
- Fix: return sentence-level beats when `len(raw_parts) <= target_count`.

Next:

- Add a startup script.
- Add LumenX service boot check.
- Add OpenMontage package validator.

## 2026-07-09 — v0.2 Runtime Probe and Handoff Validation

Command:

```bash
scripts/start_factory.sh
```

Result:

- `7 passed`.
- Generated `runs/runtime_probe.json`.
- Regenerated `runs/sample_episode/episode.json`.
- Regenerated `runs/sample_episode/openmontage_package.json`.

Added:

- Static runtime probe for LumenX, AIComicBuilder, and OpenMontage.
- OpenMontage package validator.
- Startup script now runs tests, runtime probe, then sample planning.

Next:

- Add a minimal preview artifact from the episode package.
- Add real OpenMontage execution once the package contract is stable.

## 2026-07-09 — v0.3 Preview Artifacts and Dialogue Extraction

Command:

```bash
scripts/start_factory.sh
```

Result:

- `10 passed`.
- Generated `runs/sample_episode/storyboard_preview.md`.
- Generated `runs/sample_episode/subtitles.srt`.
- Subtitle line 4 now uses the novel's spoken line: `最后一班车不是开往城外，而是开往十年前`.

Added:

- Storyboard Markdown preview.
- SRT subtitle generation.
- Episode snapshot JSON.
- Basic spoken-dialogue extraction from `说，...` text.

Next:

- Create a placeholder visual preview video or OpenMontage render plan executor.
- Replace fallback repeated dialogue with smarter scene-specific reactions.

## 2026-07-09 — v0.4 Placeholder Preview MP4

Command:

```bash
scripts/start_factory.sh
ffprobe -v error -show_entries format=duration -show_streams -of json runs/sample_episode/placeholder_preview.mp4
```

Result:

- `12 passed`.
- Generated `runs/sample_episode/placeholder_preview.mp4`.
- ffprobe confirmed:
  - 1080x1920 H.264 video
  - 30 fps
  - 45.0 seconds
  - AAC stereo audio track
  - mov_text subtitle track

Added:

- Placeholder FFmpeg renderer.
- Runtime duration calculation from shot durations.
- MP4 preview generation during `factory_cli.py plan`.

Next:

- Burn subtitles visually for social-preview compatibility.
- Replace placeholder color video with per-shot still cards or OpenMontage-rendered scenes.

## 2026-07-09 — v0.5 Per-Shot Storyboard Card Preview

Command:

```bash
scripts/start_factory.sh
ffprobe -v error -show_entries format=duration -show_streams -of json runs/sample_episode/card_preview.mp4
```

Result:

- `15 passed`.
- Generated `runs/sample_episode/cards/shot_001.png` through `shot_006.png`.
- Generated `runs/sample_episode/cards/cards.ffconcat`.
- Generated `runs/sample_episode/card_preview.mp4`.
- ffprobe confirmed:
  - 1080x1920 H.264 video
  - 30 fps
  - 45.0 seconds
  - AAC stereo audio track
  - mov_text subtitle track
- Visual inspection of `shot_001.png` confirmed Chinese text is readable and layout stays inside the card.

Added:

- Pillow-based storyboard card renderer.
- FFmpeg concat manifest for per-shot card timing.
- Card-preview MP4 generation during `factory_cli.py plan`.

Next:

- Add LumenX handoff/export JSON so the dry-run package can be consumed by the production pipeline.
- Replace static cards with generated first frames once image generation is configured.

## 2026-07-09 — v0.6 LumenX Handoff Contract

Command:

```bash
scripts/start_factory.sh
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
```

Result:

- `19 passed`.
- Generated `runs/sample_episode/lumenx_handoff.json`.
- Handoff includes:
  - `create_project`: `POST /projects?skip_analysis=true`
  - `add_characters`: 2 requests
  - `add_scenes`: 1 request
  - `add_frames`: 6 requests
  - `update_frames`: 6 requests with `requires_frame_index` mapping hints
- Verified the handoff preserves characters, frame prompts, dialogue, camera, duration, and LumenX `r2v` workflow mode.

Added:

- `factory/lumenx_adapter.py`
- LumenX script-like offline export.
- LumenX API execution plan for future live service integration.

Next:

- Add an optional live LumenX health check and dry API executor.
- Add API-key/environment reporting before attempting real generation.

## 2026-07-09 — v0.7 LumenX Live Health Bridge

Command:

```bash
scripts/start_factory.sh
cat runs/lumenx_health.json
```

Result:

- `22 passed`.
- Generated `runs/lumenx_health.json`.
- Current live LumenX status: unavailable.
- Error recorded: `Connection refused` for `http://localhost:17177/health`.
- Offline factory generation still completed successfully.

Added:

- `factory/lumenx_live.py`
- `factory_cli.py lumenx-health`
- Deployment script now reports live LumenX readiness without blocking dry-run output.

Next:

- Add environment/API-key readiness reporting.
- Attempt a controlled LumenX backend start once dependencies are installed or documented.

## 2026-07-09 — v0.8 Environment Readiness Report

Command:

```bash
scripts/start_factory.sh
cat runs/env_readiness.json
```

Result:

- `26 passed`.
- Generated `runs/env_readiness.json`.
- Current generation readiness: `ready_for_lumenx_generation=false`.
- Required key missing: `DASHSCOPE_API_KEY`.
- Optional keys missing: MuleRun, Kling, Vidu, OSS, Alibaba Cloud keys.
- Offline factory generation still completed successfully.

Added:

- `factory/env_readiness.py`
- `factory_cli.py env-report`
- Deployment script now reports missing required/optional generation credentials without printing secret values.

Next:

- Add controlled LumenX backend startup instructions/checks.
- Once `DASHSCOPE_API_KEY` is available, attempt live project creation using `lumenx_handoff.json`.

## 2026-07-09 — v0.9 LumenX Backend Bootstrap Diagnostics

Command:

```bash
scripts/start_factory.sh
cat runs/lumenx_bootstrap.json
```

Result:

- `30 passed`.
- Generated `runs/lumenx_bootstrap.json`.
- LumenX source root, `requirements.txt`, `start_backend.sh`, `package.json`, and `src/apps/comic_gen/api.py` exist.
- Current backend startup readiness: `ready_to_start_backend=false`.
- Missing backend Python modules include `fastapi`, `uvicorn`, `python-multipart`, `dashscope`, `pydantic-settings`, `PyJWT`, Alibaba Cloud SDK packages, `oss2`, `demucs`, and `soundfile`.
- Root Node helper scripts referenced by `package.json` are absent from the sparse checkout, but direct uvicorn startup remains available once Python dependencies are installed.
- Live health check still reports `Connection refused` for `http://localhost:17177/health`.

Added:

- `factory/lumenx_bootstrap.py`
- `factory_cli.py lumenx-bootstrap`
- Deployment script now writes backend startup diagnostics before the live health check.

Next:

- Create or select a dedicated LumenX Python virtualenv instead of reusing OpenMontage's venv.
- Install LumenX backend dependencies into that venv and retry a controlled local backend start.
- After backend health succeeds and `DASHSCOPE_API_KEY` is configured, attempt live project creation from `lumenx_handoff.json`.

## 2026-07-09 — v1.0 LumenX Backend Live Smoke

Commands:

```bash
scripts/bootstrap_lumenx_backend.sh
scripts/start_lumenx_backend.sh
scripts/start_factory.sh
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py lumenx-execute-handoff --handoff runs/sample_episode/lumenx_handoff.json
```

Result:

- `35 passed`.
- Created isolated LumenX Python 3.12 venv at `external/lumenx/.venv`.
- Installed API-start backend dependencies without modifying OpenMontage's venv.
- `runs/lumenx_bootstrap.json` now reports `ready_to_start_backend=true`.
- LumenX backend started on `http://127.0.0.1:17177`.
- `runs/lumenx_health.json` reports `available=true`.
- Executed `runs/sample_episode/lumenx_handoff.json` against the live LumenX backend.
- Generated `runs/sample_episode/lumenx_live_execution.json`.
- Live LumenX project ID: `6824176a-34b1-430f-b01f-dc043ff6db00`.
- Final live counts: 2 characters, 1 scene, 6 frames.

Added:

- `scripts/bootstrap_lumenx_backend.sh`
- `scripts/start_lumenx_backend.sh`
- `factory/lumenx_live_executor.py`
- `factory_cli.py lumenx-execute-handoff`
- Live execution report with local-to-LumenX ID mappings.

Remaining:

- `DASHSCOPE_API_KEY` is still missing, so real image/video/TTS generation is not attempted yet.
- Full runtime packages such as Demucs and desktop WebView support remain optional/missing in the API-start venv.

Next:

- Add a guarded generation step that refuses to spend credits unless credentials are present and the user explicitly enables real generation.
- Add first-frame/image generation integration after `DASHSCOPE_API_KEY` is configured.

## 2026-07-10 — v1.1 Guarded Real Generation Gate

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
cat runs/sample_episode/lumenx_generation_report.json
```

Result:

- `40 passed`.
- Deployment script completes with the guarded generation step enabled in plan-only mode.
- Generated `runs/sample_episode/lumenx_generation_report.json`.
- Planned real-generation endpoints:
  - `POST /projects/{script_id}/generate_assets`
  - `POST /projects/{script_id}/generate_storyboard`
  - `POST /projects/{script_id}/generate_audio`
  - `POST /projects/{script_id}/generate_video`
- Default report has `executed=false` and `requests=[]`.
- Current blockers are explicit: real generation is disabled and `DASHSCOPE_API_KEY` is missing.

Added:

- `factory/lumenx_generation_guard.py`
- `factory_cli.py lumenx-generate-live`
- Guard tests for plan-only mode, missing credentials, enabled execution, and CLI exit codes.
- `scripts/start_factory.sh` now writes the generation guard report when a live execution report exists.

Next:

- Configure `DASHSCOPE_API_KEY` in `external/lumenx/.env` or process environment.
- With LumenX backend running, execute a small guarded stage first, preferably `--stages audio --enable-real-generation`, before full image/video generation.

## 2026-07-10 — v1.2 End-to-End Readiness Verifier

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
cat runs/end_to_end_readiness.json
```

Result:

- `44 passed`.
- Deployment script now writes `runs/end_to_end_readiness.json`.
- Current readiness:
  - `overall_status=demo_ready_blocked_for_real_generation`
  - `demo_ready=true`
  - `goal_ready=false`
  - passed checks: 11
  - warnings: 1 (`lumenx_backend_health`, because the backend is not currently running)
  - failed checks: 0
  - blocked checks: 2 (`environment_credentials`, `real_generation`)

Added:

- `factory/end_to_end_readiness.py`
- `factory_cli.py readiness`
- Tests for readiness report generation and CLI behavior.
- `scripts/start_factory.sh` now emits the final readiness summary after sample generation.

Next:

- Provide `DASHSCOPE_API_KEY`.
- Start LumenX backend and run `factory_cli.py lumenx-generate-live --execution runs/sample_episode/lumenx_live_execution.json --stages audio --enable-real-generation`.
- Re-run `factory_cli.py readiness --project sample_episode` and check that blockers shrink before enabling storyboard/video generation.

## 2026-07-10 — v1.3 Local Voiced Preview

Commands:

```bash
scripts/start_factory.sh
ffprobe -v error -show_entries stream=index,codec_type,codec_name,duration:format=duration -of json runs/sample_episode/card_preview_voiced.mp4
ffmpeg -hide_banner -i runs/sample_episode/card_preview_voiced.mp4 -map 0:a:0 -af volumedetect -f null - 2>&1 | rg 'mean_volume|max_volume'
```

Result:

- `50 passed`.
- Generated `runs/sample_episode/card_preview_voiced.mp4`.
- Generated `runs/sample_episode/voiceover/voiceover.m4a`.
- Generated `runs/sample_episode/voiceover/voiceover_script.txt`.
- `ffprobe` confirms video, AAC audio, and subtitle streams.
- `volumedetect` confirms the voiceover is not silent:
  - `mean_volume: -15.0 dB`
  - `max_volume: -0.5 dB`
- End-to-end readiness now passes 14 checks, with `demo_ready=true` and `goal_ready=false`.

Added:

- `factory/local_voiceover.py`
- Local macOS `say` voice cue planning.
- Per-line AIFF clip generation, delayed FFmpeg audio mix, and voiced MP4 muxing.
- Readiness checks for voiced preview video, voiceover audio, and voiceover script.

Next:

- Replace local macOS voiceover with LumenX/DashScope TTS once `DASHSCOPE_API_KEY` is configured.
- Keep local voiceover as a no-cost review fallback for story and timing.

## 2026-07-10 — v1.4 OpenMontage Post Final Preview

Commands:

```bash
scripts/start_factory.sh
ffprobe -v error -show_entries stream=index,codec_type,codec_name,duration:format=duration -of compact=p=0:nk=1 output/sample_episode/final_preview.mp4
ffmpeg -hide_banner -i output/sample_episode/final_preview.mp4 -map 0:a:0 -af volumedetect -f null - 2>&1 | rg 'mean_volume|max_volume'
```

Result:

- `55 passed`.
- Generated `output/sample_episode/final_preview.mp4`.
- Generated `runs/sample_episode/openmontage_post_report.json`.
- `ffprobe` confirms the final preview contains H.264 video, AAC audio, and mov_text subtitles.
- Final preview duration is 45.0 seconds.
- `volumedetect` confirms the final preview audio is not silent:
  - `mean_volume: -15.1 dB`
  - `max_volume: -0.5 dB`
- End-to-end readiness includes `openmontage_post_report` and `final_preview_video`, with `demo_ready=true` and `goal_ready=false`.

Added:

- `factory/openmontage_post.py`
- `factory_cli.py plan` now writes a final preview delivery file after local voiceover.
- OpenMontage post report with detected OpenMontage path, candidate tools, timeline count, source preview, and final preview path.
- Readiness checks for OpenMontage post report and final preview video.

Remaining:

- The current post step uses the factory FFmpeg finalizer while preserving the OpenMontage handoff package and reporting detected OpenMontage tool candidates.
- Real OpenMontage internal rendering can replace the finalizer once a stable OpenMontage CLI contract is selected.
- `DASHSCOPE_API_KEY` is still missing, so real LumenX image/video/TTS generation remains blocked.

Next:

- Wire one explicit OpenMontage tool path, likely caption burn or Remotion composer, once its command contract is validated against `openmontage_package.json`.
- Configure `DASHSCOPE_API_KEY`, then run a small live LumenX stage before enabling full video generation.

## 2026-07-10 — v1.5 OpenMontage Remotion Caption Burn

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
ffprobe -v error -show_entries stream=index,codec_type,codec_name,width,height,duration:format=duration,bit_rate -of json output/sample_episode/final_preview.mp4
ffmpeg -hide_banner -i output/sample_episode/final_preview.mp4 -map 0:a:0 -af volumedetect -f null - 2>&1 | rg 'mean_volume|max_volume'
ffmpeg -y -i output/sample_episode/final_preview.mp4 -vf "fps=1/5,scale=270:-1,tile=3x3" -frames:v 1 runs/sample_episode/final_preview_contact_sheet.jpg
```

Result:

- `58 passed`.
- `scripts/start_factory.sh` generated `output/sample_episode/final_preview.mp4` through OpenMontage's `remotion_caption_burn` tool.
- `runs/sample_episode/openmontage_post_report.json` now reports `mode=openmontage_remotion_caption_burn`.
- Final preview is 1080x1920 H.264 video with AAC audio, 45.056 seconds.
- `volumedetect` confirms the final preview audio is not silent:
  - `mean_volume: -18.1 dB`
  - `max_volume: -3.1 dB`
- Visual self-check inspected `runs/sample_episode/final_preview_frame_20s.png` and `runs/sample_episode/final_preview_contact_sheet.jpg`.
- The first Remotion caption pass overlaid oversized Chinese captions because SRT lines had no spaces; the factory now converts SRT cues into short Chinese caption chunks before calling OpenMontage.

Added:

- OpenMontage `RemotionCaptionBurn` integration in `factory/openmontage_post.py`.
- Fallback to the existing FFmpeg stream-copy finalizer when OpenMontage caption burn is unavailable.
- `build_caption_segments_from_srt` for Chinese caption chunk timing.
- `factory_cli.py plan` now passes `subtitles.srt` into the OpenMontage post step.

Remaining:

- The visual layer is still the factory storyboard-card preview, not LumenX-generated AI character/video frames.
- `DASHSCOPE_API_KEY` is still missing, so real LumenX image/video/TTS generation remains blocked.

Next:

- Configure `DASHSCOPE_API_KEY`, start LumenX backend, and run a small real stage such as audio before full storyboard/video generation.
- After LumenX frames exist, feed those generated frames into the same OpenMontage Remotion post step.

## 2026-07-10 — v1.6 LumenX Live Re-Smoke After OpenMontage Post

Commands:

```bash
scripts/start_lumenx_backend.sh
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py lumenx-health --base-url http://127.0.0.1:17177 --timeout 2
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py lumenx-execute-handoff --handoff runs/sample_episode/lumenx_handoff.json --base-url http://127.0.0.1:17177 --timeout 20
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py lumenx-generate-live --execution runs/sample_episode/lumenx_live_execution.json --stages assets,storyboard,audio,video
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py readiness --project sample_episode
```

Result:

- LumenX backend health passed at `http://127.0.0.1:17177/health`.
- Re-executed `runs/sample_episode/lumenx_handoff.json` against live LumenX.
- Live LumenX project ID: `694cf063-e843-482e-aeaf-5f74d9bf6153`.
- Final live counts: 2 characters, 1 scene, 6 frames.
- Guarded generation plan still refuses to spend credits by default.
- End-to-end readiness now reports:
  - `passed=17`
  - `warning=0`
  - `failed=0`
  - `blocked=2`
  - `demo_ready=true`
  - `goal_ready=false`

Remaining:

- `DASHSCOPE_API_KEY` is still missing.
- Real LumenX stages are still unexecuted: assets, storyboard, audio, video.

Next:

- Add `DASHSCOPE_API_KEY` to `external/lumenx/.env` or the shell environment.
- Run guarded real generation one stage at a time, starting with `audio`, then `assets/storyboard`, then `video`.

## 2026-07-10 — v1.7 LumenX Live-Run Aggregator

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py lumenx-live-run --project sample_episode --base-url http://127.0.0.1:9 --timeout 0.1
scripts/start_lumenx_backend.sh
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py lumenx-live-run --project sample_episode --base-url http://127.0.0.1:17177 --timeout 20 --stages audio
```

Result:

- `61 passed`.
- Added `factory_cli.py lumenx-live-run`.
- Added `runs/sample_episode/lumenx_live_run.json` as the aggregate live smoke report.
- Unavailable backend path writes a failed report and returns nonzero without attempting handoff or generation.
- Live backend path passed health, handoff, generation guard planning, and readiness in one command.
- Live LumenX project ID: `6a82fd84-1c24-46e7-9b93-5d01d31d9c3f`.
- Final live counts: 2 characters, 1 scene, 6 frames.
- Generation remained plan-only for `audio`, with `generation_executed=false`.

Added:

- `factory/lumenx_live_pipeline.py`
- Tests for successful live-run sequencing, unavailable backend stop behavior, and CLI report writing.
- README and deployment docs for the new aggregator command.

Remaining:

- `DASHSCOPE_API_KEY` is still missing.
- `--enable-real-generation` has not been run, so the full LumenX generation goal is still not complete.

Next:

- Once `DASHSCOPE_API_KEY` is configured, run:
  `/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py lumenx-live-run --project sample_episode --base-url http://127.0.0.1:17177 --stages audio --enable-real-generation`

## 2026-07-10 — v1.8 Backend-Starting Live Pipeline Wrapper

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
LUMENX_GENERATION_STAGES=audio LUMENX_TIMEOUT=20 LUMENX_HEALTH_ATTEMPTS=20 scripts/run_lumenx_live_pipeline.sh
lsof -nP -iTCP:17177 -sTCP:LISTEN || true
```

Result:

- `63 passed`.
- Added `scripts/run_lumenx_live_pipeline.sh`.
- The wrapper starts LumenX backend, waits for a health JSON payload with `"available": true`, runs `factory_cli.py lumenx-live-run`, and cleans up the backend process.
- Verified the wrapper with a real local backend.
- Live LumenX project ID: `90c4dc30-0eee-44a6-83bd-76f05480a6d4`.
- Final live counts: 2 characters, 1 scene, 6 frames.
- `lsof` confirmed no lingering listener on port `17177` after wrapper exit.

Finding:

- First wrapper attempt treated `factory_cli.py lumenx-health` exit code `0` as service readiness even when the JSON said `"available": false`.
- Fix: the wrapper now waits for the health command output to contain `"available": true`.

Remaining:

- Real generation still requires `DASHSCOPE_API_KEY`.

Next:

- Run `ENABLE_REAL_GENERATION=1 LUMENX_GENERATION_STAGES=audio scripts/run_lumenx_live_pipeline.sh` after the key is configured.

## 2026-07-10 — v1.9 Character Reference Asset Ingestion

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py plan --input samples/sample_novel.txt --project sample_episode_assets --title 旧城来信 --shots 8 --character-assets /tmp/.../character_assets.json
ffprobe -v error -show_entries stream=index,codec_type,codec_name,duration:format=duration -of compact=p=0:nk=1 output/sample_episode_assets/final_preview.mp4
```

Result:

- `69 passed`.
- Default `scripts/start_factory.sh` still completes with `demo_ready=true`; `character_assets_ready=false` is reported as a warning, not a demo blocker.
- Added `runs/<project>/character_assets.json`.
- Added optional `factory_cli.py plan --character-assets <manifest.json>`.
- Verified a manifest with two local image paths produces `character_assets_ready=true`.
- Verified LumenX handoff includes:
  - character descriptions with reference image paths
  - `api_plan.update_character_images`
  - `/projects/{script_id}/assets/update_image` payloads for both characters
- Verified OpenMontage package carries the same character reference image paths.
- `output/sample_episode_assets/final_preview.mp4` is valid H.264/AAC, 45.056 seconds.

Added:

- `factory/character_assets.py`
- Character reference asset propagation through LumenX handoff, OpenMontage package, CLI status, and readiness checks.
- Live LumenX executor support for `update_character_images` after live character ID mapping.

Remaining:

- The test manifest used existing storyboard PNGs as stand-ins; user-provided AI character images should replace those paths.
- Real LumenX generation still requires `DASHSCOPE_API_KEY`.

Next:

- After real character images and `DASHSCOPE_API_KEY` are available, run plan with `--character-assets`, then execute `ENABLE_REAL_GENERATION=1 LUMENX_GENERATION_STAGES=audio scripts/run_lumenx_live_pipeline.sh`.

## 2026-07-10 — v1.10 Character Asset Template Command

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py character-assets-template --input samples/sample_novel.txt --project sample_episode_template --title 旧城来信 --output /tmp/.../character_assets.template.json
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py plan --input samples/sample_novel.txt --project sample_episode_template --title 旧城来信 --shots 2 --character-assets /tmp/.../character_assets.filled.json
ffprobe -v error -show_entries stream=index,codec_type,codec_name,duration:format=duration -of compact=p=0:nk=1 output/sample_episode_template/final_preview.mp4
```

Result:

- `71 passed`.
- Added `factory_cli.py character-assets-template`.
- The command extracts the two planned characters from the novel and writes a fillable manifest with `character_id`, `name`, and blank `reference_image` fields.
- Verified that filling the template with two image paths can be passed to `plan --character-assets`.
- Verified generated `runs/sample_episode_template/character_assets.json` reports `asset_ready=true`.
- Verified LumenX handoff still includes `api_plan.update_character_images`.
- Verified OpenMontage package carries the same reference image paths.
- `output/sample_episode_template/final_preview.mp4` is valid H.264/AAC, 15.061 seconds for the two-shot smoke.

Remaining:

- Real user-generated AI role images should replace the temporary stand-in images used by the smoke test.
- Real LumenX generation still requires `DASHSCOPE_API_KEY`.

Next:

- Use `character-assets-template` before planning new novels so the generated character images can be wired into LumenX and OpenMontage without hand-writing IDs.

## 2026-07-10 — v1.11 Character Generation Brief

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_character_brief.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py character-brief --input samples/sample_novel.txt --project sample_episode --title 旧城来信 --output runs/sample_episode/character_generation_brief.json
```

Result:

- `73 passed`.
- Added `factory_cli.py character-brief`.
- Added `factory/character_brief.py`.
- The command extracts the planned episode characters and writes AI role-image prompts for the reference image, full-body design, and turnaround sheet.
- The brief includes a ready-to-fill `character_assets_template` block with suggested `assets/characters/*_reference.png` paths.
- Verified sample output at `runs/sample_episode/character_generation_brief.json` for 林澈 and 苏眠.

Remaining:

- Real role images still need to be generated by the user's chosen image model and saved to the suggested paths, or the paths in the generated manifest need to be edited.
- Real LumenX generation still requires `DASHSCOPE_API_KEY`.

Next:

- Add a helper that converts the brief's `character_assets_template` block into a standalone manifest after image files are present.

## 2026-07-10 — v1.12 Character Brief Manifest Export

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_character_brief.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py character-assets-from-brief --brief runs/sample_episode/character_generation_brief.json --output runs/sample_episode/character_assets.from_brief.json
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py character-assets-from-brief --brief runs/sample_episode/character_generation_brief.json --output runs/sample_episode/character_assets.require_files.json --require-files
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py plan --input samples/sample_novel.txt --project sample_episode_brief_flow --title 旧城来信 --shots 2 --character-assets runs/sample_episode/character_assets.require_files.json
ffprobe -v error -show_entries stream=index,codec_type,codec_name,duration:format=duration -of compact=p=0:nk=1 output/sample_episode_brief_flow/final_preview.mp4
```

Result:

- `77 passed`.
- Added `factory_cli.py character-assets-from-brief`.
- The command exports the brief's `character_assets_template` block to a standalone manifest accepted by `plan --character-assets`.
- `--require-files` now returns structured JSON with `success=false` when images are missing, instead of a Python traceback.
- Verified the positive path with sample reference files:
  - `character_assets_ready=true`
  - LumenX handoff includes `api_plan.update_character_images`
  - OpenMontage package carries resolved reference image paths
  - `output/sample_episode_brief_flow/final_preview.mp4` is valid H.264/AAC, 15.061 seconds

Remaining:

- The smoke used placeholder files at the suggested paths; production runs should replace them with real generated character images.
- Real LumenX generation still requires `DASHSCOPE_API_KEY`.

Next:

- Add a small readiness check that reports whether generated role images are real decodable images, not only present paths.

## 2026-07-10 — v1.13 Character Reference Image Signature Check

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_character_assets.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py character-assets-from-brief --brief runs/sample_episode/character_generation_brief.json --output runs/sample_episode/character_assets.require_files.json --require-files
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py plan --input samples/sample_novel.txt --project sample_episode_brief_flow --title 旧城来信 --shots 2 --character-assets runs/sample_episode/character_assets.require_files.json
ffprobe -v error -show_entries stream=index,codec_type,codec_name,duration:format=duration -of compact=p=0:nk=1 output/sample_episode_brief_flow/final_preview.mp4
```

Result:

- `78 passed`.
- Added PNG/JPEG/WEBP signature checks to character reference ingestion.
- Empty files or non-image bytes with image extensions now fail with `invalid character reference image`.
- Verified brief-exported manifests still pass after replacing local smoke placeholders with real PNG files.
- Verified `sample_episode_brief_flow` still reports `character_assets_ready=true`, includes LumenX `update_character_images`, and outputs a valid H.264/AAC MP4, 15.061 seconds.

Remaining:

- This is a lightweight file-signature check, not a full perceptual quality check for image consistency.
- Real LumenX generation still requires `DASHSCOPE_API_KEY`.

Next:

- Add an operator-facing command that summarizes the whole current workflow state: env, LumenX readiness, character refs, latest preview, and remaining blockers.

## 2026-07-10 — v1.14 Operator Workflow Status

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_workflow_status.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py workflow-status --project sample_episode
```

Result:

- `81 passed`.
- Added `factory/workflow_status.py`.
- Added `factory_cli.py workflow-status`.
- The command writes `runs/<project>/workflow_status.json` and prints a concise JSON summary.
- The report includes:
  - overall readiness state
  - missing generation credentials
  - LumenX bootstrap, health, live project, and real generation state
  - character reference asset readiness
  - final preview and voiced preview paths
  - blockers and next action IDs
- Current `sample_episode` status:
  - `demo_ready=true`
  - `goal_ready=false`
  - blockers: `environment_credentials`, `real_generation`
  - next actions: `configure_dashscope_api_key`, `generate_character_references`, `run_real_lumenx_generation`

Remaining:

- Real LumenX generation still requires `DASHSCOPE_API_KEY`.
- Main `sample_episode` still needs real generated character reference images for production-quality AI video generation.

Next:

- Make `scripts/start_factory.sh` emit the new workflow status summary after readiness so one script gives the full operator view.

## 2026-07-10 — v1.15 Start Script Workflow Status

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_live_pipeline_script.py::test_start_factory_script_emits_workflow_status_after_readiness -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
ffprobe -v error -show_entries stream=index,codec_type,codec_name,duration:format=duration -of compact=p=0:nk=1 output/sample_episode/final_preview.mp4
```

Result:

- `82 passed`.
- `scripts/start_factory.sh` now emits `factory_cli.py workflow-status --project sample_episode` after readiness.
- The one-command deployment now writes `runs/sample_episode/workflow_status.json`.
- Latest `scripts/start_factory.sh` result:
  - `demo_ready=true`
  - `goal_ready=false`
  - blockers: `environment_credentials`, `real_generation`
  - next actions: `configure_dashscope_api_key`, `generate_character_references`, `run_real_lumenx_generation`
- Verified `output/sample_episode/final_preview.mp4` is valid H.264/AAC, 45.056 seconds.

Remaining:

- Real LumenX generation still requires `DASHSCOPE_API_KEY`.
- Main `sample_episode` still needs real generated character reference images for production-quality AI video generation.

Next:

- Add a small command that can validate `DASHSCOPE_API_KEY` presence and then run only the cheapest real LumenX stage first, likely audio, before attempting full video generation.

## 2026-07-10 — v1.16 Audio-Only Real Generation Smoke

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_live_pipeline_script.py -q
env -u DASHSCOPE_API_KEY FACTORY_PYTHON=/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python LUMENX_AUDIO_SMOKE_REPORT=/tmp/lumenx_audio_smoke_preflight.json bash scripts/run_lumenx_audio_smoke.sh
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
```

Result:

- `85 passed`.
- Added `scripts/run_lumenx_audio_smoke.sh`.
- The script preflights `DASHSCOPE_API_KEY` before starting LumenX.
- Missing-key behavior now writes structured JSON with:
  - `success=false`
  - `executed=false`
  - `stages=["audio"]`
  - `blocked_reasons=["DASHSCOPE_API_KEY is missing."]`
- The script delegates to `scripts/run_lumenx_live_pipeline.sh` with `ENABLE_REAL_GENERATION=1` and `LUMENX_GENERATION_STAGES=audio` only after credentials are ready.
- `scripts/run_lumenx_live_pipeline.sh` now respects `FACTORY_CONFIG` and passes `--config "$CONFIG_PATH"` to `factory_cli.py`.

Remaining:

- Real audio generation still cannot be executed until `DASHSCOPE_API_KEY` is configured.
- This smoke covers only audio; full video generation should remain a later, explicit step after audio passes.

Next:

- After a real key is available, run `scripts/run_lumenx_audio_smoke.sh`, inspect `runs/sample_episode/lumenx_generation_report.json`, and only then consider storyboard/video stages.

## 2026-07-10 — v1.17 Character Asset Status Report

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_character_brief.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py character-brief --input samples/sample_novel.txt --project sample_episode --title 旧城来信 --output runs/sample_episode/character_generation_brief.json
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py character-assets-status --brief runs/sample_episode/character_generation_brief.json --output runs/sample_episode/character_assets_status.json
```

Result:

- `88 passed`.
- Added `factory_cli.py character-assets-status`.
- Added `build_character_assets_status_from_brief`.
- The report writes one row per planned character with:
  - suggested reference image path
  - resolved absolute path
  - file existence
  - image signature validity
  - `ready`, `missing`, or `invalid` status
- `character-assets-from-brief --require-files` now uses the same PNG/JPEG/WEBP signature validation as `plan --character-assets`.
- Current `runs/sample_episode/character_assets_status.json` reports `asset_ready=true` for the two sample reference PNGs under `runs/sample_episode/assets/characters/`.

Remaining:

- These sample reference PNGs are still local smoke assets; production runs should replace them with real generated AI character references.
- Real LumenX generation still requires `DASHSCOPE_API_KEY`.

Next:

- Teach `scripts/start_factory.sh` to automatically use a ready `character_assets.from_brief.json` when character references pass status checks, while preserving a fallback path when images are missing.

## 2026-07-10 — v1.18 Auto-Wire Ready Character References

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_live_pipeline_script.py::test_start_factory_script_uses_ready_character_assets_from_brief -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
rg -n "asset_ready|reference_image_path|update_character_images|image_url" runs/sample_episode/character_assets.json runs/sample_episode/lumenx_handoff.json runs/sample_episode/openmontage_package.json runs/sample_episode/workflow_status.json
ffprobe -v error -show_entries stream=index,codec_type,codec_name,duration:format=duration -of compact=p=0:nk=1 output/sample_episode/final_preview.mp4
```

Result:

- `89 passed`.
- `scripts/start_factory.sh` now generates `character_generation_brief.json`, writes `character_assets_status.json`, and checks whether references are ready before planning.
- When `asset_ready=true`, the script exports `character_assets.from_brief.json` with `--require-files` and passes it to `factory_cli.py plan --character-assets`.
- Latest `scripts/start_factory.sh` result:
  - `character_assets_ready=true`
  - LumenX handoff includes `api_plan.update_character_images`
  - OpenMontage package includes resolved reference image paths
  - workflow next actions no longer include `generate_character_references`
- Verified `output/sample_episode/final_preview.mp4` is valid H.264/AAC, 45.056 seconds.

Remaining:

- The current ready reference images are local smoke PNGs; production-quality runs should replace them with real generated AI character references.
- Real LumenX generation still requires `DASHSCOPE_API_KEY`.

Next:

- Add a production asset provenance field so smoke placeholder references and real AI-generated role references are not confused.

## 2026-07-10 — v1.19 Character Asset Provenance

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_character_assets.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_character_brief.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_workflow_status.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
rg -n "production_ready|asset_source|provenance_status|next_actions|confirm_character_reference_provenance" runs/sample_episode/character_assets.json runs/sample_episode/character_assets_status.json runs/sample_episode/workflow_status.json
ffprobe -v error -show_entries stream=index,codec_type,codec_name,duration:format=duration -of compact=p=0:nk=1 output/sample_episode/final_preview.mp4
```

Result:

- `92 passed`.
- Added `asset_source`, `provenance_status`, and `production_ready` to character asset ingestion.
- Added production source rule: `asset_source=user_generated_ai`.
- `character-assets-status` now reports image readiness separately from provenance readiness.
- `workflow-status` now emits `confirm_character_reference_provenance` when images are ready but their source is missing, placeholder, or unknown.
- Latest `scripts/start_factory.sh` result:
  - `character_assets_ready=true`
  - `production_ready=false`
  - next actions include `confirm_character_reference_provenance`
  - final preview remains valid H.264/AAC, 45.056 seconds

Remaining:

- Current sample role references are valid local smoke PNGs but still lack confirmed production provenance.
- Real LumenX generation still requires `DASHSCOPE_API_KEY`.

Next:

- After replacing smoke references with real generated role images, set `asset_source=user_generated_ai`, rerun `scripts/start_factory.sh`, then verify `production_ready=true` before real audio/video generation.

## 2026-07-10 — v1.20 Visual Generation Production Gate

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_lumenx_generation_guard.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_cli_lumenx_generate_live.py tests/test_lumenx_live_pipeline.py tests/test_live_pipeline_script.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py lumenx-generate-live --execution runs/sample_episode/lumenx_live_execution.json --stages audio --output runs/sample_episode/lumenx_generation_audio_guard.json
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py lumenx-generate-live --execution runs/sample_episode/lumenx_live_execution.json --stages assets,storyboard,video --output runs/sample_episode/lumenx_generation_visual_guard.json
```

Result:

- `94 passed`.
- Added `VISUAL_GENERATION_STAGES = {"assets", "storyboard", "video"}` to the LumenX generation guard.
- Visual stages now require `character_assets.json` to report `production_ready=true` before real generation can execute.
- Audio-only generation remains allowed by the character provenance gate, so `scripts/run_lumenx_audio_smoke.sh` can still be the first real-generation smoke after `DASHSCOPE_API_KEY` is configured.
- Current sample guard reports:
  - audio stages blocked only by real-generation disabled/key state
  - visual stages additionally blocked by `Character reference assets are not production-ready for visual generation.`

Remaining:

- Real generation still requires `DASHSCOPE_API_KEY`.
- Visual real generation additionally requires replacing smoke references with real role images and marking `asset_source=user_generated_ai`.

Next:

- Add a concise preflight command for real-generation readiness that reports audio-ready versus visual-ready separately.

## 2026-07-10 — v1.21 Real Generation Preflight

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_real_generation_preflight.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_real_generation_preflight.py tests/test_lumenx_generation_guard.py tests/test_cli_lumenx_generate_live.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py real-generation-preflight --project sample_episode --output runs/sample_episode/real_generation_preflight.json
rg -n "audio_ready|visual_ready|all_ready|missing_required_keys|ready|blocked_reasons|production_ready|requires_production_character_assets" runs/sample_episode/real_generation_preflight.json
```

Result:

- `99 passed`.
- Added `factory/real_generation_preflight.py`.
- Added `factory_cli.py real-generation-preflight`.
- The report separates:
  - `audio_ready`
  - `visual_ready`
  - per-stage blockers for `assets`, `storyboard`, `audio`, and `video`
- Current `sample_episode` preflight:
  - `audio_ready=false` because `DASHSCOPE_API_KEY` is missing
  - `visual_ready=false` because `DASHSCOPE_API_KEY` is missing and character assets are not `production_ready`
  - visual stages include `requires_production_character_assets=true`

Remaining:

- Real audio smoke still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires real generated role references with `asset_source=user_generated_ai`.

Next:

- Emit `real-generation-preflight` from `scripts/start_factory.sh` so one-command deployment writes every operator report needed before real generation.

## 2026-07-10 — v1.22 Start Script Real Generation Preflight

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_live_pipeline_script.py::test_start_factory_script_emits_real_generation_preflight -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
ffprobe -v error -show_entries stream=index,codec_type,codec_name,duration:format=duration -of compact=p=0:nk=1 output/sample_episode/final_preview.mp4
rg -n "audio_ready|visual_ready|all_ready|missing_required_keys|production_ready|blocked_reasons|requires_production_character_assets" runs/sample_episode/real_generation_preflight.json
```

Result:

- `100 passed`.
- `scripts/start_factory.sh` now emits `factory_cli.py real-generation-preflight --project sample_episode`.
- One-command deployment now writes `runs/sample_episode/real_generation_preflight.json`.
- Latest start script result:
  - `demo_ready=true`
  - `audio_ready=false`
  - `visual_ready=false`
  - missing key: `DASHSCOPE_API_KEY`
  - character assets: `production_ready=false`
- Verified `output/sample_episode/final_preview.mp4` is valid H.264/AAC, 45.000 seconds.

Remaining:

- Configure `DASHSCOPE_API_KEY` to make audio preflight pass.
- Replace smoke role references with real generated role images and set `asset_source=user_generated_ai` to make visual preflight pass.

Next:

- Add a tiny manifest helper to stamp `asset_source=user_generated_ai` on a reviewed character asset manifest after real role images are generated.

## 2026-07-10 — v1.23 Character Asset Source Confirmation

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_character_brief.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_workflow_status.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_live_pipeline_script.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py character-assets-confirm-source --manifest <tmp>/character_assets.from_brief.json --output <tmp>/character_assets.confirmed.json
scripts/start_factory.sh
ffprobe -v error -show_entries stream=index,codec_type,codec_name,duration:format=duration -of compact=p=0:nk=1 output/sample_episode/final_preview.mp4
```

Result:

- `105 passed`.
- Added `factory_cli.py character-assets-confirm-source`.
- The command stamps reviewed character references with `asset_source=user_generated_ai`, adds `provenance_status=confirmed`, and marks entries `production_ready=true` only after file and image signature checks pass.
- Placeholder or untrusted source labels such as `smoke_placeholder` are rejected by the confirmation helper.
- `workflow-status` now tells the operator to run `character-assets-confirm-source` when images exist but provenance is not confirmed.
- `scripts/start_factory.sh` now prefers `runs/sample_episode/character_assets.confirmed.json` when it exists, and otherwise falls back to `character_assets.from_brief.json` for dry-run demos.
- Verified the confirmation command in a temporary directory so no sample placeholder images were stamped as production assets.
- Latest one-command deployment remains `demo_ready=true`; final preview is H.264/AAC, 45.056 seconds.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires real generated role references to be saved and confirmed into `runs/sample_episode/character_assets.confirmed.json`.

Next:

- Once `DASHSCOPE_API_KEY` is configured, run an audio-only real-generation smoke before enabling visual stages.

## 2026-07-10 — v1.24 Operator Handoff Package

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_operator_handoff.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_live_pipeline_script.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py operator-handoff --project sample_episode
scripts/start_factory.sh
ffprobe -v error -show_entries stream=index,codec_type,codec_name,duration:format=duration -of compact=p=0:nk=1 output/sample_episode/final_preview.mp4
```

Result:

- `109 passed`.
- Added `factory/operator_handoff.py`.
- Added `factory_cli.py operator-handoff`.
- The command writes:
  - `runs/sample_episode/operator_handoff.json`
  - `runs/sample_episode/operator_handoff.md`
- The handoff summarizes:
  - demo/goal state
  - final preview path
  - LumenX live script id
  - audio/visual real-generation readiness
  - exact next commands for `.env`, character provenance confirmation, audio smoke, and full guarded generation
- `.env` setup in the generated handoff is non-destructive: it uses `test -f ... || cp ...` before opening the file.
- `scripts/start_factory.sh` now emits `factory_cli.py operator-handoff --project sample_episode` after real-generation preflight.
- Latest one-command deployment remains `demo_ready=true`; final preview is H.264/AAC, 45.056 seconds.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires real generated role references confirmed into `runs/sample_episode/character_assets.confirmed.json`.

## 2026-07-10 — v1.26 Character Prompts in Operator Handoff

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_operator_handoff.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py operator-handoff --project sample_episode
```

Result:

- `109 passed`.
- `operator_handoff.json` now includes `character_generation.brief_path`, `prompt_count`, and per-character prompt records.
- `operator_handoff.md` now includes a `Character Image Prompts` section with:
  - character name
  - recommended reference image path
  - positive prompt
  - negative prompt
- This makes the remaining real role-image generation step directly actionable from the handoff file.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires using those prompts to generate role references, then confirming them into `runs/sample_episode/character_assets.confirmed.json`.

## 2026-07-10 — v1.27 Reviewed Role Image Installer

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_character_brief.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_operator_handoff.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py character-assets-install-references --brief <tmp>/character_generation_brief.json --source-manifest <tmp>/reviewed_role_images.json --output <tmp>/character_assets.confirmed.json
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py operator-handoff --project sample_episode
```

Result:

- `112 passed`.
- Added `factory_cli.py character-assets-install-references`.
- The command reads a reviewed source manifest, validates PNG/JPEG/WEBP signatures, copies images into the brief target paths, and writes `character_assets.confirmed.json` with `asset_source=user_generated_ai`.
- The installer validates the whole batch before copying, so a target conflict or invalid source image does not leave partially installed references.
- Updated `operator_handoff.md` to point at the installer with `runs/sample_episode/reviewed_role_images.json`.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires creating `runs/sample_episode/reviewed_role_images.json` from real generated role images and running the installer.

Next:

- After credentials are configured, run `scripts/run_lumenx_audio_smoke.sh` and capture the first real TTS/audio generation result.

## 2026-07-10 — v1.25 Self-Starting Real Generation Handoff

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_operator_handoff.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_workflow_status.py -q
```

Result:

- Updated the operator handoff full-generation command to use:
  `PROJECT=sample_episode ENABLE_REAL_GENERATION=1 LUMENX_GENERATION_STAGES=assets,storyboard,audio,video scripts/run_lumenx_live_pipeline.sh`
- Updated `workflow-status` to point at the same self-starting live pipeline script.
- This avoids assuming the LumenX backend is already running when the operator starts full real generation from the handoff.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires real generated role references confirmed into `runs/sample_episode/character_assets.confirmed.json`.

## 2026-07-10 — v1.28 Reviewed Role Images Template

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_character_brief.py tests/test_live_pipeline_script.py tests/test_operator_handoff.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py character-assets-reviewed-template --brief runs/sample_episode/character_generation_brief.json --output runs/sample_episode/reviewed_role_images.template.json
scripts/start_factory.sh
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,duration -show_entries format=duration -of json output/sample_episode/final_preview.mp4
```

Result:

- `115 passed`.
- Added `factory_cli.py character-assets-reviewed-template`.
- `scripts/start_factory.sh` now writes `runs/sample_episode/reviewed_role_images.template.json` after generating `character_generation_brief.json`.
- The template keeps `reference_image` blank for each reviewed source image, while including the target brief path, positive prompt, negative prompt, filled manifest path, and installer command.
- `operator_handoff.json` and `operator_handoff.md` now point to both the fillable template and the expected filled `reviewed_role_images.json`.
- Video self-check: final preview remains H.264/AAC, 1080x1920, 45.056 seconds; 9-frame contact sheet shows no black frames or obvious subtitle overflow.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires filling `runs/sample_episode/reviewed_role_images.json` with real generated role image paths and running the installer.

## 2026-07-10 — v1.29 Reviewed Role Images Directory Import

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_character_brief.py tests/test_live_pipeline_script.py tests/test_operator_handoff.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py character-assets-reviewed-from-dir --brief runs/sample_episode/character_generation_brief.json --image-dir runs/sample_episode/assets/characters --output runs/sample_episode/reviewed_role_images.from_dir.json
scripts/start_factory.sh
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,duration -show_entries format=duration -of json output/sample_episode/final_preview.mp4
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,channels,duration -of json output/sample_episode/final_preview.mp4
```

Result:

- `118 passed`.
- Added `factory_cli.py character-assets-reviewed-from-dir`.
- The command scans a reviewed image directory, matches filenames by character id/name, validates PNG/JPEG/WEBP signatures, and writes `reviewed_role_images.json`.
- `scripts/start_factory.sh` now creates `runs/sample_episode/reviewed_role_images/` as the default drop folder.
- `operator_handoff.md` now includes the folder path and exact directory-import command before the role prompts.
- Video self-check: final preview remains H.264/AAC, 1080x1920, 45.056 seconds; refreshed 9-frame contact sheet shows no black frames or obvious subtitle overflow.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires dropping real generated role images into `runs/sample_episode/reviewed_role_images/`, importing them, then running the installer.

## 2026-07-10 — v1.30 Auto-Install Drop Folder Role Images

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_live_pipeline_script.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,duration -show_entries format=duration -of json output/sample_episode/final_preview.mp4
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,channels,duration -of json output/sample_episode/final_preview.mp4
```

Result:

- `119 passed`.
- `scripts/start_factory.sh` now detects PNG/JPEG/WEBP files in `runs/sample_episode/reviewed_role_images/`.
- When matching files exist, the script runs `character-assets-reviewed-from-dir`, then `character-assets-install-references`, before `character-assets-status` and `plan`.
- Empty drop folders keep the dry-run path intact: no `character_assets.confirmed.json` is created, `production_ready=false`, and visual real-generation preflight remains blocked.
- Video self-check: final preview remains H.264/AAC, 1080x1920, 45.056 seconds; refreshed 9-frame contact sheet shows no black frames or obvious subtitle overflow.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires real generated role images to be dropped into `runs/sample_episode/reviewed_role_images/`.

## 2026-07-10 — v1.31 Start Factory Real-Generation Delegation

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_live_pipeline_script.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,duration -show_entries format=duration -of json output/sample_episode/final_preview.mp4
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,channels,duration -of json output/sample_episode/final_preview.mp4
```

Result:

- `120 passed`.
- `ENABLE_REAL_GENERATION=1 scripts/start_factory.sh` now delegates to `scripts/run_lumenx_live_pipeline.sh` after producing the local dry-run package.
- The delegated live pipeline owns backend startup/cleanup, live handoff, and real-generation execution.
- `start_factory.sh` captures the live pipeline exit status, still refreshes readiness, workflow status, real-generation preflight, and operator handoff, then returns that status.
- Default `scripts/start_factory.sh` remains a dry-run deployment path and still completes with `demo_ready=true`.
- Video self-check: final preview remains H.264/AAC, 1080x1920, 45.056 seconds; refreshed 9-frame contact sheet shows no black frames or obvious subtitle overflow.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires production-ready role images in `runs/sample_episode/reviewed_role_images/`.

## 2026-07-10 — v1.32 Unified Real-Generation Operator Entry

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_workflow_status.py tests/test_operator_handoff.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,duration -show_entries format=duration -of json output/sample_episode/final_preview.mp4
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,channels,duration -of json output/sample_episode/final_preview.mp4
```

Result:

- `120 passed`.
- `workflow_status.json` now points the operator to `PROJECT=<project> ENABLE_REAL_GENERATION=1 scripts/start_factory.sh` for full real generation.
- `operator_handoff.json` and `operator_handoff.md` now use `PROJECT=sample_episode ENABLE_REAL_GENERATION=1 LUMENX_GENERATION_STAGES=assets,storyboard,audio,video scripts/start_factory.sh`.
- README and deployment docs now describe `start_factory.sh` as the top-level real-generation entry, with `run_lumenx_live_pipeline.sh` as the delegated backend/live-pipeline implementation.
- Video self-check: final preview remains H.264/AAC, 1080x1920, 45.0 seconds; refreshed 9-frame contact sheet shows no black frames or obvious subtitle overflow.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires production-ready role images in `runs/sample_episode/reviewed_role_images/`.

## 2026-07-10 — v1.33 Real-Generation Start Gate

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_real_generation_start_gate.py tests/test_live_pipeline_script.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
ENABLE_REAL_GENERATION=1 scripts/start_factory.sh
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,duration -show_entries format=duration -of json output/sample_episode/final_preview.mp4
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,channels,duration -of json output/sample_episode/final_preview.mp4
```

Result:

- `125 passed`.
- Added `factory_cli.py real-generation-start-gate`.
- `ENABLE_REAL_GENERATION=1 scripts/start_factory.sh` now writes `runs/sample_episode/real_generation_start_gate.json` before starting the live pipeline.
- The gate checks local blockers that do not require backend startup: `DASHSCOPE_API_KEY` and production character assets for visual stages.
- With the current local state, the enabled run exits before live pipeline startup with:
  - `DASHSCOPE_API_KEY is missing.`
  - `Character reference assets are not production-ready for visual generation.`
- Default `scripts/start_factory.sh` remains a dry-run deployment path and still completes with `demo_ready=true`.
- Video self-check: final preview remains H.264/AAC, 1080x1920, 45.056 seconds; refreshed 9-frame contact sheet shows no black frames or obvious subtitle overflow.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires production-ready role images in `runs/sample_episode/reviewed_role_images/`.

## 2026-07-10 — v1.34 Start Gate in Operator Handoff

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_operator_handoff.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
rg -n "Real Generation Start Gate|real_generation_start_gate|DASHSCOPE_API_KEY|Character reference assets" runs/sample_episode/operator_handoff.md runs/sample_episode/operator_handoff.json
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,duration -show_entries format=duration -of json output/sample_episode/final_preview.mp4
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,channels,duration -of json output/sample_episode/final_preview.mp4
```

Result:

- `125 passed`.
- `operator_handoff.json` now includes `real_generation_start_gate` with the gate path, existence, readiness, stages, and blocked reasons.
- `operator_handoff.md` now includes a `Real Generation Start Gate` section so the operator can see why `ENABLE_REAL_GENERATION=1` did not launch the live backend.
- The current handoff surfaces the two local blockers directly:
  - `DASHSCOPE_API_KEY is missing.`
  - `Character reference assets are not production-ready for visual generation.`
- Default `scripts/start_factory.sh` still completes as a dry-run deployment path with `demo_ready=true`.
- Video self-check: final preview remains H.264/AAC, 1080x1920, 45.056 seconds; refreshed 9-frame contact sheet shows no black frames or obvious subtitle overflow.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires production-ready role images in `runs/sample_episode/reviewed_role_images/`.

## 2026-07-10 — v1.35 Operator Unblock Checklist

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_operator_handoff.py::test_build_operator_handoff_summarizes_current_state_and_next_commands tests/test_operator_handoff.py::test_write_operator_handoff_outputs_json_and_markdown -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
rg -n "Current Unblock Checklist|unblock_checklist|PROJECT=sample_episode scripts/start_factory.sh|DASHSCOPE_API_KEY|reviewed_role_images" runs/sample_episode/operator_handoff.md runs/sample_episode/operator_handoff.json
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,duration -show_entries format=duration -of json output/sample_episode/final_preview.mp4
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,channels,duration -of json output/sample_episode/final_preview.mp4
```

Result:

- `125 passed`.
- Added `unblock_checklist` to `operator_handoff.json`.
- Added `Current Unblock Checklist` to `operator_handoff.md`.
- Current blockers now map to concrete targets and commands:
  - `DASHSCOPE_API_KEY` points to the LumenX `.env` setup command.
  - Reviewed role images point to `runs/sample_episode/reviewed_role_images/` and `PROJECT=sample_episode scripts/start_factory.sh`.
- Default `scripts/start_factory.sh` refreshed the handoff and still completes as a dry-run deployment path with `demo_ready=true`.
- Video self-check: final preview remains H.264/AAC, 1080x1920, 45.056 seconds; refreshed 9-frame contact sheet shows no black frames or obvious subtitle overflow, and the storyboard-card preview matches the 6-shot voiceover script.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires production-ready role images in `runs/sample_episode/reviewed_role_images/`.

## 2026-07-10 — v1.36 Local Factory Dashboard

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_dashboard.py tests/test_live_pipeline_script.py::test_start_factory_script_emits_dashboard_after_operator_handoff -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
rg -n "poster=|shot_001.png|Current unblock checklist|DASHSCOPE_API_KEY|PROJECT=sample_episode scripts/start_factory.sh" runs/sample_episode/factory_dashboard.html
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,duration -show_entries format=duration -of json output/sample_episode/final_preview.mp4
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,channels,duration -of json output/sample_episode/final_preview.mp4
```

Result:

- `129 passed`.
- Added `factory.dashboard` for a static local HTML control room.
- Added `factory_cli.py dashboard --project sample_episode`.
- `scripts/start_factory.sh` now refreshes `runs/sample_episode/factory_dashboard.html` after `operator-handoff`.
- Dashboard content is sourced from `operator_handoff.json` and shows the preview video, readiness state, start gate blockers, current unblock checklist, artifacts, and next commands.
- The dashboard video now uses `runs/sample_episode/cards/shot_001.png` as a poster when available, so the local page opens on a storyboard frame instead of a black video box.
- Browser verification: desktop 1280px and mobile 390px both render the dashboard with the preview video, poster, checklist, blocker text, and no horizontal overflow.
- Video self-check: final preview remains H.264/AAC, 1080x1920, 45.056 seconds; refreshed 9-frame contact sheet shows no black frames or obvious subtitle overflow.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires production-ready role images in `runs/sample_episode/reviewed_role_images/`.

## 2026-07-10 — v1.37 Real-Generation Failure Diagnostics

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_lumenx_generation_guard.py::test_guarded_generation_records_http_failure_details -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_operator_handoff.py::test_operator_handoff_summarizes_generation_failure_diagnostics tests/test_dashboard.py::test_build_factory_dashboard_renders_operator_state -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_lumenx_generation_guard.py tests/test_operator_handoff.py tests/test_dashboard.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
rg -n "Real Generation Diagnostics|real_generation_diagnostics|Failed stage|Request count|Last generation failure|lumenx_generation_report" runs/sample_episode/operator_handoff.md runs/sample_episode/operator_handoff.json runs/sample_episode/factory_dashboard.html
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,duration -show_entries format=duration -of json output/sample_episode/final_preview.mp4
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,channels,duration -of json output/sample_episode/final_preview.mp4
```

Result:

- `131 passed`.
- `lumenx_generation_report.json` now records failed real-generation endpoint attempts with `failed_stage`, request URL/path, HTTP status, response body, and `error_details`.
- HTTP failures now count as executed attempts, so reports distinguish "blocked before call" from "backend/provider call failed."
- `operator_handoff.json` and `.md` now include `real_generation_diagnostics`.
- `factory_dashboard.html` now surfaces the latest real-generation failure in a `Last generation failure` panel.
- Default `scripts/start_factory.sh` refreshed the dry-run package, handoff, and dashboard with `demo_ready=true`.
- Current dry-run handoff shows `Real Generation Diagnostics` with no failed stage and request count 0 because real generation is still gated before endpoint calls.
- Browser verification: desktop 1280px and mobile 390px still render the dashboard with the preview video, poster, checklist, blocker text, and no horizontal overflow.
- Video self-check: final preview remains H.264/AAC, 1080x1920, 45.0 seconds; refreshed 9-frame contact sheet shows no black frames or obvious subtitle overflow.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires production-ready role images in `runs/sample_episode/reviewed_role_images/`.

## 2026-07-10 — v1.38 Audio Smoke Report Refresh

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_live_pipeline_script.py::test_lumenx_audio_smoke_script_refreshes_operator_reports_after_attempt tests/test_live_pipeline_script.py::test_lumenx_audio_smoke_script_blocks_without_dashscope_key tests/test_live_pipeline_script.py::test_lumenx_audio_smoke_script_runs_audio_only_real_generation -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/run_lumenx_audio_smoke.sh
scripts/start_factory.sh
rg -n "factory_dashboard|Current unblock checklist|DASHSCOPE_API_KEY|Real Generation Diagnostics|lumenx_audio_smoke" runs/sample_episode/factory_dashboard.html runs/sample_episode/operator_handoff.md runs/sample_episode/lumenx_audio_smoke_preflight.json
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,duration -show_entries format=duration -of json output/sample_episode/final_preview.mp4
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,channels,duration -of json output/sample_episode/final_preview.mp4
```

Result:

- `132 passed`.
- `scripts/run_lumenx_audio_smoke.sh` now captures the live audio-only pipeline exit code.
- After an audio smoke backend/provider attempt, it refreshes `workflow-status`, `real-generation-preflight`, `operator-handoff`, and `dashboard`.
- The script exits with the original audio smoke status, so automation can still treat smoke failure as failure while the local reports stay current.
- With the current missing `DASHSCOPE_API_KEY`, `scripts/run_lumenx_audio_smoke.sh` exits `1` and writes `runs/sample_episode/lumenx_audio_smoke_preflight.json` with `blocked_reasons=["DASHSCOPE_API_KEY is missing."]`.
- Default `scripts/start_factory.sh` still refreshes the dry-run package, handoff, and dashboard with `demo_ready=true`.
- Video self-check: final preview remains H.264/AAC, 1080x1920, 45.056 seconds; refreshed 9-frame contact sheet shows no black frames or obvious subtitle overflow.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires production-ready role images in `runs/sample_episode/reviewed_role_images/`.

## 2026-07-10 — v1.39 Audio Smoke Missing-Key Refresh

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_live_pipeline_script.py::test_lumenx_audio_smoke_script_refreshes_reports_after_missing_key_without_stdout_noise tests/test_live_pipeline_script.py::test_lumenx_audio_smoke_script_refreshes_operator_reports_after_attempt tests/test_live_pipeline_script.py::test_lumenx_audio_smoke_script_blocks_without_dashscope_key -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/run_lumenx_audio_smoke.sh
scripts/start_factory.sh
rg -n "DASHSCOPE_API_KEY|lumenx_audio_smoke|Current unblock checklist|Real Generation Diagnostics|factory_dashboard" runs/sample_episode/factory_dashboard.html runs/sample_episode/operator_handoff.md runs/sample_episode/lumenx_audio_smoke_preflight.json
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,duration -show_entries format=duration -of json output/sample_episode/final_preview.mp4
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,channels,duration -of json output/sample_episode/final_preview.mp4
```

Result:

- Added `refresh_operator_reports` inside `scripts/run_lumenx_audio_smoke.sh`.
- Missing-key audio smoke exits still print only the smoke preflight JSON to stdout, preserving machine-readable behavior.
- When dry-run artifacts already exist, missing-key audio smoke now refreshes `workflow-status`, `real-generation-preflight`, `operator-handoff`, and `dashboard` silently before exiting.
- `133 passed`.
- With the current missing `DASHSCOPE_API_KEY`, `scripts/run_lumenx_audio_smoke.sh` exits `1` and stdout remains valid smoke JSON with `blocked_reasons=["DASHSCOPE_API_KEY is missing."]`.
- Default `scripts/start_factory.sh` refreshed the dry-run package, handoff, and dashboard with `demo_ready=true` and `goal_ready=false`.
- `operator_handoff.md` includes `Real Generation Diagnostics`; `factory_dashboard.html` includes the current blocker, unblock checklist, and audio smoke command.
- Chrome browser verification: desktop 1280px and mobile 390px both render the dashboard with the preview video, poster, checklist, `DASHSCOPE_API_KEY` blocker, audio smoke command, and no horizontal overflow.
- Video self-check: final preview remains H.264/AAC, 1080x1920, 45.056 seconds; refreshed 9-frame contact sheet shows no black frames or obvious subtitle overflow.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires production-ready role images in `runs/sample_episode/reviewed_role_images/`.

## 2026-07-10 — v1.40 Character Asset Status Actions

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_character_brief.py::test_build_character_assets_status_marks_ready_images_without_source_not_production_ready tests/test_character_brief.py::test_build_character_assets_status_suggests_drop_folder_for_missing_images -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_character_brief.py::test_cli_character_assets_status_writes_report -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_character_brief.py::test_build_character_assets_status_marks_ready_images_without_source_not_production_ready tests/test_character_brief.py::test_build_character_assets_status_suggests_drop_folder_for_missing_images tests/test_character_brief.py::test_cli_character_assets_status_writes_report -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py character-assets-status --brief runs/sample_episode/character_generation_brief.json --output runs/sample_episode/character_assets_status.json
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
rg -n "next_actions|confirm_existing_reference_images|install_reviewed_role_images|place_reviewed_role_images" runs/sample_episode/character_assets_status.json
```

Result:

- Added `next_actions` to `character_assets_status.json`.
- Missing or invalid role images now point automation at `runs/<project>/reviewed_role_images/`.
- Valid images without trusted provenance now expose both safe operator paths: confirm the existing target reference images, or install reviewed images from the drop folder.
- `factory_cli.py character-assets-status` stdout now includes the `next_actions` ids for machine-readable automation.
- `134 passed`.
- Default `scripts/start_factory.sh` refreshed the dry-run package, handoff, dashboard, and sample character asset status with `demo_ready=true` and `goal_ready=false`.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires production-ready role images in `runs/sample_episode/reviewed_role_images/` or an operator-confirmed `character_assets.confirmed.json`.

## 2026-07-10 — v1.41 Character Asset Actions in Handoff and Dashboard

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_operator_handoff.py::test_build_operator_handoff_summarizes_current_state_and_next_commands tests/test_operator_handoff.py::test_write_operator_handoff_outputs_json_and_markdown tests/test_dashboard.py::test_build_factory_dashboard_renders_operator_state -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_operator_handoff.py tests/test_dashboard.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
rg -n "Character Asset Actions|Character asset actions|confirm_existing_reference_images|install_reviewed_role_images|character_assets_status" runs/sample_episode/operator_handoff.md runs/sample_episode/operator_handoff.json runs/sample_episode/factory_dashboard.html
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py dashboard --project sample_episode
```

Result:

- `operator_handoff.json` now includes `character_assets_status` with status path, readiness flags, summary, and machine-readable `next_actions`.
- `operator_handoff.md` now includes a `Character Asset Actions` section with target paths and commands.
- `factory_dashboard.html` now renders a `Character asset actions` panel before the generic next commands.
- Fixed dashboard mobile overflow by allowing grid children, panels, and metrics to shrink within the viewport.
- `134 passed`.
- Chrome browser verification: desktop 1280px and mobile 390px both render the preview video and character asset actions; no horizontal overflow after the CSS fix.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires production-ready role images in `runs/sample_episode/reviewed_role_images/` or an operator-confirmed `character_assets.confirmed.json`.

## 2026-07-10 — v1.42 Mock LumenX Live Smoke

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_lumenx_mock_backend.py::test_lumenx_mock_live_pipeline_runs_handoff_against_http_backend -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_lumenx_mock_backend.py::test_cli_lumenx_mock_live_run_writes_success_report -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_live_pipeline_script.py::test_lumenx_mock_live_smoke_script_runs_mock_backend_and_refreshes_reports -q
scripts/run_lumenx_mock_live_smoke.sh
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_lumenx_mock_backend.py tests/test_operator_handoff.py tests/test_dashboard.py tests/test_live_pipeline_script.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
rg -n "Mock Live Smoke|Mock live smoke|lumenx_mock_live_run|18 requests|script_mock_1" runs/sample_episode/operator_handoff.md runs/sample_episode/operator_handoff.json runs/sample_episode/factory_dashboard.html runs/sample_episode/lumenx_mock_live_run.json
```

Result:

- Added `factory.lumenx_mock_backend` with a local mock LumenX HTTP server for `/health`, project creation, character image updates, scenes, frames, frame updates, and generation endpoint placeholders.
- Added `factory_cli.py lumenx-mock-live-run` and `scripts/run_lumenx_mock_live_smoke.sh`.
- The mock smoke runs the real live pipeline health and handoff executor against the mock backend, writes `runs/sample_episode/lumenx_mock_live_run.json`, then refreshes workflow status, handoff, preflight, and dashboard.
- Current sample mock smoke passed with 18 mock requests, 2 characters, 1 scene, and 6 frames.
- `operator_handoff.json`/`.md` and `factory_dashboard.html` now surface the latest Mock Live Smoke summary.
- `137 passed`.
- Chrome browser verification: desktop 1280px and mobile 390px both render the preview video, Mock live smoke, character asset actions, and current blockers with no horizontal overflow.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires production-ready role images in `runs/sample_episode/reviewed_role_images/` or an operator-confirmed `character_assets.confirmed.json`.

## 2026-07-10 — v1.43 Isolated Mock Smoke Artifacts

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_lumenx_mock_backend.py::test_lumenx_mock_live_pipeline_runs_handoff_against_http_backend -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_lumenx_mock_backend.py tests/test_lumenx_live_pipeline.py tests/test_end_to_end_readiness.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_operator_handoff.py::test_write_operator_handoff_outputs_json_and_markdown tests/test_dashboard.py::test_build_factory_dashboard_renders_operator_state -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_lumenx_mock_backend.py tests/test_lumenx_live_pipeline.py tests/test_end_to_end_readiness.py tests/test_operator_handoff.py tests/test_dashboard.py -q
scripts/run_lumenx_mock_live_smoke.sh
rg -n "LumenX source|Mock live smoke|18 requests|lumenx_mock_live_execution|lumenx_live_execution|lumenx_mock_generation_report" runs/sample_episode/operator_handoff.md runs/sample_episode/operator_handoff.json runs/sample_episode/factory_dashboard.html runs/sample_episode/lumenx_mock_live_run.json runs/lumenx_mock_readiness.json
```

Result:

- Mock live smoke now writes isolated reports: `runs/lumenx_mock_health.json`, `runs/sample_episode/lumenx_mock_live_execution.json`, `runs/sample_episode/lumenx_mock_generation_report.json`, and `runs/lumenx_mock_readiness.json`.
- The mock runner cleans up stale mock artifacts previously written to real live/generation/health paths, while leaving non-mock real reports alone.
- End-to-end readiness accepts the isolated mock live execution as local-demo evidence and labels its source as `mock`; real generation remains blocked until real credentials and production role images are available.
- Operator handoff and dashboard now display `LumenX source` so `script_mock_1` cannot be confused with a real backend script.
- Current sample mock smoke passed with 18 requests, 2 characters, 1 scene, 6 frames, `demo_ready=true`, and `goal_ready=false`.
- Chrome browser verification: desktop 1280px and mobile 390px both render the preview video, Mock live smoke, `LumenX source=mock`, and current blockers with no horizontal overflow.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires production-ready role images in `runs/sample_episode/reviewed_role_images/` or an operator-confirmed `character_assets.confirmed.json`.

## 2026-07-10 — v1.44 Reviewed Role Image Intake Report

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_character_brief.py::test_build_reviewed_role_images_intake_reports_partial_drop_folder_state tests/test_character_brief.py::test_cli_character_assets_reviewed_intake_writes_machine_readable_report tests/test_operator_handoff.py::test_build_operator_handoff_summarizes_current_state_and_next_commands tests/test_operator_handoff.py::test_write_operator_handoff_outputs_json_and_markdown tests/test_dashboard.py::test_build_factory_dashboard_renders_operator_state tests/test_live_pipeline_script.py::test_start_factory_script_writes_reviewed_role_images_intake_before_import -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_character_brief.py tests/test_operator_handoff.py tests/test_dashboard.py tests/test_live_pipeline_script.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
rg -n "Role Image Intake|Reviewed role image intake|Matched:|Missing:|place_missing_role_images|reviewed_role_images_intake" runs/sample_episode/operator_handoff.md runs/sample_episode/operator_handoff.json runs/sample_episode/factory_dashboard.html
scripts/run_lumenx_mock_live_smoke.sh
```

Result:

- Added `factory_cli.py character-assets-reviewed-intake`.
- The intake report writes `runs/sample_episode/reviewed_role_images_intake.json` with per-character `matched`, `missing`, `invalid`, or `ambiguous` status, expected filenames, candidate filenames, and machine-readable `next_actions`.
- `scripts/start_factory.sh` now refreshes the intake report on every run and only auto-imports/installs reviewed role images when intake is `ready=true`; incomplete or invalid draft images no longer stop handoff/dashboard refresh.
- Operator handoff now includes a `Role Image Intake` section and the dashboard now renders a `Role image intake` panel.
- Current sample intake reports `matched=0`, `missing=2`, `invalid=0`, `ambiguous=0`, `total=2` and points to `place_missing_role_images`.
- `141 passed`.
- Default `scripts/start_factory.sh` refreshed the dry-run package, handoff, dashboard, and final preview with `demo_ready=true` and `goal_ready=false`.
- Mock live smoke still passes with 18 requests, 2 characters, 1 scene, and 6 frames.
- Browser verification: desktop 1280px and mobile 390px both render the preview video, Role image intake, `place_missing_role_images`, `0 / 2 matched`, and current blockers with no horizontal overflow.
- Video self-check: final preview remains H.264/AAC, 1080x1920, 45.056 seconds; refreshed 9-frame contact sheet shows no black frames or obvious subtitle overflow.

Remaining:

- Real audio/video generation still requires `DASHSCOPE_API_KEY`.
- Visual generation still requires production-ready role images in `runs/sample_episode/reviewed_role_images/` or an operator-confirmed `character_assets.confirmed.json`.

## 2026-07-10 — v1.45 Production Role Images and Installed Intake State

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_character_brief.py::test_build_reviewed_role_images_intake_detects_already_installed_sources tests/test_live_pipeline_script.py::test_start_factory_script_refreshes_intake_after_installing_reviewed_images tests/test_operator_handoff.py::test_build_operator_handoff_summarizes_current_state_and_next_commands tests/test_dashboard.py::test_build_factory_dashboard_marks_reviewed_role_images_as_installed -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_character_brief.py tests/test_operator_handoff.py tests/test_dashboard.py tests/test_live_pipeline_script.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
scripts/run_lumenx_mock_live_smoke.sh
ffprobe -v error -show_entries stream=index,codec_name,codec_type,width,height,r_frame_rate,channels -show_entries format=duration,size -of json output/sample_episode/final_preview.mp4
```

Result:

- Generated production reference portraits for 林澈 and 苏眠 from `character_generation_brief.json` and placed them in `runs/sample_episode/reviewed_role_images/`.
- The intake report now distinguishes `ready` from `installed`, verifies installed target bytes against the reviewed source, and clears stale install actions after a successful copy.
- Replacing a reviewed image at the same path correctly returns `installed=false` until the updated bytes are installed.
- `scripts/start_factory.sh` refreshes intake after installation; the sample now reports `ready=true`, `installed=true`, `matched=2`, and no character asset action.
- `character_assets.json` now reports `asset_ready=true` and `production_ready=true` for both characters.
- Mock LumenX smoke passed with 18 requests, 2 character image bindings, 1 scene, and 6 frames.
- `144 passed` before the following preview-renderer iteration.

Remaining:

- Real LumenX audio/video generation still requires `DASHSCOPE_API_KEY`.

## 2026-07-10 — v1.46 Character-Aware Local Preview

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_shot_card_renderer.py::test_render_shot_cards_uses_production_character_references -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_shot_card_renderer.py tests/test_character_assets.py tests/test_openmontage_adapter.py -q
scripts/start_factory.sh
ffmpeg -v error -y -i output/sample_episode/final_preview.mp4 -vf "fps=1/5,scale=270:480:force_original_aspect_ratio=decrease,pad=270:480:(ow-iw)/2:(oh-ih)/2:black,tile=3x3:padding=8:margin=8" -frames:v 1 runs/sample_episode/final_preview_contact_sheet_v146.jpg
ffprobe -v error -show_entries stream=index,codec_name,codec_type,width,height,r_frame_rate,channels -show_entries format=duration,size -of json output/sample_episode/final_preview.mp4
```

Result:

- Production-ready character references now render into the upper half of every local storyboard card; the original text-only card remains the fallback when trusted role assets are unavailable.
- The main `plan` path passes resolved `character_assets.json` into the card renderer before local voiceover and OpenMontage caption burn.
- Current final preview is H.264/AAC, 1080x1920, 30 fps, 45.056 seconds, with both role portraits, readable storyboard text, voiceover, and hard subtitles.
- Video self-check: refreshed 9-frame contact sheet covers the complete cut and shows no black frames, broken crops, subtitle collisions, or text overflow.
- `145 passed` inside the final `scripts/start_factory.sh` run.
- In-app browser `file://` navigation was blocked by browser security policy; dashboard rendering remains covered by the installed-state HTML unit test, and the prior desktop/mobile verification remains applicable because this iteration did not change dashboard layout CSS.

Remaining:

- The local preview uses static role reference portraits; scene-specific AI images and motion clips require real LumenX generation.
- Real LumenX audio/video generation still requires `DASHSCOPE_API_KEY`.

## 2026-07-13 — v1.47 Capability-Aware Gateway Routing

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py provider-report
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py env-report
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py gateway-text-smoke
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py gateway-image --prompt no-cost-check --output /tmp/manju-gateway-image.png
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py gateway-video-probe --project sample_episode
scripts/run_lumenx_audio_smoke.sh
```

Result:

- Added capability-scoped routing for gateway text (`qwen3.6-plus`), gateway image (`qwen-image-2.0`), local video, and Doubao/local audio.
- LumenX child startup maps gateway text settings to its OpenAI-compatible environment without modifying the vendored checkout or printing credentials.
- Start gates, preflight, generation guard, handoff, workflow status, operator handoff, and audio smoke now report the selected provider instead of applying a global DashScope requirement.
- Gateway/local stages are delegated to the factory and are not posted to incompatible LumenX DashScope endpoints; explicit legacy DashScope routing remains covered.
- Fixed a credential-boundary bug: OpenMontage's unrelated `OPENAI_API_KEY` can no longer be selected as an Onion gateway credential. Only its Doubao Speech settings are reused.
- The ignored factory `.env` selects gateway text/image, local video, and automatic TTS. Sanitized reports currently show gateway text/image blocked only by missing `GATEWAY_API_KEY`; Doubao audio and local video are ready.
- Text, image, and video gateway commands all reported `executed=false` without `--enable-live`; no paid cloud call was made.
- Audio smoke resolved `provider=doubao`, `executor=factory`, and skipped LumenX startup.
- Shell syntax checks and Python compile checks passed.
- `193 passed`.

Remaining:

- Add a real gateway token to the ignored factory `.env`, then run the explicitly enabled text smoke and one low-cost character image generation.
- The verified gateway image endpoint does not document reference-image input, so reference-dependent storyboard generation remains degraded to reviewed-role compositing.
- Gateway video remains disabled until an explicitly enabled probe confirms the response contract; character-reference video input is still unverified.

## 2026-07-13 — v1.48 Gateway Seedance Video Pipeline

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_gateway_video.py tests/test_gateway_video_batch.py tests/test_provider_profile.py tests/test_cli_gateway.py tests/test_lumenx_generation_guard.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py provider-report
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py gateway-video-batch --project sample_episode --limit 1
```

Result:

- Replaced the incorrect chat-style video probe payload with the New API video contract: `prompt`, `images`, `duration`, and Doubao metadata.
- Added asynchronous task polling, completed-video URL extraction from `metadata.url`, bounded download, atomic MP4 output, and secret/signed-URL redaction.
- Added local PNG/JPEG/WebP role-reference encoding and per-shot role mapping from LumenX frames.
- Added `gateway-video-generate` for one clip and `gateway-video-batch` for storyboard batches; both remain network-disabled unless explicitly enabled.
- Gateway batches write directly to each OpenMontage `expected_assets.video_clip` path.
- Gateway batches resume by skipping existing non-empty clips; `--overwrite` explicitly opts into regeneration.
- `lumenx-generate-live` now executes a selected gateway video stage instead of treating factory delegation as completed work.
- Default video routing is now `doubao-seedance-2-0-fast`; the provider is blocked only when `GATEWAY_API_KEY` is absent or gateway video is disabled.
- Sample dry-run planned `shot_001` with both reviewed role references, 8 seconds, 9:16, 720p, and zero network calls.
- `45 passed` in the focused gateway/provider/guard suite and `210 passed` in the full test suite.
- Python compile checks and `git diff --check` passed.

Remaining:

- Add `GATEWAY_API_KEY` to the ignored factory `.env`, then explicitly generate one validation clip with `--limit 1 --enable-live` before running the full six-shot batch.

## 2026-07-13 — v1.49 Gateway Video Safety and Resume Hardening

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_gateway_video.py tests/test_gateway_video_batch.py tests/test_cli_gateway.py tests/test_provider_profile.py tests/test_lumenx_generation_guard.py tests/test_operator_handoff.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m compileall -q factory factory_cli.py
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py provider-report
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py gateway-video-generate --model doubao-seedance-2-0 --prompt "国漫角色走入雨夜车站，镜头缓慢推进" --duration 8 --resolution 1080p --output /tmp/manju-review-single.mp4 --report-output /tmp/manju-review-single-report.json
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py gateway-video-batch --project sample_episode --model doubao-seedance-2-0 --resolution 1080p --limit 1 --output /tmp/manju-review-batch.json
```

Result:

- Added exact New API nested task-envelope parsing, including `result_url`, `fail_reason`, and failed tasks wrapped under `data` or `output`.
- Single and batch commands now share provenance signatures, per-clip locks, submitted-task resume, immediate-URL resume, atomic private state, and strict stale/corrupt-state rejection.
- Deterministic request construction and local validation now complete before writing `submitting`; oversized request bodies, invalid references, invalid output/report paths, and unwritable report directories cannot leave a false ambiguous-charge claim or contact the provider.
- Input handoff/package schemas, project IDs, production role assets, timeline/frame indexes, character IDs, shot IDs, output uniqueness, MP4 suffixes, Seedance duration/reference limits, and model-specific resolutions are validated before execution.
- MP4 downloads are bounded and streamed, verify `Content-Length`, require complete top-level `ftyp`/`moov`/`mdat` boxes, and replace the final output atomically. Signed URLs and remote reference paths are removed from reports and errors.
- Unknown text, image, video, and TTS providers now fail closed instead of silently switching to another billable provider.
- `gateway-video-probe` is explicitly submission-only and potentially billable; a queued response is no longer labeled production-ready.
- The focused gateway/provider/handoff suite passed `109` tests; the full factory suite passed `274` tests. Python compile checks and `git diff --check` passed.
- Standard `doubao-seedance-2-0` dry runs accepted one 1080p single clip and the first storyboard clip. The batch plan bound both reviewed role references, used 8 seconds at 9:16, and made zero network calls; no MP4 or resume state was created.

Remaining:

- No `GATEWAY_API_KEY` is present, so no paid task was submitted. The first explicitly enabled one-clip run must still verify the hosted gateway's acceptance of local Data URI role references, task polling, downloaded codec/visual quality, and audio behavior before a six-shot batch.

## 2026-07-13 — v1.50 First Live Seedance Clip and Hybrid OpenMontage Preview

Commands:

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_gateway_video.py tests/test_gateway_video_batch.py tests/test_cli_gateway.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py gateway-video-batch --project sample_episode --model doubao-seedance-2-0-fast --resolution 720p --limit 1 --submit-timeout 300 --overwrite --enable-live
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py refresh-preview --project sample_episode
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python /Users/tml/.codex/skills/video-edit-self-check/scripts/video_preflight.py output/sample_episode/final_preview.mp4
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
```

Result:

- Configured the Onion gateway credential only in the ignored local `.env`; tracked files and reports contain no secret value.
- The original 60-second submission timeout expired before the gateway returned its first response. Read-only token logs and the authenticated task dashboard confirmed that attempt created no task and incurred no video charge before retry.
- Added a dedicated 300-second submission timeout while preserving the 60-second polling timeout and 900-second total task wait. The second run issued exactly one paid POST, persisted its task ID, resumed through polling, and completed successfully.
- Verified the hosted `doubao-seedance-2-0-fast` route accepts both reviewed character references as Data URIs. `shot_001.mp4` is H.264, 720x1280, 8.042 seconds, 4,796,199 bytes, and intentionally has no embedded audio because dialogue is mixed in post.
- Visual inspection confirmed the requested rain-soaked old district, convenience-store entrance, both consistent characters, and black envelope. The 9-frame sheet showed motion across the full clip with no blank or broken frame.
- Added `refresh-preview`, which uses valid generated MP4s per shot and character storyboard cards as fallbacks, reuses the existing voiceover without a TTS request, and sends the result through OpenMontage caption burn.
- The refreshed final preview uses one dynamic Seedance shot and five storyboard fallbacks. OpenMontage reported `openmontage_remotion_caption_burn` with no fallback; the result is H.264/AAC, 1080x1920, 30 fps, and 45.0 seconds.
- Final video preflight returned no warnings. Black-frame detection found no black segment; audio measured `-18.0 dB` mean and `-3.1 dB` peak; the full contact sheet showed readable captions without face or layout collisions.
- Added hybrid source-selection, FFmpeg normalization, project refresh, CLI, timeout, and failure-path tests. The full factory suite passed `283` tests.

Remaining:

- Five storyboard shots still use the zero-cost character-card fallback. Generating them with `--limit 0` is an explicit paid action and is not started automatically.
- The gateway image endpoint still has no verified role-reference contract; character-consistent still generation remains on the reviewed-reference fallback path.

## 2026-07-13 — v1.51 Production Hardening and Persistent Worker

Result:

- Added restart-safe Doubao TTS cue states, task-ID resume, role voice mapping, actual `doubao/local/mixed` reporting, audio validation, and atomic downloads.
- Made gateway generation a first-class readiness source; workflow, handoff, dashboard, and CLI refresh now agree on completed/expected dynamic shots.
- Replaced box-only MP4 acceptance with bounded `ffprobe` stream validation. Hybrid renders, voiceover mixes/muxes, OpenMontage finalization, and TTS downloads publish through sibling temporary files so failures preserve the last good artifact.
- Added generic `run-project`, idempotent `enqueue`, and one-shot/continuous `worker` commands. Queue claims use a POSIX lock and atomic moves, while project runs leave terminal reports and reject traversal project IDs.
- Added `scripts/bootstrap_factory.sh`, `scripts/start_worker.sh`, pinned dependencies, and removed the runtime dependency on OpenMontage's virtualenv.
- A real no-cost Worker job completed for `worker_smoke_20260713`. Its final preview has valid H.264/AAC streams and a 22.592-second duration; the control-plane refresh completed successfully.
- Installed the project `.venv`; the full suite passed `314` tests at bootstrap, and Ruff passed with no findings after runtime fixes.

Remaining:

- The sample production episode still has five storyboard-card fallbacks until the explicitly billable full Seedance batch is resumed.
- Distinct Doubao role voices require authorized voice IDs in `DOUBAO_SPEECH_VOICE_MAP`; the code no longer invents provider voice IDs.

## 2026-07-13 — v1.52 Complete Six-Shot Production Cut and Final Hardening

Commands:

```bash
.venv/bin/python factory_cli.py run-project --input samples/sample_novel.txt --project sample_episode --title 旧城来信 --shots 8 --character-assets runs/sample_episode/character_assets.confirmed.json
.venv/bin/python factory_cli.py refresh-preview --project sample_episode
.venv/bin/python factory_cli.py readiness --project sample_episode
.venv/bin/python factory_cli.py workflow-status --project sample_episode
.venv/bin/python factory_cli.py real-generation-preflight --project sample_episode
.venv/bin/python factory_cli.py operator-handoff --project sample_episode
.venv/bin/python /Users/tml/.codex/skills/video-edit-self-check/scripts/video_preflight.py output/sample_episode/final_preview.mp4
.venv/bin/pytest -q
.venv/bin/ruff check factory tests factory_cli.py
npx tsc --noEmit
```

Result:

- Completed and validated all six `doubao-seedance-2-0-fast` 720p source clips. The final OpenMontage preview reports six dynamic shots and zero storyboard fallbacks.
- Added validated project-level edit decisions. The sample suppresses one duplicate source/generated subtitle cue, removes the malformed `shot_004` double-face transition, and freezes `shot_006` before its source-generated caption appears. Canonical `subtitles.srt` remains unchanged; rendering uses the derived `subtitles.render.srt`.
- Fixed OpenMontage caption timing so each caption page ends at its own `endMs` instead of lingering until the next cue. Added a direct Node regression test and corrected the remaining Remotion TypeScript boundaries; `npx tsc --noEmit` now exits successfully.
- Added project artifact transactions so failed reruns restore the last-good episode, media, reports, and final preview. Hybrid/OpenMontage command crashes also preserve prior delivery output.
- Corrected voiceover provider reporting by separating provider-selection source from Doubao credential source. The current sample truthfully reports local TTS, 12 local clips, zero cloud clips, and no errors or warnings.
- The control plane now refreshes the provider-aware start gate before handoff generation. A goal-ready project no longer exposes stale DashScope, role-image, or audio-smoke actions. Current readiness, workflow, start gate, preflight, handoff, and dashboard all report ready with no blockers or next steps.
- Added repository `pytest.ini`, edit-decision tests, rollback tests, caption fallback tests, and control-plane consistency tests. The complete factory suite passed `327` tests; Ruff, Python compile, shell syntax, and diff whitespace checks passed.
- Final media preflight returned no warnings: H.264/AAC, 1080x1920, 30 fps, 45.056 seconds, one stereo audio stream. Full-cut visual inspection found no black/broken frames, duplicate captions, face collision, or text overflow. No black segment or silence longer than one second was detected at the selected thresholds; audio measured `-18.1 dB` mean and `-3.1 dB` peak.
- Secret scan found no gateway/Speech tokens in source or documentation. The ignored factory and OpenMontage environment files are both mode `0600`.

Remaining:

- No blocker remains for the working local production route. Cloud Doubao role voices are an optional upgrade and still require a separately authorized Speech X-Api-Key plus valid voice IDs; the Onion gateway token cannot replace that credential.
- The gateway image endpoint still lacks a verified role-reference contract, so the production route continues to use reviewed character references directly with Seedance video.

## 2026-07-14 — v1.53 Perceptual Audio and Motion-Cadence Fix

Result:

- Reproduced the reported voice collision: the prior 45-second cut placed two cues at fixed offsets even when the first local narration lasted longer than its slot. Several cues therefore overlapped by construction.
- Removed invented filler dialogue and duplicate narrator/dialogue readings. The sample now contains seven source-backed cues across six duration-aware shots instead of twelve cues forced into fixed 7.5-second shots.
- Local speech now uses Mainland Mandarin voices at rate `165`, sends only the line text to the speech engine, probes each rendered clip, and schedules it sequentially from measured duration. A cue that cannot fit its shot fails closed instead of being mixed over another speaker.
- Canonical subtitles are rewritten from measured voice boundaries. The current provider report records seven local clips, zero Doubao clips, distinct narrator/character voices, and `timing_overlap_count=0`.
- Added `motionCadenceFps=12`: Seedance motion is sampled at 12 fps and duplicated into the 30 fps delivery stream, producing a deliberate motion-comic cadence without changing platform compatibility.
- Moved the `shot_002` stop point to 3.2 seconds after frame-level inspection found its generated white caption began near 3.4 seconds. The final 16–18 second inspection shows only the OpenMontage subtitle layer.
- Re-rendered the full six-shot cut. Technical preflight reports H.264/AAC, 1080x1920, one audio stream, 61.077 seconds, and no warnings; full-contact-sheet inspection found no black/broken frame, source/generated subtitle collision, or text overflow.
- The complete factory suite passed `331` tests. Ruff, Python compilation, shell syntax, and diff whitespace checks also passed.

Remaining:

- The current cut intentionally uses macOS local speech, not Doubao Speech. Cloud TTS remains an optional provider switch that requires a separately authorized Speech X-Api-Key and valid voice IDs.

## 2026-07-15 — v1.54 Verified Doubao TTS and Audio Mastering

Result:

- Confirmed the Onion OpenMontage sample had previously generated three real `seed-tts-2.0` MP3 files through the Speech V3 unidirectional endpoint. Its current `.env` entries were empty with inline comments; the old dotenv parser had incorrectly treated those comments as credentials.
- Added quote-aware inline-comment parsing and regression coverage so empty dotenv values remain empty. Provider readiness no longer reports a comment as a key or voice ID.
- Added compatibility for both new-console `DOUBAO_SPEECH_API_KEY` authentication and the verified legacy `DOUBAO_TTS_APPID` + `DOUBAO_TTS_ACCESS_KEY` streaming route. Streaming audio and metadata publish atomically, response audio is validated, secrets are redacted, and generated metadata excludes credentials.
- Securely restored the verified local Speech credentials into the ignored factory `.env` with mode `0600`; no tracked source, report, or generated metadata contains either value. The gateway was queried directly and currently exposes no TTS model, so its token is not reused for speech.
- Generated all seven episode cues with `seed-tts-2.0`, voice `zh_female_vv_uranus_bigtts`, performance context, and speech rate `-4`. The provider report records seven Doubao clips, zero local clips, zero timing overlaps, and no provider errors.
- The first `-8` pacing attempt exceeded `shot_004` and was rejected before final publication. At `-4`, every measured clip fits its shot with the configured lead, inter-speaker gap, and tail clearance.
- Added `-16 LUFS` / `-1.5 dBTP` speech mastering and 48 kHz stereo delivery. Final measurement is `-15.97 LUFS`; media preflight reports H.264/AAC, 1080x1920, one audio stream, 61.056 seconds, and no warnings.
- Rebalanced subtitle chunks when a page would otherwise contain fewer than four characters. The former single `封。` tail now renders as `有寄件人的黑色信封。` without face collision or overflow.
- Preserved the in-progress micro-shot quality artifacts. The Doubao audio refresh uses the last approved six-shot hybrid visual cut and records that decision in `audio_refresh_report.json` instead of weakening the incomplete-quality-path guard.

Remaining:

- The account currently has one verified Doubao voice. Narrator and Su Mian therefore share that voice and `role_voice_distinct=false` is reported honestly; distinct roles require separately authorized voice IDs in `DOUBAO_SPEECH_VOICE_MAP`.
- The new micro-shot visual timeline still requires paid model bakeoff candidates, visual QC, and a complete selection before the quality-path renderer can replace the approved six-shot cut.

## 2026-07-15 — v1.55 Micro-Shot Quality Production Path

Problem:

- The six-shot cut put several scenes, characters, actions, and transitions into each long model request.
- Source clips contained neutral performances, in-model cuts, generated text, a duplicate face, and continuous camera drift.
- Trimming and freezing could hide visible failures but could not make the source candidates production-quality.

Decision and implementation:

- Added a separate 19-item `visual_timeline` so long narration can span short visual beats.
- Added strict performance planning and prompt compilation with one scene, one action, exact role participation, observable expression changes, and canonical negative constraints.
- Added production video/still candidate batches, resumable candidate state, a paid model bakeoff gate, OCR/scene/motion visual QC, exact visual selection, and a variable-cadence micro-preview renderer.
- Restricted formal character candidates to `doubao-seedance-2-0` and `doubao-seedance-1-5-pro`; `doubao-seedance-2-0-fast` remains a draft route.
- Added an incomplete-quality-path guard so a dry visual timeline cannot accidentally overwrite the last approved preview.

Effect:

- `visual_timeline.json` validates 19 micro-shots and compiles all production prompts.
- `model_bakeoff_plan.json` selects `micro_005`, `micro_017`, and `micro_003` as representative samples.
- No paid bakeoff or batch generation was run during the architecture phase.

Next:

- Generate and score representative paid candidates before allowing any full 19-shot batch.

## 2026-07-17 — v1.56 Multi-Role Doubao Voices and Boundary Trim

Problem:

- The verified Doubao cut used one voice for both narration and Su Mian.
- Seed-TTS clips contained about 0.4 seconds of leading/trailing silence, which distorted slot-fit calculations.

Decision and implementation:

- Tested six official Seed-TTS 2.0 voices and retained all successful probes.
- Assigned Flow female to narration, Vivi 2.0 to Su Mian, and configured Ruyayichen for Lin Che.
- Added silence-boundary trimming before duration scheduling and retained atomic provider caches.
- Produced a six-voice comparison, a three-role dialogue demo, and an updated full cut in one delivery folder.

Effect:

- Current episode report contains seven Doubao clips, zero local clips, zero overlaps, and distinct active role voices.
- Lin Che has no dialogue in the current episode, so his configured voice is demonstrated in the three-role sample.
- Final audio measures about -16 LUFS.
- The full factory suite passed 1,612 tests after the voice update.

Delivery:

- `output/sample_episode/多音色试听交付/`

## 2026-07-17 — v1.57 Truthful Quality Readiness and Baseline Audit

Problem:

- `workflow_status.json` reported `goal_ready=true` even though the micro-shot path had no bakeoff report, visual selection, or micro preview.
- `status.json` still claimed seven local TTS clips after the successful Doubao audio refresh.
- Technical preflight had no warnings, but source-level visual failures remained.

Reasoning:

- Real video coverage is not equivalent to completing the active quality target.
- Once any quality-path artifact exists, the system must require the complete bakeoff, selection, micro-preview, and final quality render chain.
- Voiceover truth should come from the current provider report, not an older project-plan snapshot.

Implementation:

- Added an active quality-path readiness check and a dedicated `demo_ready_blocked_for_quality_upgrade` state.
- Added the `complete_quality_upgrade` operator action with exact missing artifacts.
- Made workflow-status writes atomically synchronize current provider, clip counts, role distinction, and overlap count into `status.json`.
- Added `docs/quality-iteration-handbook.md` as the structured historical and future iteration record.

Verification:

- Focused readiness/workflow regression suite: 19 passed.
- Current state: `demo_ready=true`, `goal_ready=false`, only `quality_upgrade_path` is blocking.
- Current provider truth: Doubao 7, local 0, role voices distinct, overlap count 0.
- Current final media: H.264/AAC, 1080x1920, 30 fps, 61.033 seconds; preflight warnings 0.
- Content audit still detects long freezes, one 0.1-second black segment, source text, in-model cuts, and the `shot_004` duplicate face.

Next:

- Add first-class CLI orchestration for the quality path, run the paid representative model bakeoff, and generate only candidates that pass per-shot QC.

## 2026-07-17 — v1.58 Live Bakeoff Diagnostics and Targeted Candidate Iteration

Problem:

- The initial bakeoff treated every model visible in `/v1/models` as production-compatible.
- Provider HTTP failures retained only status codes, making the 1.5 Pro video and Seedream still failures indistinguishable.
- The first successful still embedded prompt-derived text and a provider watermark.
- The representative ticket shot repeatedly generated fake printing even with the canonical no-text constraint.

Reasoning:

- A model alias being listed proves endpoint routing, not support for the required reference-image task.
- Deterministic HTTP rejection, ambiguous process interruption, provider timeout, and failed visual content require different retry decisions.
- Text-prone props need composition changes; repeating negative words is not a sufficient iteration strategy.

Implementation:

- Added bounded, allow-listed provider error detail extraction with credential, URL, signature, and data-image redaction.
- Persist deterministic HTTP rejection as `rejected` with its status code while preserving ambiguous `submitting` states for manual task lookup.
- Confirmed the gateway lists all four requested aliases, then captured the actual 1.5 Pro rejection: its adapter does not support the `r2v` task used by character references.
- Restricted character production to `doubao-seedance-2-0`; 1.5 Pro remains experimental.
- Split still-image sizes by model. Seedream now uses `1440x2560`; GPT Image keeps `1024x1536`.
- Added `watermark=false` for Seedream and removed `9:16` / subtitle-safe-area labels from still prompts.
- Moved GPT Image to experimental after a 300-second attempt returned HTTP 504.
- Reduced OCR false positives from isolated short shape noise while retaining real multi-character text detection.
- Added `quality-bakeoff-candidates`, `quality-visual-qc`, and `quality-finalize-bakeoff` CLI stages.
- Recorded every visual-plan revision in `visual_plan_review.json`.

Effect:

- `micro_017` candidate 1 passes automatic QC and manual review at 94.
- Seedream `micro_003` candidate 2 has no readable text or provider watermark and is the current passing still candidate.
- `micro_005` candidates 1 and 2 remain correctly rejected for embedded ticket text; the final allowed candidate changes the composition to show only the paper edge.
- Production and experimental model lists now reflect observed capabilities instead of optimistic aliases.

Verification:

- Quality, model, prompt, gateway, batch, and QC regression suite: 816 passed.
- Ruff and `git diff --check`: passed.
- Candidate 3 completed and was inspected through a 9-frame full-duration contact sheet. It again generated a complete printed ticket despite the paper-edge-only composition; automatic OCR and manual review both rejected it for `embedded_text`.

Next:

- Stop billing `micro_005` after its three-candidate limit. Route ticket close-ups through a no-text still or post-composited prop, replace the second video representative with dual-character `micro_018`, then finalize the production model gate before any full batch.

## 2026-07-17 — v1.59 Resumable Production Controls and Dual-Role Revision

Problem:

- The quality modules had no first-class CLI for the full production batch or complete visual selection.
- A transient TLS EOF interrupted polling after `micro_018` had already been submitted.
- The first dual-role candidate preserved both identities but expanded the story letter into a printed page with fake text and did not show a clear gaze shift.

Reasoning:

- Batch generation must consume the selected bakeoff models and fail before all network calls when that gate is incomplete.
- A transport interruption during polling is recoverable and must not be treated as a new paid candidate.
- The dual-role identity test is still valuable; the text-bearing prop and observable gaze action should be redesigned independently.

Implementation:

- Added `quality-production-candidates` with a zero-network default, selected-model gate, candidate cap, route/limit controls, resumable rendering, and secret-safe reports.
- Added `quality-select`, which requires an input inside the project run directory and reuses the existing per-shot QC, candidate fingerprint, complete-coverage, and bakeoff validation before atomic publication.
- Added bounded retries for at most three consecutive poll transport failures while keeping deterministic HTTP, invalid response, and provider task failures fail-fast.
- Resumed the original `micro_018` task by its stored task ID; no new submission was made.
- Rejected `micro_018` candidate 1 for `embedded_text` and changed candidate 2 to show only the thin edge of a closed black envelope at the bottom of frame with a concrete downward gaze shift.
- Rejected Seedream `micro_003` candidate 2 for `composition_mismatch`: it removed text and watermark but showed active rainfall despite `雨停后` and `no_rain`. Candidate 3 now states that rain has fully stopped and permits only residual counter droplets.
- Rejected `micro_003` candidate 3 for the same active-rainfall mismatch. The shot reached its three-candidate cap and was removed from the bakeoff; it now requires post cleanup or a redesigned route. The still representative moves to text-free, weather-free `micro_011`.

Verification:

- Production runner and CLI tests: 7 passed.
- Gateway video and resumable batch tests: 85 passed.
- Ruff, compileall, and `git diff --check`: passed at this stage.

Next:

- Generate and review `micro_018` candidate 2. If it passes, finalize the production model report and start candidate 1 for the remaining micro-shots through the new guarded command.

## 2026-07-17 — v1.60 OCR Precision and Observable Dual-Role Acting

Problem:

- `micro_018` candidate 2 removed the printed letter, but OCR treated clothing and envelope edges as random English fragments and reported `embedded_text`.
- Once the false positive was removed by inspection, the clip still failed creatively: both characters mostly faced the viewer, the requested gaze shift was barely visible, and the result read as a gently drifting illustration.

Reasoning:

- Automatic OCR should catch clear, high-confidence text; low-confidence fragments reconstructed from line art must not become a hard failure.
- Passing text and identity checks is not enough. The planned action must be readable at the first/last-frame level, otherwise the candidate should fail on its score.
- A gaze target below frame still encourages the model to preserve the front-facing reference pose. A second visible character is a more concrete, text-free target.

Implementation:

- Raised the default Tesseract token confidence threshold from 60 to 80 and added a regression fixture based on the observed `we`, `AN`, and `ALN` false detections.
- Re-ran candidate 2 QC. Automatic checks now pass with no text or cut failure; manual review remains failed at 74 because expression, semantics, and motion do not meet the production threshold.
- Recorded the candidate review before changing the timeline so its evidence hash remains bound to the exact failed design.
- Redesigned candidate 3 to contain no letter or envelope. Its single action is Lin Che shifting his eyes from straight ahead toward Su Mian while Su Mian remains still with lowered gaze.

Verification:

- OCR regression slice: 3 passed.
- Ruff for the modified QC implementation and tests: passed.
- Candidate 2 refreshed automatic QC: passed; manual production gate: failed as intended.
- Candidate 3 technical preflight: H.264, 1080x1920, 24 fps, 4.042 seconds, no audio stream.
- Candidate 3 automatic QC: no text, cut, or black-frame failure. Nine-frame manual review passed at 88 with stable dual identities and a readable gaze turn.

Next:

- Generate the `micro_011` Seedream still representative, then finalize the bakeoff only if both model routes meet the threshold.

Static representative update:

- `micro_011` candidate 1 rendered at 1440x2560 with a structurally sound loudspeaker and no provider watermark.
- It failed at 45 with `embedded_text` and `composition_mismatch`: a clear ticket-hall sign appeared below the loudspeaker and a row of people appeared at frame right.
- Candidate 2 changes the shot from a hall establishing view to a loudspeaker close-up occupying ninety percent of frame, with only a dark ceiling behind it.
- Candidate 2 removed all people, text, and watermark while preserving clean loudspeaker geometry. It passed at 92; the remaining deviation is a brighter lower window frame and a roughly two-thirds subject share.

Production preflight update:

- Added exact `--micro-shot` targeting to the guarded production command with unknown-ID, duplicate-ID, route mismatch, and zero-unselected-render checks.
- The first full dry-run found 19 routable jobs but also exposed repeated text-prone props in the production timeline.
- Replaced exhausted `micro_003` and `micro_005` designs with new IDs `micro_003r` and `micro_005r`, preserving all old failed candidates.
- Removed ticket surfaces from the remaining Su Mian shots, replaced the cinema sign with an unlettered lightbox, and replaced the poster image with blank frosted glass and reflected light.
- The source narration and subtitles retain the ticket, time-travel, and poster information; the generated visuals now carry reaction, atmosphere, and spatial continuity without asking the model to render text-bearing props.

## 2026-07-17 — v1.61 Audited Production Batch and Precision QC

Problem:

- The selected Seedream route still generated active rain for `micro_001` and `micro_003r` after each shot reached its three-candidate limit.
- `quality-production-candidates --kind still` treated every video shot as an explicit route error when no shot IDs were supplied.
- Seedance 2.0 rejected a three-second reference-video request even though the timeline correctly asked for a short establishing beat.
- One completed Seedance task failed only while downloading because the CDN response body did not match its declared `Content-Length`.
- Tesseract treated `Wii`, `IN`, and repeated cardigan buttonhole shapes recognized as `iff` as embedded text.

Reasoning:

- Candidate limits apply to model generations, not to transparent editorial transforms of already reviewed local assets. A crop or frame extraction is acceptable only when its source, operation, coordinates, source hash, and output hash remain auditable.
- Route filters, provider duration compatibility, task completion, download integrity, and visual content are separate gates. Fixing one must not weaken the others.
- OCR should require enough text evidence to distinguish typography from line art, while the nine-frame human review remains responsible for short logos and ambiguous marks.

Implementation:

- Added an exact-schema `editorial_still` route. It accepts only project-local `copy`, `crop_scale`, or `extract_frame` operations with immutable source and output fingerprints, and it cannot replace character video.
- Reused the clean frosted-glass portion of passed `micro_010` for `micro_001`; cropped the clean envelope center from exhausted `micro_003r` candidate 3. Failed generated candidates remain untouched.
- Changed default `--kind` behavior to filter the full timeline automatically. Explicit shot IDs still fail on unknown IDs, duplicates, or route mismatch.
- Clamp source video duration to the provider-supported minimum of four seconds while preserving the shorter editorial timeline duration.
- Kept strict download length validation but retry a completed task URL up to three times. The failed `micro_006` download resumed from its original task ID and succeeded without a new submission.
- OCR now needs at least four high-confidence normalized characters in a crop and confirmation in two distinct sampled frames. One-frame `Wii` / `IN` and repeated three-letter `iff` texture noise no longer hard-fail; persistent `SALE` and sentence text still do.

Effect:

- Static production selections now cover all seven static micro-shots. The two editorial derivatives contain no active rain, generated text, watermark, or cleanup ghosting and retain full provenance.
- `micro_002`, `micro_004`, `micro_005r`, `micro_006`, and `micro_012` candidate 1 passed automatic and manual video QC at 92, 92, 94, 86, and 98 respectively.
- `micro_006` remains a low-priority replacement candidate because its ending reads as surprise more than suppressed frowning; passing the threshold does not erase that note.
- Focused gateway, production runner, duration, and visual-QC regression slices passed 174 tests after the final fixes.

Remaining:

- Generate and review the five missing character videos: `micro_007`, `micro_009`, `micro_013`, `micro_014`, and `micro_019`.
- Publish complete `visual_selection.json`, render the new micro-cut with the approved Doubao voices, and run final frame, subtitle, overlap, loudness, freeze, cut, and OCR checks.

## 2026-07-17 — v1.62 Complete Micro-Cut, Human OCR Recovery, and Audio-Visual Alignment

Goal:

- Finish the approved 19-micro-shot quality path, render the real gateway assets with the approved multi-role Doubao audio, inspect the complete cut, and iterate until no known production blocker remains.

Problems found:

- `micro_009` candidate 1 passed automatic OCR, but a human zoom inspection found persistent pseudo-letter shapes in the blurred cinema lightbox.
- The first complete micro-cut was technically valid but contained two narration/visual mismatches: the narration described two people printed on a poster while the selected frame showed abstract frosted glass, and it described a childhood handoff while the approved visual showed adult Lin Che receiving the envelope.
- Planned static holds appeared in generic freeze detection and needed to be distinguished from accidental freezes.

Reasoning:

- OCR is evidence, not a substitute for full-frame human review. Low-contrast pseudo-text that escapes Tesseract still fails the clean-frame gate.
- Re-generating complex multi-person memories would reintroduce identity, extra-person, text, and anatomy risk. A truthful screenplay adaptation can preserve the mystery while matching already approved visuals exactly.
- Freeze warnings must be interpreted against the micro-shot source kind and timeline; reviewed still holds are intentional, while a long hold used to conceal a failed video is not.

Implementation:

- Rejected `micro_009` candidate 1 at 92 because `embedded_text` is a hard failure. Redesigned the composition so Lin Che fills the frame against a dark defocused wall and looks toward off-screen light. Candidate 2 removed the lightbox and passed at 98.
- Reviewed and passed the remaining candidates: `micro_007` at 92, `micro_013` at 88, `micro_014` at 94, and `micro_019` at 92.
- Published an exact 19-item selection: 12 Seedance 2.0 videos, five Seedream 4.5 stills, and two audited editorial stills. Every video binds to a fresh passed QC report; every still binds to its byte size and SHA-256.
- Rendered `micro_preview.mp4` with per-shot 6/8/10 fps cadence, approved source ranges, hard cuts, and no legacy card fallback.
- Adapted shot 3 narration to the two overlapping light reflections in the blank poster frame and shot 5 narration to the envelope reveal, Lin Che receiving it, and Su Mian standing behind him.
- Regenerated only the two changed narrator clips with Doubao Seed-TTS 2.0. Five unchanged clips were reused from the signed cache; timed subtitles were rebuilt from measured durations.
- Re-rendered the voiced micro-cut and burned the revised subtitles through OpenMontage Remotion.

Verification:

- Visual selection gate: 19/19 sources, 12 video, 7 still, success.
- Final media: H.264/AAC, 1080x1920, 30 fps, 48 kHz stereo, 61.033 seconds, 29,017,862 bytes.
- Final SHA-256: `5c37963912dde7e9bb10ddec7a6cee19e9ca9937cc8f4646e1513958b817feb2`.
- Video preflight warnings: 0. Black segments: 0.
- Audio: seven Doubao clips, zero local clips, zero timing overlaps, distinct active voices, no provider errors or warnings.
- Measured loudness: `-16.31 LUFS`; true peak: `-5.06 dBTP`.
- Full-cut contact sheet, all 19 micro-shot midpoints, and all 18 cut boundaries before/after were inspected. No broken crop, source subtitle, generated/source subtitle collision, black insertion, model-internal transition, or caption overflow was found.
- Freeze detections align with the seven reviewed still sources and are intentional timeline holds.
- Full regression suite: 1,658 passed. Ruff, compileall, JSON parsing, and `git diff --check`: passed.
- Workflow state is now `goal_ready`; the quality-upgrade path reports `production_ready=true`, `micro_preview_success=true`, and `render_path=quality_micro`.

Effect:

- The deliverable no longer relies on the old six-shot rescue edit, long frozen bad frames, or card fallbacks.
- The cinema pseudo-text was removed instead of hidden.
- Narration, subtitles, and visual evidence now describe the same events at the two previously mismatched story beats.
- The full production path is resumable and auditable from model selection through final render.

Residual optimization opportunities:

- `micro_006` passes at 86 but reads more as surprise than a suppressed frown.
- `micro_013` passes at 88 but shows the stopped pose more clearly than the act of stopping.
- `micro_016` passes at 84 and uses an abstract silver background rather than a literal cinema screen.
- These are score-level polish items, not identity, anatomy, text, cut, audio, or delivery blockers. Future premium iterations should replace them one at a time and retain the current approved cut until a replacement passes.

## 2026-07-20 — v1.63 Dual-Character Speaking A/B and Scene Continuity

Goal:

- Produce two real ten-second dialogue samples under the same scene and character references: natural mouth motion with approved Doubao voices, and Seedance native audio with tighter lip sync.

Problems found:

- The configured Doubao AppID/AccessKey client supports streaming synthesis, while the first speaking pipeline assumed every credential used the asynchronous task API.
- Seedream returned JPEG content at a `.png` output path, which failed the strict reference-image format check before video generation.
- The first natural-mouth Lin Che candidate used a full-body composition, making mouth motion unreadable on a phone screen.
- Generic freeze detection marked restrained facial performance as short freezes even when dense frame sampling showed changing mouth shapes.

Reasoning:

- Provider capability, not the presence of credentials, must choose the TTS protocol.
- Reference assets must have matching suffixes and file signatures before entering a paid downstream model.
- Mouth visibility is a composition requirement. A speaking candidate cannot pass merely because the prompt asked the character to speak.
- Motion quality must be judged from source cadence, dense facial samples, and dialogue timing together; a numerical freeze warning alone cannot distinguish subtle acting from an accidental held frame.

Implementation:

- Added synchronous Doubao streaming synthesis with completed-state reuse while preserving the existing resumable asynchronous path.
- Fail closed before any call or state write when a submitted asynchronous TTS task is reopened with a legacy-only client, preserving its durable task ID for the correct recovery client.
- Normalize JPEG/WebP scene output to genuine PNG inside the guarded artifact store before reuse or Seedance submission.
- Migrate legacy JPEG-at-PNG anchors without invalidating paid completed videos: write compatible post-conversion signatures first, block on in-flight recovery state, then atomically replace the anchor.
- Apply the same legacy-anchor gate to targeted video-only execution; a missing or mismatched completed scene state now fails before the video client is prepared.
- Reused one immutable empty night-street anchor across all four dialogue clips.
- Kept Seedance's native 24fps material free of optical-flow interpolation and converted directly to the 30fps delivery stream.
- Retried only natural-mouth Lin Che candidate 2 with a chest-up framing and explicit unobstructed mouth articulation; candidate 1 remains preserved.
- Composed A, B, and a sequential A/B comparison with mobile-safe labels, 48kHz stereo audio, and a deliberate 0.5-second neutral separator.

Verification:

- A and B are each 10.00 seconds at 1080x1920, H.264, 30fps, with one AAC 48kHz stereo audio stream.
- Sequential comparison is 20.51 seconds; A/B technical preflight reports zero warnings and no unintended black segments.
- A measures -15.4 LUFS / -2.5 dBFS true peak; B measures -14.5 LUFS / -2.5 dBFS; their difference is 0.9 LU.
- Dense face sequences show visible open/closed mouth states for both characters in both routes. Lin Che natural candidate 2 resolves the first candidate's unreadable full-body framing.
- Manual contact-sheet review found no extra people, identity swap, anatomy duplication, generated text, watermark, overlay collision, or location change.
- Regression coverage proves submitted async TTS state survives a legacy-client switch unchanged, a baseline-style JPEG anchor upgrades to PNG while all seven completed assets remain reusable with zero new provider calls, and video-only execution cannot bypass a missing scene state.
- The gateway currently lists no ASR/Whisper model and local speech-recognition permission is not enabled, so the native-audio route is visually reviewed but not claimed as phoneme-level certified.

Effect:

- B is the preferred route for visible dialogue because native speech and facial performance are generated together.
- A remains the controlled-voice route for approved multi-role Doubao voices, narration, and off-screen dialogue; its mouth motion is deliberately treated as natural approximation rather than exact lip sync.
- The final evidence, selected candidate numbers, commands, technical results, and residual limitations are recorded in `runs/sample_episode/speaking_ab_20260720/review.md`.

## 2026-07-27 — v1.64 Pet Sitcom Audio-First Continuity Rebuild

Goal:

- Replace the old clip assembly with one auditable 10-shot, 54-second,
  audio-first production path for “冻干到底是谁偷吃的”.
- Preserve approved voices and dialogue timing while making mouth motion,
  action continuity, sound design, and final publication explicit gates.

Exact before/after:

- fourteen independent fixed five-second clips → ten variable shots with state and dependency contracts,
- video-first TTS overlay → immutable TTS-driven speaking shots,
- global `atempo` fitting → no dialogue retiming,
- fixed pluck/shaker loop → approved non-looped source with three narrative cue regions,
- numeric previous-shot continuity → explicit main-axis and replay dependency graph,
- subjective “looks okay” only → hash-bound mouth onset/offset and action-continuity review.

Current implementation:

- Task 2 fixes 8 Doubao Seed-TTS 2.0 lines, role voices, absolute timeline,
  trimmed immutable WAV hashes and six generation-length drive WAVs. The
  `shot_06` owner line starts as a 0.20-second J-cut; no dialogue uses `atempo`.
- Task 4 runs a one-time `doubao-seedance-2-0` audio-drive probe and binds its
  report, MP4, nine evidence frames and manual mouth review to current anchors,
  TTS, drive audio, model and prompt.
- Task 5 uses endpoint-bound audio-driven generation for the six speaking
  shots. Candidate provenance binds prompt, anchors, dependencies, immutable
  TTS, drive WAV, model, duration, audio mode, MP4 and continuity endpoint.
- Task 6 review v4 checks all 10 selected shots, six mouth-timing records,
  owner native audio, action preparation/execution/settle, eyeline/screen
  position, physical transition logic and the explicit continuity graph.
- Task 7 trims 10 variable shots without padding/interpolation, builds
  non-looped three-act sound with J/L bridges, and validates clean/release as
  1080x1920 H.264 High, yuv420p, 30fps, AAC stereo 48k and about 54 seconds.
- Task 8 exposes the exact CLI sequence `plan -> anchors -> audio ->
  audio-probe -> shots -> review -> compose -> status` and a pure-read status.

Old failure review:

| User-visible failure | Root cause | Reasoning | Treatment | Effect | Next iteration |
| --- | --- | --- | --- | --- | --- |
| 声线不统一 | 视频先生成、后置配音且角色音色没有成为镜头输入契约 | 声音身份必须先于说话表演冻结，否则候选之间无法审计 | Task 2 固定三角色 Seed-TTS 2.0 voice/rate，8 条 WAV 及时间线 immutable/hash-bound | 角色声线和重跑复用具有同一来源真值 | Task 10 用真实 provider 复核八条音色、语速、首尾裁切与主观一致性 |
| 对话重叠 | 全局 `atempo` 和按镜头补齐改变实际语音边界 | 时间线应消费实测 TTS 时长，而不是把对白硬塞进固定五秒 | 使用绝对起止、0.30 秒尾部余量和 overlap preflight；只保留有意的 J-cut | 取消对白变速，并能在 compose 前 fail closed | Task 10 试听完整 54 秒，复核 J-cut 可懂度与每条对白间隔 |
| 嘴不动 | 后置 TTS 与视频生成解耦，提示词口型没有真实音频驱动 | 先证明 endpoint 支持参考音频，再让六个 speaking shots 消费 drive WAV | 增加 Task 4 probe、人工 probe review、Task 5 audio-driven generation 和六镜 mouth timing | 口型证据绑定真实 drive audio 与 selected MP4；不再只看提示词 | Task 10 对 probe 和六镜逐帧记录 onset/offset；只声明人工视觉复核，不声明音素级认证 |
| 场景跳变 | 14 个独立五秒请求只按数字前镜头衔接，没有 start/end state 或 replay 回边 | 连续性必须表示故事依赖，不等同于 `shot_n-1` | 10 镜头固定角色/场景 anchors、start/end state、main-axis 与 replay dependency graph，使用 edit endpoint sidecar | 选择上游变化只使真正依赖镜头 stale，主轴与回放回接可审计 | Task 10 逐边看 six-frame evidence，重点复核 `shot_05/06 -> shot_07` |
| 动作过滑/掉帧 | 旧方案先统一降帧，既掩盖模型内无准备动作，也制造 cadence 问题 | 动作质量应在源候选评审，交付只做确定性 30fps 转换，不做光流补帧 | review v4 增加 preparation/execution/settle 与 freeze gate；compose 禁止 `minterpolate`、`tpad` 和短片补齐 | 能区分源动作失败与交付帧率，短源直接要求重生 | Task 10 在 10-shot review 记录动作时间戳，重点看推镜、转头、尾巴和镜子动作 |
| BGM生硬 | 固定 pluck/shaker loop 与剧情、对白无批准关系 | 音乐应由经过人工批准的连续来源按叙事区域处理，并在对白处退让 | sound v2 使用一条非循环源，三幕 cue、dialogue duck、room tone、foley 和 restrained ending button | BGM 不再机械回环，监控段和反转段有不同音色，J/L bridge 保持连贯 | Task 10 用 approved music 重新 prepare，试听对白可懂度、转段动机与结尾按钮 |

Manual gates and truth boundary:

- Human approval is required for anchors, the audio-drive probe, all six
  speaking-shot mouth timings, all 10 shot reviews plus owner native-audio
  gate, and the music source. The implementation does not claim that these
  subjective approvals are automated.
- Persisted `unsupported` and `inconclusive` probe results are terminal and
  fail closed. They return `next_stage=blocked` and must never trigger an
  automatic paid retry. Only a genuinely `missing` or `stale` probe is eligible
  for an operator-directed live rerun; `pending` means review, not resubmission.
- `status` does not probe media, run providers/TTS/FFmpeg/FFprobe, or mutate
  files. It reports whether the latest strongly validated evidence and its
  bytes/hashes remain current. `compose` still reruns the strong gates.

Sound v2 migration:

- The schema remains `motion-comic-factory.pet-sitcom-sound-design.v2`, while
  `pet-sitcom-three-act-v2` now includes source duration, stream duration,
  sample rate, channels, codec type/name and channel layout in the binding.
- A legacy v2 manifest is intentionally stale under the new binding. Task 10
  must run prepare again with the approved music source; hashes must not be
  hand-edited.
- Old and new workers compute different bindings and will mark one another's
  manifest stale. Do not run mixed-version workers in parallel during upgrade.

Operator output and recovery:

- The production root is
  `$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2`.
- Current evidence lives under `evidence/`; current sound binding is
  `sound_design.json`; immutable stems live under `audio/sound_design/`.
- Final files are `final/冻干到底是谁偷吃的_清洁版.mp4` and
  `final/冻干到底是谁偷吃的_发布版.mp4`, with `review.md` as the final
  human-readable evidence index.
- Compose uses `.pet-sitcom-compose-publish.lock` and
  `.pet-sitcom-compose-transaction.json`; a later compose recovers an
  interrupted clean/release pair before rendering. Operators must not delete
  recovery materials or run parallel compose.

Security:

- Only environment variable names are documented. No real gateway key, API
  key, complete secret URL or data URI is stored in this log.

## 2026-07-28 — v1.65 宠物 Task 2 定版、成片自检与证据链修复

目标：

- 完成 10 镜、54 秒双猫短剧，解决声线漂移、对白重叠、嘴不动、动作冻结、
  场景跳变和生硬配乐。
- 在交付前同时通过逐镜人工审核、最终媒体技术门禁、全片接触表和关键转场抽帧。
- 保留真实失败候选和本地重剪来源，不通过手改哈希或伪造审核记录完成报告。

问题与判断：

1. **镜头 4 比编辑窗短一帧**：源片是合法 24 fps 本地重剪，4.167 秒只比
   4.2 秒编辑窗短一个源帧。复制尾帧会造成可见冻结，光流会制造猫脸和胡须伪影。
2. **最终编码产生假冻结**：源镜头 6 和 8 的自动检测为零，但
   `veryfast / CRF 18` 把细微毛发、呼吸和传感器纹理压平，成片检测误认为长静帧。
3. **历史审核误绑可变候选路径**：同一候选槽位的本地重剪会覆盖旧字节，旧验证器
   再哈希历史路径后把真实归档判为 stale。
4. **本地重剪没有直接失败记录**：镜头 4 候选 3 使用已审核候选 2 作为源，
   但旧报告只接受“失败记录直接指向当前 MP4”，无法表达合法的重剪来源链。
5. **声音与配乐需要统一检查**：旧样片出现角色声线变化、对白相叠和机械循环 BGM；
   单看 TTS 成功状态不能证明最终混音合理。

处理：

- 仅对满足“current hash-bound、本地 FFmpeg 重剪、24 fps、短缺不超过
  `1/24` 秒、recipe 时长匹配”的镜头使用全段 PTS 微调。普通短源仍 fail closed；
  禁止 `tpad`、尾帧复制和 `minterpolate`。
- 最终 H.264 改为 `medium / tune grain / CRF 16`，保留源片的细毛、呼吸和
  监控纹理，不人工添加噪点。
- 历史归档改为验证不可变 JSON 字段、路径边界、64 位哈希、审核记录绑定以及
  `<old>_to_<current>.json` 文件名；不再要求已被合法覆盖的旧候选字节仍存在。
- 报告和验证器共用同一套重剪来源解析：当前本地重剪必须绑定自身哈希、候选号和
  provenance，并且同镜头的 source candidate 必须能命中一条有效失败审核归档。
  篡改来源路径、候选号或哈希仍会被拒绝。
- 删除仅由一次 101 帧时长实验产生的临时选择历史
  `90637f…`，保留所有真实模型候选、失败原因和正式重剪记录。
- 角色音频固定为豆包 Seed-TTS 2.0：主人
  `zh_female_vv_uranus_bigtts`，奶糖
  `saturn_zh_female_tiaopigongzhu_tob`，豆包
  `saturn_zh_female_keainvsheng_tob`。8 条对白使用实测 WAV 时长和绝对时间线，
  不使用 `atempo`，对白重叠为零。
- 配乐选择人工批准的 Mixkit `Secret Garden` 连续片段，保留 70 秒 48 kHz
  stereo PCM 来源，不循环、不拉伸。0-26.5 秒审问、26.5-37.4 秒监控、
  37.4-54 秒反转分别处理；对白区退让 8 dB，并加入克制的袋子、尾巴、脚步、
  镜子和结尾声音。

验证：

- 选定镜头哈希：
  `457d077b`、`eaeb7b6d`、`6da1d1a1`、`c4b55397`、
  `1df1df25`、`7e925db7`、`c4ddb552`、`29efa93e`、
  `61f3cad0`、`4fdace54`。
- 清洁版 SHA-256：
  `7b40257b67c80c75b1dbe8a9ff98bbbe0e0b421e901175af1478280551882627`。
- 发布版 SHA-256：
  `b8952c7661bee82b68b0a30ff2d8c174cae6cc680fa39e26419e83b42c62a4f0`。
- 两版均为 54.000 秒、1080x1920、30 fps CFR、H.264 High、
  yuv420p、AAC stereo 48 kHz、`-15.7 LUFS`、`-1.5 dBTP`。
- 最终 blackdetect=0、freezedetect=0；video self-check 对两版均为
  warnings=0。
- 检查全片九宫格，以及 13.8-20.0、25.5-38.5、43.4-54.0 秒高密度抽帧：
  角色轴线稳定，监控袋子先滑动/翻折再出现尾巴，镜子先被推入再质问，字幕没有遮挡
  猫脸、嘴、前爪或证据道具。
- 回归覆盖同路径本地重剪、归档文件名篡改、合法上游失败候选和未审核上游候选四种
  历史场景。
- 宠物工作流完整回归 `563 passed`；Ruff、compileall、`git diff --check`
  和敏感长 `sk-` 值扫描通过。只读 CLI 状态为 `composition_ready=true`。

效果：

- 双猫各自保持可爱且固定的音色；8 条对白无重叠，主人 J-cut 是唯一有意跨镜声音。
- 六个说话镜头都有可见的准备、开口、闭口和静默区，人工 onset/offset 误差门限为
  0.25 秒；不宣称音素级逐字认证。
- 动作不再通过降帧、尾帧补齐或光流制造“顺滑”，模型原始微动作被高保真编码保留。
- 客厅主轴、厨房审问、监控回放和回到厨房的因果关系清楚，切换是叙事镜头切换，
  不是模型内无原因瞬移。
- `review.md` 可从当前成片反查模型、候选、声音、失败记录、来源哈希和最终技术证据。

后续迭代：

- 高级版只逐镜替换，不覆盖当前已通过基线。新候选必须同时提高口型细节、动作幅度
  和场景一致性，否则保留当前镜头。
- 若网关后续提供可验证的中文音素/viseme 输出，可增加音素级自动评分；当前继续以
  immutable TTS、25 fps/12 fps 人工抽帧和主观同步为真值边界。

## 2026-07-29 冻干案发布版二次坏样片纠正

用户反馈：

- 发布版存在持续杂音，场景切换过多导致眩晕，冻干袋在没有外力时自行滑动。
- 对话镜头中还存在声音继续但当前角色闭嘴的问题，动作因果关系不可信。

定位与思考：

- 对旧成片做切镜检测，54 秒内发现 16 个硬切，其中一段约 5.4 秒包含 4 次切换。
- 审核音频图后确认程序持续生成粉红底噪，并用粉红噪声或正弦波模拟袋子、尾巴、
  脚步和镜子声音；这不是素材噪声，而是合成策略本身制造的宽带杂音。
- 原监控镜头把“袋子滑动/翻折”和“尾巴出现”并列，却没有任何可见接触或动力，
  说明旧审核只检查动作是否发生，没有检查动作由谁引起。
- 口型补救曾依赖频繁正反打，虽然局部遮住闭嘴画面，却破坏了观看节奏。新策略是
  优先选完整开口素材，只在源素材冻结或说话主体错误时使用一次短反应镜头。

处理：

- 重剪为 8 个叙事镜头、44.5 秒；保留稳定空间轴线，删除重复反应和无意义转场。
- 监控段固定冻干袋，只让画面外有来源的猫尾运动；镜子仅在黑猫前爪接触后移动。
- 两段关键对白分别重组选用持续开口的橘猫和黑猫素材，反应镜头不再让闭嘴角色
  承担可见对白。
- 音频改为严格白名单：8 条豆包 Seed-TTS 2.0 对白加一条人工批准配乐；彻底移除
  程序合成底噪、拟音和连续 room tone。
- 增加成片 Gate：硬切总数、5 秒切镜密度、黑帧、冻结、音频来源白名单和完整道具
  因果合同必须全部通过。发布版独立扫描切镜，只忽略与已知字幕显隐边缘重合的检测点，
  其他发布版新增跳切一律失败；对白还必须唯一绑定并完整落在所属镜头内。

结果：

- 精修发布版 44.5 秒，9 个硬切，任意 5 秒最多 2 个硬切；blackdetect=0、
  freezedetect=0。
- 响度 `-15.6 LUFS`、真峰值 `-1.5 dBTP`；新版频谱不再出现旧版贯穿全片的
  宽带噪声层。
- 联系表和高密度抽帧确认冻干袋位置/朝向不变、尾巴独立运动、镜子有前爪动力，
  两段关键对白的开口角色与声音起止一致。
- 相关宠物短剧回归 `323 passed`，精修模块独立审查补强后 `23 passed`。当前口型目标
  是自然同步，不宣称音素级逐字匹配。

## 2026-07-30 — v1.66 160 秒《斑斑来访》长片生产与全片坏样片闭环

目标：

- 制作一条原创双猫加斑鸠、39 镜、约 160 秒的竖屏宠物长片，保持角色、客厅空间、
  道具和动作因果连续。
- 使用网关视频模型和豆包多角色 TTS，把逐镜生成、口型、声音白名单、最终合成及
  全片复检串成可重复工作流。
- 不以单镜自动通过代替全片人工检查；发现坏样片后保留候选和证据，定点重生。

模型与生产策略：

- 网关请求合同经过输入格式诊断：M4A 路径不作为生产依赖，MP3/Fast 只用于确认
  endpoint 行为；39 个正式镜头统一使用 `doubao-seedance-2-0`。
- Seedance 最短生成窗按 4 秒请求，短编辑镜头只消费计划内的有效时长。提示词明确
  4 秒内的动作阶段，质量计算也只检查实际使用窗口，避免把 provider 补出的尾段
  错算成成片冻结。
- 33 条对白全部使用豆包 Seed-TTS 2.0。奶糖、豆包、斑斑和旁白分别固定一个
  voice ID；重新排程会删除陈旧 drive audio，防止旧节奏缓存混入新镜头。
- 后六镜重新按“准备、执行、结果、停顿”写动作时标，礼物先被带入、放下，再由
  猫看到并回应，避免道具无外力移动或角色跨镜瞬移。

发现的问题与处理：

1. **单镜通过但全片仍有长停帧**：逐镜内部阈值没有覆盖最终剪辑边界。对发布版再跑
   全片 `freezedetect` 和密集抽帧，定位到 S002 约 1.6 秒静止尾段、S027 约
   0.8 秒静止尾段。
2. **S002 反应镜头缺少持续动作**：候选 2 仍有约 0.58 秒停顿，候选 3 退化到
   约 0.79 秒，均拒绝。第 4 候选把影子扫过、转头、分侧收翼、短回望和肩羽收束
   拆成连续时间窗；内部最长低运动段降至 0.292 秒，外部扫描仅剩片尾 0.375 秒
   的短暂稳定姿态。
3. **S027 台词后冻结且开口晚于声音**：第 4 候选保留闭喙、对白、闭喙和对白后
   转头/眨眼/呼吸四段动作，自动扫描冻结为零。逐帧测得鸟喙约 0.17 秒开始动作，
   将原始 Seed-TTS WAV 从镜内 0.45 秒前移到 0.20 秒，不重新合成、不变速；
   可见开口与声音相差约 0.03 秒。
4. **FFmpeg 对白总线重复消费**：同一音频标签同时送入混音和响度分析会造成
   filter graph 不稳定。使用 `asplit` 显式拆分，发布总线 true-peak 目标设为
   `-2.0 dBTP`，为 AAC 编码留出余量。
5. **恢复环境漂移**：OpenMontage 源码存在，但 `.venv` 已丢失，导致 27 个 CLI
   和 runtime probe 测试同时失败。按其 `requirements.txt` 重建 Python 3.12
   虚拟环境，不改动业务行为。
6. **恢复状态的敏感地址**：当前实现只需安全 task ID 即可续跑，不需要持久化带
   凭据的下载 URL。回归测试改为要求状态文件和候选报告都不得包含完整远程地址
   或其中的凭据。

最终验证：

- 清洁版与发布版均为 160.000 秒、1080x1920、39 镜、33 句对白、单一音频流。
- 33 条音轨全部为 `doubao / seed-tts-2.0`；角色到 voice ID 是一对一关系，
  对白重叠为零，最小非负间隔为 0.6645 秒。
- 发布版响度 `-15.57 LUFS`，true peak `-1.88 dBTP`，音频来源策略为
  `approved_music_and_seed_tts_only`；全片 FFmpeg 解码零错误。
- 全片逐秒接触表和 S002、S027 密集抽帧已人工复核。旧 S002 1.6 秒和 S027
  0.8 秒停帧消失；剩余最长低变化段为 0.667 秒，位于镜头结束前的有意收势。
- video preflight：160 秒、1080x1920、单音轨、零 warnings；全片未发现黑帧、
  凭空换物、道具自行移动或无原因场景跳转。
- 完整回归 `2467 passed`，Ruff `All checks passed`。

效果与边界：

- 成片从单镜“技术上可用”提升为全片层面的动作、物理因果、音色和口型一致。
- 口型达到自然的音节级视觉对齐，并未获得 provider 的 phoneme/viseme 真值，
  因此不宣称每个字的精确口型。
- S001、S002、S003 在切点前仍保留 0.375 至 0.667 秒的短收势，这是可读的动作
  结束，不再通过补尾帧或光流伪造运动。

产物：

- 清洁版：`$HOME/Desktop/宠物短剧样片/斑鸠来访_20260729_v1/final/斑斑来访_无字净版.mp4`
- 发布版：`$HOME/Desktop/宠物短剧样片/斑鸠来访_20260729_v1/final/斑斑来访_发布版.mp4`
- 最终 QC：`$HOME/Desktop/宠物短剧样片/斑鸠来访_20260729_v1/final/final_qc.json`
- 全片复检证据：`$HOME/Desktop/宠物短剧样片/斑鸠来访_20260729_v1/final/full_qc_final_v2/`

## 2026-07-31 — v1.67《猫猫鬼点子》逐镜重拍与网关空响应诊断

目标：

- 参考 77.229569 秒原片的故事、37 个切点、动作功能和本地评估音频节奏，
  全量重生原创女主、奶糖和豆包的画面；不复制源视频像素、人物身份、账号标识或水印。
- 先完成 0-12.3 秒 R001-R009 试片，逐镜通过身份、空间、动作因果、自然口型、
  真实运动和技术门禁后，再扩展全片。

本轮问题与判断：

1. **参考图扩展名和真实编码不一致**：Seedream 下载结果写在 `.png` 路径，
   实际字节是合法 JPEG。素材层按文件签名通过，视频客户端却要求后缀和签名同时
   匹配，导致付费请求前被本地误拒绝。
2. **标准 Seedance 提交返回空响应**：R001 使用五张参考图和原始 WAV 时，
   网关约四秒后返回空响应，未得到可解析任务 ID。把 1.733 秒音频补静音到四秒后
   仍失败，所以不是音频短于 provider 窗口。
3. **请求体大小不是根因**：用无效模型分别提交约 200 KB 至 1.9 MB 请求，
   网关都能返回结构化 JSON；历史成功的两参考图加 WAV 请求约 1.90 MB，
   也大于当前失败请求。
4. **减少参考图仍失败**：把角色和客厅合成一张 2x2 控制板，仅保留控制板、
   构图帧和补齐 WAV，结果仍为空响应；Seedream 多参考图请求同样出现空响应，
   问题指向当前多模态适配层或上游服务，不是单个镜头提示词。
5. **失败清理丢失诊断状态**：候选事务会正确阻止坏视频进入正式目录，但异常分支
   同时删除 `.gateway.json`，无法区分“远端没有创建任务”和“已创建但本地没收到
   响应”，存在重复提交和重复计费风险。
6. **源证据路径只做字面包含**：评审记录可把 `../outside.jpg` 或同根目录改名文件
   伪装成 R001 源帧，即使哈希正确也不代表它是规定的
   `reference/shots/R001/start|middle|end.jpg`。

处理：

- 视频客户端改为以 PNG/JPEG/WebP 文件签名确定上传 MIME；后缀仍只允许安全图片
  类型。所有 R001-R009 请求均完成离线打包验证，最多五张参考图，未超过九图限制。
- 暂停自动重复提交。远端状态不确定时不换候选号、不加并发，也不以 Fast 模型替代
  正式质量模型；先保留任务状态并查网关日志。
- 新增失败尝试档案：
  `rejected/generation_attempts/<shot>/<candidate>/attempt_NNN/`。每次保存脱敏后的
  `gateway_report.json`、`gateway_state.json` 和 `failure.json`，正式候选仍不提升，
  旧候选仍事务回滚保留。
- 修正敏感信息正则，避免把合法的 `task-...` 任务 ID 中间片段误当成 `sk-` 密钥；
  API key、签名参数、token URL 和授权字段仍全部脱敏。
- 源帧评审先解析真实路径并拒绝任意 `..`、全路径任一 symlink，再要求每条记录精确
  等于规定的 shot/label 路径；评审、批准和 selection 再验证三层都加入逃逸与改名
  对抗测试。

效果与当前边界：

- 图像后缀误拒绝已消除；失败报告不会再因事务清理消失，也不会把不确定任务误当成
  可安全重提。
- Task 6 最终独立复审为 spec PASS、quality PASS、无遗留发现；关联回归 85 项通过。
  网关、音频、素材、参考证据和候选生成扩展回归 187 项通过。
- 当前尚未得到 R001 云端候选，不能宣称 12.3 秒试片或 77.229569 秒全片已完成。
  阻塞点是网关多模态请求空响应；本地合成、CLI 和最终门禁继续开发，不用该故障作为
  停止全部工作的理由。
- 网关恢复或登录任务日志后，先核对已存在的 `submitting` 记录，再只提交一个 R001
  候选。R001 通过人工逐帧检查后才允许批量生成 R002-R009。

## 2026-07-31 — v1.68 合成发布、OCR 证据与竞态关闭

发现的问题：

1. 早期最终 QC 仍有“记录计划值而非复测成片”、发布非事务、干净版与字幕版身份
   不清、源画面复制检测不足等问题。
2. OCR 仅靠旧 `subtitle` 字段，无法证明账号名、水印、头像和装饰字已经人工分类；
   空 `ocr_events` 也可能被误当成审核完成。
3. ASS 使用百分之一秒时间会把 30 fps 边界舍入到相邻帧；系统默认 Arial 还可能把
   中文渲染成方框。
4. 即使成片、QC 和发布快照已经验证，最终指针切换前若 selection 或源 AAC 被替换，
   旧实现仍可能发布与记录摘要不一致的内容。

处理与迭代：

- 合成改为 clean/captioned 双母版、真实 30 fps 切点、源 AAC 校验、黑帧/冻结/
  源画面复制/对比视频实测，并用事务目录发布；任意失败删除新目标并保持旧指针。
- OCR 改为内容寻址证据。每个检测必须映射一个人工事件，`reviewed_zero`、数量、
  帧窗、源框和 SHA-256 全部闭环；只有 `dialogue_subtitle` 可进入字幕母版。
- 字幕时间改用整数 `start_frame/end_frame`，ASS 只做整数帧到厘秒的确定性映射；
  中文字体固定并绑定 `/System/Library/Fonts/STHeiti Medium.ttc` 的哈希与
  `Heiti SC` family，真实渲染验证字形和精确变化帧。
- 最终发布回调重新计算每个 selected clip、源 AAC 和参考视频哈希，并重新验证当前
  selection/audio manifest 的路径与摘要。针对字节、元数据、合法路径切换、摘要
  切换、symlink 和 `..` 别名做发布前故障注入。

结果：

- 第 7 阶段最终独立审查 PASS；完整 replica 回归 `211 passed`，Ruff、py_compile
  和 diff 检查通过。
- 37 镜真实注释已迁移到 v2：65 条对白、111 条固定品牌排除和 1 条 R012 装饰事件，
  共 177 条；37 份证据均为内容寻址文件，旧 v1 注释单独备份。
- 生产严格 loader 返回 37 镜、177 事件、65 条可渲染事件。7 页边界联系表覆盖
  264 个 `pre/start/end-1/end` 检查格并已逐页人工确认；R012 装饰字精确为
  `[484,509)`。
- 发布回调的所有对抗用例均拒绝新版本、逐字节保留旧指针并清理失败事务。

剩余边界：

- 最后一次文件读取到原子替换 current 指针之间仍存在极短的文件系统竞态。当前
  独立终审将其判定为可接受的已记录残余风险；若要完全消除，需要不可变输入发布
  或跨进程锁，而不是继续增加重复哈希。
- 网关空响应仍阻塞 R001 云端候选；本地发布基础设施和真实 OCR 合同已经就绪，
  但不能据此宣称试片或全片画面已经生成。

## 2026-08-03 — v1.69 R001/R002 实片、口型闭环与坏例修正

先查清的问题：

1. 两条旧 `submitting` 记录在网关日志中都没有对应 Seedance 任务，已按失败归档，
   不再把历史状态当成仍在计费的任务。
2. R001 第一次重提返回隐私图片拦截。请求里既有源视频人物帧，也有写实人物参考图；
   后续只向供应商发送原创猫咪参考与空场景，人类身份改为固定文本描述。
3. Seedance 原生输出是 24 fps，旧门禁误要求 30 fps，导致已付费的正常视频被拒绝。
   生成门禁现接受标准 24/25/30 fps，最终合成仍统一到 30 fps。
4. R001 候选 01 丢失猫咪花色且云端音轨延迟；候选 02 镜头过远、猫落到脚边；
   候选 03 构图与花色合格，但整句嘴巴不动。三条都没有直接自动批准。
5. R002 首轮审核把精确 `1.366667s` WAV 从 FFmpeg 标题读成 `1.37s`，误报声音越界；
   现改为读取 WAV 帧数计算精确时长。
6. R002 的固定脸框 Wav2Lip 虽然很快，却扭曲眼镜与脸部；该版本判废。高批量动态
   检测又触发进程退出，最终使用 `face_det_batch_size=1` 完成 33 帧稳定检测。
7. 第一次重新封装使用 `-shortest`，容器有 1.366 秒但视频流只有 1.333 秒。门禁现
   检查视频流时长本身，并支持审计式替换已有口型版本，旧版本按哈希保留历史副本。

处理方式：

- 安装官方 Wav2Lip，并固定仓库 commit、GAN 权重和 S3FD 权重 SHA-256。口型晋升时
  原始 Seedance 输出与原始 provenance 进入内容寻址归档，当前候选记录驱动音频、
  模型权重、原始云端视频和最终输出的哈希。
- 说话镜头先保留 Seedance 的头部、身体和镜头运动，只在原生嘴型不可用时修复嘴周；
  最终音轨重新绑定镜头自己的 drive WAV，不使用 Seedance 延迟音轨或运输静音。
- 状态机允许同一镜头的旧失败备选与一个有效候选并存；只有某镜头不存在任何有效
  候选时，旧备选才会让生成阶段变成 `stale`。
- 每次后处理后重新生成 12 帧接触表、8 fps 口型表和源/候选三时点对照，旧审核图
  不复用；人工门禁继续检查身份、数量、花色、构图、动作、口型、物理与品牌排除。

结果：

- R001 通过：两只猫花色与接触位置稳定，女主伸懒腰连续，驱动音频与输出静音边界
  一致，嘴部在语音开始后 0.20 秒内进入音节变化。
- R002 通过：与 R001 的酒红发色、发际线、圆框眼镜、脸部比例、上衣和沙发连续；
  近脸保留眨眼和仰视，嘴型具有闭唇、圆唇、开口和收口变化。
- 当前真实状态为 `approved=2/37`、`pilot_approved=2/9`、无歧义提交；不能宣称试片
  或全片已经完成。
- replica 全量回归 `333 passed`，Ruff 与 `git diff --check` 通过。

下一轮：

- 从 R003 开始继续逐镜生成和审核；试片九镜全部通过后才合成 12.3 秒 pilot。
- 将低批量动态人脸检测、运输音频和最终原音轨重封装收进可直接调用的本地后处理
  命令，并为同一候选缓存人脸检测框，减少 CPU 重复计算。
- Wav2Lip 官方许可仅适用于个人、研究和非商业评估；公开或商业发布前必须替换为
  许可合适的口型方案或取得授权。

### 2026-08-03 续跑补充：R003 提交歧义与后续镜头预审

- R003 源镜头预审补回绿色易拉罐、白色吸管、两猫左右位置和女主坐直动作；正式
  请求只生成一个候选，但提交阶段收到 `curl (52) Empty reply from server`，本地仅有
  `status=submitting`，没有 task ID，正式目录没有候选晋升。
- 当前 `ambiguous_submission_count=1`。模型 API key 不能读取控制台任务日志，
  `/api/task/self` 返回 401，说明需要已登录控制台会话。未证明远端终态前禁止重提，
  以免重复任务或重复计费。
- OpenMontage `lip_sync` 不再硬编码系统 `python`，而是优先调用 Wav2Lip 自带 venv；
  新增可配置 `face_detection_batch_size`，默认 4，本机 `.env` 已配置 Wav2Lip 路径。
  工具测试 1 项通过，运行时状态为 `available`。
- R004-R009 已逐镜预审并补全物理约束：R004 只允许单只手和前臂持手机入画；R005
  手机由手持续握持；R007 双臂持续承托豆包；R006/R008 猫咪说话前后闭口；R009
  女主头肩保持沙发接触。六镜均已离线编译到正确参考图与驱动音频。
- 离屏人物 prompt 合同已修正：允许动作需要时出现一只手和前臂，同时禁止脸部、
  完整身体和可见说话嘴。generation/CLI 关联回归 `136 passed`，Ruff 通过。

## 2026-08-04 — v1.70 试片封版、局部口型与跨镜连续性复查

发现的问题：

1. 全脸 Wav2Lip 会重绘眼镜、眼睛和脸部轮廓。嘴型虽然动了，人物身份和表情却比
   Seedance 原片更假；这类结果不能因为“有口型”就晋升。
2. R003 的绿色饮料容器、R004/R005 的手机容易生成标签、条码、假界面或伪文字；
   R006 一度把同一客厅的米色布艺沙发变成棕色沙发。
3. R009 语音结束时仍有张嘴尾帧，视觉上像声音停了但人物还在说话。
4. 第一轮九镜评测虽通过，但封版后再看精确切点发现 R003 的圆顶盖绿杯和白吸管，
   到 R004 变成了无盖圆筒。这说明只看单镜 Q1/MID/Q3 不足以证明跨镜连续。
5. 首个封存包把 `deliveries` 本身设为只读，后续版本无法在其下创建子目录。封存包
   没有损坏，但交付目录的层级设计不适合持续迭代。

思考与处理：

- 把“动作来源”和“嘴部修复”拆开。优先使用带驱动音频的 Seedance 原生动作；仅在
  原生嘴型不可用时，使用 OpenCV YuNet 定位嘴部，在原始 Seedance 帧上做羽化椭圆
  局部合成，眼睛、眼镜、头发和脸部轮廓全部保留原片。
- 为局部口型增加检测器权重、原始视频、全脸 Wav2Lip 中间件、输出视频和参数哈希；
  末三帧把合成权重渐退为零，让 R009 在语音结束时自然闭口。
- 将生成提示从宽泛描述改为可检查的状态约束：杯身纯色空白、透明半球盖、竖直白
  吸管；手机只允许真实握持且不得出现界面和文字；R006 明确沿用同一米色沙发和
  侧光。失败镜头保留为历史，不覆盖原始云端结果。
- R003、R005、R006 改用带镜头驱动音频的新候选，R004 额外生成第三候选。评审顺序
  改为单镜 12 帧接触表、8 fps 口型表、27 张节拍证据，最后再看 8 组精确切点前后
  帧；R003→R004 的杯盖、吸管、猫左右位置、沙发和光向全部对齐后才重新封版。
- 新交付使用可写容器 `sealed_deliveries/`，每个 release 作为独立只读子目录；旧的
  `deliveries` 包保持原样，不解除权限或覆盖历史证据。

效果：

- 新试片 `pilot-947j60l3` 共 13.333333 秒、400 帧、9 镜和 8 个切点；所有切点均为
  零帧偏差，无黑帧、意外冻结或字幕安全区越界。
- 全脸口型版本的眼部误差约为 4.9-10.6，局部嘴部合成降至约 2.7-2.8，嘴部变化量
  基本保持；人物眼镜和面部表情不再被整脸重绘。
- Delivery Eval 自动技术检查通过，63/63 项语义检查逐项填写可见证据并通过；最终
  封存视频哈希为 `3de0e3ef36e170fe592390a351aed872c22a0d89fe8749375cd76eaa9cfea366`。
- 网关、生成、口型、审核、合成和 CLI 联合回归 `309 passed`；Ruff 与
  `git diff --check` 通过。两条仍要求把 R009 截成一帧的旧测试已按完整镜头合同更新。
- 当前成片仍沿用参考视频音频，只允许本地效果评估；公开发布前必须替换成已清权的
  原创多角色 TTS，并处理 Wav2Lip 的非商业许可限制。

## 2026-08-04 — v1.71 全片封版、解剖坏例与独立交付门禁

本轮目标：

- 从 9 镜试片扩展到 37 镜完整重拍，保留 77.229569 秒参考故事、对白节奏和镜头功能，
  但全部替换女主与两只猫的视觉身份。
- 不把“能播放”当成完成条件；每镜通过候选绑定、12 帧动作表、8 fps 口型表、源片
  三时点对照和人工门禁后，再重合成、独立评测并密封交付。

发现的问题与思考：

1. 后段问答镜头同时包含画外音、道具和多主体。R026/R031/R034 不应因有声音就强迫
   画面主体开口；R027/R035 必须区分画外猫台词与女人自己的回答；R028/R033/R037
   还要保证手机和三脚架符合重力与接触关系。处理时把“谁在画面中”“谁在说话”与
   `presenter_sync_mode` 分开记录，画外音镜头明确为无可见说话者。
2. R034 的食物问答需要恰好六颗球呈 3×2 排列和唯一一块惠灵顿；R036 初版虽能开口
   且食物稳定，却同时出现两只托食物前爪和两只落地前爪。单看中间帧容易忽略总肢体数，
   说明“动作合理”检查必须再加主体解剖总数和每条肢体连接关系。
3. R036 改为胸像近景，画面在惠灵顿下缘立即裁切，只允许两只与肩部连接的白色前爪
   托住唯一食物，禁止腹部、后腿、地面和额外落地爪。新候选重新生成 12 帧动作表、
   8 fps 口型表和源片对照后才批准，旧候选保留为失败历史。
4. 最终 AAC 首轮解码峰值超过 1.0，虽然容器和码率正常，仍有潜在削波。音频改为仅在
   最终 PCM→AAC 编码前统一衰减 2 dB；不分镜重编码、不在切点拼音频，以保持连续参考
   音轨。实测峰值降至 0.898943，削波样本为 0，源 PCM 相关性仍为 0.999890。
5. 独立 Delivery Eval 原先固定三列拼接 192 张长片证据，产生 2160×81920 JPEG，超过
   JPEG 65500 像素维度限制。接触表改为按最大行数动态增加列数并把最长边限制为
   16000 像素，同时逐张加载，避免长片一次驻留全部图片。
6. 旧评测把 AI 生成画面与独立对白音频视为“不同源即失配”，也把连续参考音轨上的
   每个视觉切点当成音频拼接点。新增 `generated_lipsync` 合同：要求 AI 路由、有效时间
   偏移和人工口型审查；音频边界只在素材或时间不连续时检查。
7. 最终语义审查第一次返回 `NEEDS_REVIEW`。根因不是 257 条结论，而是两个工作目录
   解析 `Z` 时区后缀的 Python 版本不同；评测环境不接受 `Z`。统一写为 ISO 8601
   `+00:00` 后，验证器识别人工身份和时区并通过。没有使用 override 降低门槛。
8. 全量回归发现 compose 状态机把“上游参考已失效、但历史发布仍存在”误报为
   `missing`。原因是此前为解决“当前 final 被旧 stale pilot 拖累”而按最高就绪层级
   判定，却把无就绪层级的末分支过度简化。修正为：最高当前版本优先；无发布才是
   `missing`；已有发布但依赖失效必须是 `stale`。

结果：

- 最终 release 为 `final-yqhz4h9z`：37 镜、36 个零帧偏差切点、77.233333 秒画面、
  77.229002 秒音频、720×1280、30 fps、H.264/AAC；无黑段、异常冻结或源画面直接复制。
- 人工查看全片接触表、节拍接触表、7 张逐镜审查板、111 张 Q1/MID/Q3 证据和 R035→
  R036→R037 精确切点；R036 最终只有两只前爪、一个惠灵顿，食物不复制、不漂移。
- Delivery Eval 技术检查 PASS，257/257 项语义检查 PASS，override 状态为 ABSENT。
  三条广告模板 P2 提示保留为非阻塞信息，因为本片是剧情复刻而非商品转化广告。
- 密封母版 SHA-256 为
  `112eb3977e1df8cee850f06b6e42094bbd68d15252f600601cd20895d2cb16f3`；密封包视频与
  评审母版逐字节一致。
- 主项目高风险回归 `215 passed`、全量回归 `2828 passed`；Delivery Eval 定向回归
  74 项、finalize/deliver 96 项及全部 `test_video_eval*.py` 160 项通过。两个工作树的
  语法与 `git diff --check` 均通过，主项目 Ruff 通过。
- 当前仍为本地评估版：画面是原创 AI 重拍，但声音保留参考视频音轨。公开或商业发布
  前必须替换为已清权的原创对白、音乐与音效，并复核口型方案许可。

## 2026-08-10 — 面试猫 V2 多肢体与物理因果局部修复

发现的问题：

1. S004 手机按键特写同时出现猫头、按键爪和从右上延伸的长前肢，参考镜头原本只露
   一只画外进入的爪；模型在主体身体被裁掉时错误补全了肢体归属。
2. S012 抱文件镜头把动作前后的接触姿态并置，生成四只前爪；S018 又把纸杯碰倒前后
   两个状态同时画成两个杯子。三处都说明只检查首帧和普通缩略图不足以发现中间帧坏例。
3. 通用锚点提示禁止所有文字，与 S004 必须保留手机内“同意面试”界面冲突；原命令还
   会顺带尝试重跑其他缓存过期镜头，不适合小范围修复。

思考与处理：

- 采用局部重生成，不做遮罩修补，也不重做其余 40 镜。S004 改成手机特写且全程只露
  一只连接画外身体的前爪；S012 固定可见前爪总数为两只并持续支撑同一份文件；S018
  固定纸杯总数为一个，并明确“接触一次→绕底缘倾倒→短距离滑动→停止”的重力链。
- 新增 `preserve_source_ui_text`，允许保留设备内部界面，同时继续移除视频字幕、水印和
  外部覆盖文字；新增 `--shots` 局部生成参数，只让 S004/S012/S018 的缓存失效。
- 每镜先检查原尺寸锚点，再按 8 fps 展开 32 帧动态表；合成后再次检查问题镜头及其前后
  转场。旧封装包保持不变，新版本独立进入 Delivery Eval。

结果：

- 三镜均未再出现额外肢体、脱离身体的爪、重复文件或重复纸杯；纸杯运动保持单物体、
  单次接触和连续重力响应，三个镜头接回整片后的前后转场无新增跳变。
- 新版仍为 1076×1920、30 fps、6143 帧、204.766667 秒；封装母版与工作母版 SHA-256
  均为 `fd87b6a7049d6223c9b2c834628db5606466bcc65c987e2d748ef8d108ca5c57`，解码音频哈希
  与源片一致。
- Delivery Eval 技术与语义审核 PASS；全量回归 `2844 passed`，专项源锁定流程
  `16 passed`，`git diff --check` 通过。新版封装目录为 `sealed_delivery_v2_20260810`。

## 2026-08-10 — 面试猫 V3 动作卡顿与剧情过渡优化

发现的问题：

1. 43 个生成镜头中有 42 个实际为 24 fps，旧合成统一使用 `fps=30`，通过周期性复制
   原帧补足帧率。成片尺寸和总帧数虽然正确，但动作中会出现短暂停顿，尤其在抬爪、
   转头和走动镜头里更明显。
2. 全片所有镜头都使用硬切；在地点、时间或动作阶段同时变化的剧情边界，前后亮度和
   构图差异较大，观看时会产生突然跳到下一场的感觉。
3. 0.1 秒交叉叠化样片虽然降低了视觉突变，却让切点两侧的猫和手机同时半透明出现，
   形成重影，不符合真实摄影或当前叙事风格，因此没有用于正式成片。

思考与处理：

- 合成前逐镜探测真实帧率。低于 30 fps 的镜头使用双向运动估计补到 30 fps；原生
  30 fps 的 S007 保持原样，避免无意义的二次计算和画面改写。
- 不把过渡统一铺到每个切点，只选择 S004、S007、S012、S016、S020、S026、S032、
  S037 之后的八个主要剧情边界。每侧各用两帧淡入或淡出，并在中间保留一帧完整黑场，
  让地点或时间变化获得视觉停顿，同时不让两个主体、肢体或道具重叠。
- 对 S004、S012、S018、S025、S043 展开动作密集帧检查，确认运动补帧没有重新引入
  额外前爪、脸部扭曲、纸杯复制、文件变形或物体穿插；再检查八个过渡的前后帧和整片
  节拍联系表。

效果：

- 低差异相邻帧由 861 组降至 229 组，减少约 73%；中位帧间变化从 0.595 提升到
  0.885，抬爪、转身和行走段的周期性停顿显著减少。
- 八个剧情边界各产生一个 0.033333 秒完整黑帧；除此以外没有新增黑段。交叉叠化的
  半透明人物、猫和手机重影没有进入正式版本。
- 成片仍为 1076×1920、30 fps、6143 帧、204.766667 秒；解码音频 SHA-256 与源片
  同为 `1e0d5fdc445a9898a57fb9b21d8903cec3de94fd7dd138a9580b4c9b949cfe48`。
- Delivery Eval 技术与 62 项语义审核 PASS；封存母版 SHA-256 为
  `bc1867edd26f9b0d0892fbc9ebbe40ecb38552cebb588b3b8d3cbec1f3106efe`，封存目录为
  `sealed_delivery_v3_1_20260810`。V3.1 只修正质量报告中对八个单帧过渡黑场的描述，
  视频内容和母版哈希不变。

## 2026-08-13 — 统一九阶段生产核心与代码清理

发现的问题：

1. 小说、原创宠物短剧和参考复刻分别积累了项目 Runner、队列 Worker、LumenX 控制面、
   longform、speaking A/B 等多套调度壳；相同的项目状态、重试和交付判断被重复实现。
2. 视频任务仍以 LumenX 命名，Provider 配置强制依赖 LumenX 目录；README 和部署文档继续
   引导使用已过时的 `run-project`、`enqueue` 和 Worker，容易让新项目走回旧线路。
3. 部分角色素材指引和配置写死本机绝对路径；OpenMontage 未安装时连中立剪辑包都无法
   生成，不利于迁移和开源。

思考与处理：

- 把业务收束为 `构思 -> 剧本 -> 分镜 -> 素材 -> 音频 -> 视频 -> 剪辑 -> EVAL -> 交付`
  一条主流程，`original`、`novel`、`replica` 只覆盖输入解析和专业阶段策略。
- 保留网关文本/图像/Seedance、豆包多角色 TTS、OpenMontage/FFmpeg、宠物复刻、口型、
  物理连续性和 EVAL 能力；删除重复的旧调度、实验外壳和 LumenX 专属运行/控制模块。
- 新增中立 `video_handoff.json`，网关批处理仅对旧 LumenX handoff 保留只读兼容；共享
  JSON/文本原子写入和哈希逻辑集中到 `file_io.py`。
- 配置和生成命令改为可移植相对路径；OpenMontage 缺席时使用 FFmpeg 路线并如实标记，
  不再阻止阶段产物生成。重写 README、部署说明和代码地图，完整历史迭代日志继续保留。

效果：

- 删除约 2.9 万行重复实现与对应旧测试，总入口只保留统一项目命令和必要的专业诊断命令。
- Python Ruff、compileall 与全量 `2615 passed`；前端生产构建完成，4 项页面测试通过。
- 发布审计未发现真实密钥；模型权重、生成媒体、队列历史、缓存和依赖目录均排除在新仓库
  之外。旧 GitHub 仓库保持不变，新项目使用无旧提交历史的干净快照发布。
## 2026-08-14 - MiniMax H3 official video provider

### Problem

- The unified pipeline could only execute video through the internal gateway/Seedance route.
- MiniMax H3 uses `/v2/video_generation`, a nested asynchronous task response, and provider-specific limits that did not fit the previous request contract.
- The first live request exposed a contract mismatch: the service requires `ratio`; sending `aspect_ratio` was interpreted as an omitted ratio and rejected before generation.

### Reasoning and correction

- Kept the nine-stage pipeline unchanged and added H3 as a replaceable video adapter instead of creating another production path.
- Reused the hardened timeout, retry, download validation, state recovery, and secret-redaction behavior from the existing video client.
- Added tests before implementation for provider selection, request shape, 4-15 second and 768P/2K constraints, reference assets, nested polling, usage/cost reporting, CLI defaults, and credential redaction.
- Captured the real HTTP 400 response as a regression test, then changed only the request field from `aspect_ratio` to `ratio`.

### Result

- `VIDEO_PROVIDER=minimax` now selects `MiniMax-H3` through the same storyboard-to-video and batch execution path.
- `video-generate` and `video-batch` are neutral aliases; existing gateway command names remain compatible.
- A live 4-second 768P probe completed successfully, returned an H.264/AAC 768x1344 clip, and reported four output seconds with an estimated cost of CNY 2.00.
- The contact-sheet review showed one stable black-and-white cat, a fixed warm wooden room, one continuous paw-raise action, no extra limbs, and no scene cut.
- Independent review then hardened five edge cases: billable H3 POST requests cannot fall back to an automatic curl resubmission; resume state restores resolution/duration/reference counts for 2K cost reporting; remote reference audio is represented only by a SHA-256 digest; the adapter canonicalizes and enforces `MiniMax-H3`; and unsupported providers return structured CLI blockers instead of crashing.

## 2026-08-15 - MiniMax H3 official prompt protocol

### Problem

- The H3 transport was live, but StoryMotion still sent the same flat visual prompt used by Seedance.
- Character reference images were all marked as generic references, so direct first-frame and first/last-frame generation could not express their actual role.
- Dialogue had no stable speaker IDs, language tags, mouth-open window, sentence-end closure, or narrator closed-mouth rule.

### Reasoning and correction

- Kept the upstream MiniMax-H3 checkout independent at `/Users/tml/Desktop/MiniMax-H3`; the checkout is a specification/reference source, not a vendored runtime dependency.
- Added a provider-specific compiler following the official H3 base and Ref2VA field order. Reference prompts define stable `<Subject N>` and `<Picture N>` labels, retention rules, playback-order action, `(S1)` speakers, exact `<d>[Chinese] ...</d>` dialogue, soundscape, and no default non-diegetic music.
- Selected the H3 compiler only when the resolved provider/model is `minimax`/`MiniMax-H3`. Gateway and Seedance keep their previous prompt contract.
- Added typed H3 image inputs for `first_frame`, `last_frame`, and `reference_image`, including duplicate-keyframe and unsupported-role validation. Plain image paths remain backward compatible as `reference_image`.
- Kept final voice identity under the existing approved Doubao TTS pipeline. H3-generated sound is not mixed on top of the final voice track; exact word-level lip sync remains a separate post-process and EVAL responsibility.

### Result

- Focused H3 compiler, provider, handoff, batch, and pipeline regression tests passed; the final full regression finished with `2651 passed`.
- Generated H3 prompts now expose auditable official sections instead of an unstructured sentence, while preserving Chinese dialogue text and explicit mouth behavior.
- First-frame and last-frame roles are now available through the H3 adapter without breaking existing reference-image callers.

## 2026-08-15 - Production pipeline hardening

### 发现的问题

1. 阶段缓存默认版本长期不变，代码实现升级后可能继续复用旧产物；项目级视频模型覆盖也没有贯穿 handoff 和实际客户端。
2. 首帧、尾帧和角色参考图进入批处理后丢失语义角色；分镜靠提示词中的名字反推出镜人物，空镜和无台词角色容易绑定错误。
3. TTS 只在整集时间线上存在，视频生成任务没有逐镜对白音频；H3 原生声音状态也没有如实进入最终混音策略。
4. 云端片段使用 concat stream-copy 拼接并以 `-shortest` 截止，混合帧率、分辨率、像素比例或尾部时长时可能卡顿、失败或吞掉结尾。
5. 通用模式和复刻模式的 EVAL 语义不一致，客观媒体失败有时仍会进入人工审批；CLI 又直接调用大量宠物模块私有符号，维护边界不稳定。

### 思考与处理

- 给所有九阶段步骤设置显式实现版本，让版本、输入和配置共同参与阶段签名；只使变化阶段及下游失效。
- 在共享 `Shot` 契约加入 `character_ids`，旧项目读取时安全迁移；从剧本规划阶段一次确定出镜角色，Provider 层不再猜测名字。
- 视频任务保存图片路径与 `first_frame`、`last_frame`、`reference_image` 角色，并把有效 Provider/模型、图片角色和音频摘要一起写入签名。
- 从已测量的 TTS 时间线无损切出对白镜头 WAV，传给支持参考音频的 Provider；不支持时明确标记后处理口型策略，不虚报原生同步。
- 新增确定性媒体装配层：逐镜统一 CFR、尺寸、像素格式和 SAR，移除云端原音后按分镜时长补齐或裁切，再按明确目标时长合入最终音轨和字幕。
- 新增统一 `eval.v2` 客观门禁；无效视频、缺失音轨、时长漂移、对白重叠、镜头数不符和生成失败直接阻断，主观画面问题才交给人工审核。
- 用白名单动态服务面承接宠物专业模块能力。CLI 只调用公开名称，同时保留旧扩展需要的兼容入口；共享 JSON 读取集中到 `file_io.py`。

### 结果

- H3 图片角色、项目级模型覆盖、逐镜音频、任务恢复与缓存失效都具备回归测试；旧纯路径和旧 Episode JSON 仍可读取。
- 混合编码、分辨率、帧率和像素比例的真实 FFmpeg 小样可以稳定拼接，最终输出不再依赖 `-shortest`。
- Ruff、compileall、`git diff --check` 和 152 项核心回归通过；全量回归 `2673 passed`，零失败。
- MiniMax 上游仓库、模型权重、生成媒体和凭据均未并入 StoryMotion Studio；旧 GitHub 项目未删除或改写。

## 2026-08-16 - 本机制作工作台发布

### 发现的问题

1. 九阶段生产核心已有完整项目、审批、返修、收费门禁和作品合同，但缺少一个可靠的本机入口；前端代理还固定指向单一 API 端口，端口占用时无法整体启动。
2. 自动化浏览器覆盖停留在媒体检查，未完整走通项目创建、本地阶段、严格审批、退回持久化、局部返修、视频测试门禁、失败任务恢复和历史作品下载。
3. 旧展示站仍包含页面、Cloudflare Worker、D1、Vinext 配置和重复媒体。删除前必须证明 7 个公开文件均已进入可独立重建的受控归档。
4. 真实 Chrome QA 发现项目 ID 的 HTML 正则在 Unicode `v` 模式下无效、移动端新建按钮隐藏了加号，以及缺省 favicon 请求产生 404 控制台错误。

### 思考与处理

- 新增安全启动器，确定性选择两个不同的空闲回环端口，分别启动 Uvicorn 与 Vite，等待 API/页面就绪后才报告地址。父进程屏蔽子进程输出，不打印环境值；信号关闭使用有界等待并只清理自己创建的进程组。
- 前端增加待运行阶段的本机执行入口，通过正式 `runStage`/`getJob` 合同轮询并重载权威修订；视频收费入口仍只允许经过预检、确认令牌和测试/批量提交。
- 新增完整离线 Playwright 流程，在 1440x900、1024x768、390x844 覆盖创建、执行、审批、退回、影响应用、刷新恢复、作品、下载、设置和安全错误/空状态。浏览器夹具不绕过生产组件，也不访问外部网络。
- 用真实启动器和 Chrome 检查项目、工作区、作品列表、图像/音频详情与设置。修复无效正则、移动端图标按钮和 favicon 404，并重新检查控制台、失败请求、外部请求、横向溢出、控件裁切、键盘焦点及媒体尺寸。
- 删除前核验 7 个旧 public 文件均被 Git 跟踪，源文件、迁移清单和 tracked archive 的 SHA-256 全部一致；API 完成 7 次预览 HEAD、7 次 Range 读取和 7 次附件 HEAD，干净副本只靠 `assets/workbench_archive` 重建 2 件历史作品。

### 结果

- 旧 `sites/pet-video-showcase/` 页面、Worker、D1、Vinext 配置和 rendered-HTML 测试在迁移门禁通过后删除；`assets/workbench_archive/`、迁移工具和上游 GitHub 记录保留。
- 真实 QA 三种视口均无控制台错误、非取消类网络失败、外部请求、页面横向溢出、控件裁切或零尺寸媒体；图片像素有效，音频控件可见，键盘焦点明确。
- 7 个历史 payload 继续显示 `unverified` 权利警告。3 个音频样本在权利确认或排除前不适合公开发布，新 GitHub 仓库默认保持私有。
- 发布矩阵通过：Ruff、compileall、`git diff --check`、后端 3,008 项、前端 97 项、TypeScript、ESLint、Vite 构建和三种视口 Playwright 12 项全部成功；仅保留既有依赖弃用提示和 JSDOM 媒体方法提示。

## 2026-08-16 - Task 10 独立审查修复

### 发现的问题

1. 测试曾把一项真实凭据当作脱敏样例，当前文件虽可替换，旧 Git 历史仍不可发布。
2. 干净副本没有前端依赖，启动器存在端口探测与子进程绑定之间的竞争；前端子进程还继承了不需要的 Provider 凭据。
3. 浏览器验收拦截全部 `/api`，没有覆盖真实项目文件、审核、返修、作业、媒体授权和下载；本地阶段刷新后也无法可靠恢复。
4. 网关配置含私有默认地址，当前文档仍混有退役运行时建议和个人 checkout 路径。
5. 精确 390x844 QA 暴露了长成果页自动滚动时，阶段按钮可能落入顶部 sticky 区域或底部固定导航之下的问题。

### 修复与验证

- 当前 tracked tree 全部改用明显虚构哨兵；新增不打印值的确定性内容/历史扫描器，以及只从 `git archive HEAD` 生成单提交新历史的 clean release 导出器。旧源历史禁止推送，凭据仍需账户持有人在外部撤销或轮换。
- Bootstrap 验证 Node.js/npm 并执行 lockfile 驱动的 `npm ci`。启动器对 API 或页面端口竞争都重选整组端口并有界重试，只清理自有子进程；Vite 环境改为白名单。
- Playwright 使用真实临时 FastAPI 与 `WorkbenchService`，仅替换收费视频渲染边界。项目、九阶段、严格审核、整阶段退回、局部返修、一次性确认令牌、失败任务恢复、作品、Range、下载和设置全部落到真实持久化合同。
- 工作区合同公开 active/recoverable `run_stage` job；刷新、StrictMode、路由切换和瞬时读取失败都连接同一作业，不重复提交。SSE 正常关闭后立即回读持久状态。
- 网关改为必须显式配置 `GATEWAY_BASE_URL`，仓库只使用 `example.invalid`。退役评估移入历史归档，当前操作文档和代码地图不再包含个人 checkout 路径或旧生产建议。
- 移动端补充顶部/底部滚动保留区，正常指针点击在 390x844 通过。六张临时 QA 截图显示三种视口无横向溢出、控件裁切、外部请求或意外控制台错误，未核验权利警告持续可见。
- 最终矩阵：后端 3,026 项、前端 102 项、真实 Playwright 15 项、launcher/bootstrap/security/catalog 107 项，以及 Ruff、compileall、TypeScript、ESLint、Vite build 和 diff 检查全部通过。旧站 7 个源 blob、manifest 与 tracked archive 哈希再次 7/7 一致，8 个归档文件和 7 条 `unverified` 权利记录保持不变。
- 2026-08-16: 按最终交付范围将工作台收敛为本机桌面网页端。移除手机/平板专用底部导航、阶段抽屉、响应式样式和多视口 Playwright 项目，仅保留 `1440x900` 桌面验收；同时为干净发布导出补充直接命令回归测试，避免模块导入测试通过但文档命令无法运行。
- 真实桌面浏览器验收发现创建任务已经完成后，关闭成功弹窗仍保留旧项目列表。成功关闭现在会重新读取本地项目，项目卡片无需手动刷新即可出现；项目三栏工作区、离线概念阶段、作品中心和设置页均无横向溢出、控件裁切或页面控制台错误。

## 2026-08-18 - H3 动作节奏约束

### 发现的问题

- 旧 H3 提示词只要求动作“物理连续”，模型容易把连续误解为全程匀速补间。杯子摔碎后，人物的停顿、观察、撑地、落脚、重心转移和取粮同时发生，形成缺少发力过程的滑行动作。

### 思考与处理

- 把动作要求从抽象形容词改成因果拍点：起始姿态与触发、短暂反应、支撑或接触、重心或物体轨迹、结果停稳。
- 明确动作可以连续，但速度不能始终均匀；要求自然加速、减速和短停顿，并禁止匀速补间、漂浮、滑行、瞬移和过度平滑插值。
- 将规则写入 H3 统一提示词编译器，而不是只修单个样片；新增回归测试，确保文本与参考图两类 H3 提示词都会继承该规则。

### 结果

- 杯碎至取粮返修版按“落定、看猫、叹气、撑地、落脚、起身、取粮”推进，技术检查与 38 项逐段语义检查通过。
- H3 编译器、视频 handoff 与 Provider 相关 37 项回归测试通过。

## 2026-08-31 - 音频优先微分镜生产线重构

### 发现的问题

- 原流程把 9–10 秒的独立镜头、动作、对白和反应混在一起，生成画面与后贴配音脱节，角色物理关系、口型和连续性不稳定。
- 仅靠提示词无法保证音频、角色身份、场景关键帧、上一镜末帧和审核证据一致；未通过审核的素材仍可能被剪辑器读取。

### 迭代过程

1. 新增表演卡：每个微镜头只承担一个目的，并绑定唯一说话者、台词、动作节拍、接触点和负面约束。
2. 改为最终音频优先：豆包角色对白先生成、切为不可变哈希资产；可见说话镜头必须携带匹配音频。
3. 增加悟空、杨戬、哪吒三角色口型试片。模型只有三项全部通过且整体质量通过，才能生成正脸对白；否则只能生成动作镜头。
4. 视频请求强制携带角色图、场景关键帧、同场最近已批准末帧和能力证明；任一证据改变，旧远端任务不能复用。
5. 候选状态被限定为 `planned → audio_ready → submitted → rendered → review_required → approved | rejected | blocked`。剪辑只读取已批准清单。
6. 审核会锁定视频生成作业、九张视觉采样帧、最终音频、锚点和口型评分的哈希；无对白动作镜头同样不能绕过来源验证。
7. 新增本地预检：在任何付费调用之前检查音频、模型能力、关键帧、锚点和审核链，且不会覆盖第 1 集既有 V1/V2/V3 成片。

### 结果与当前边界

- 本次重构仅改造生产门禁、提示词、审核和预检流程；没有重新生成视频、没有调用网关、没有产生新费用。
- 本地验证覆盖 757 项通过、1 项跳过；Ruff 与 `git diff --check` 通过。
- 后续正式生成仍需单独授权付费批次，并先通过本地预检与三角色口型能力测试。
