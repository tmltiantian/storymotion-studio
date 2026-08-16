# Gateway Provider Integration Design

## Goal

Replace the factory's single-provider `DASHSCOPE_API_KEY` assumption with a
capability-aware provider profile that can use the Onion internal AI gateway
for text and image generation, probe gateway video generation safely, and keep
the existing local TTS and static-role preview path available.

The integration must never claim character-consistent image or video support
unless the selected endpoint has been verified to accept the required role
references.

## Verified Gateway Capabilities

The model marketplace was inspected on 2026-07-10 through its public UI.

| Capability | Verified endpoint | Models relevant to the factory | Decision |
| --- | --- | --- | --- |
| Text | `/v1/chat/completions` | `qwen3.6-plus`, `qwen3.7-max`, other OpenAI-compatible chat models | Production integration |
| Image | `/v1/images/generations` | `qwen-image-2.0`, `doubao-seedream-4-0`, `doubao-seedream-4-5`, `doubao-seedream-5-0-lite`, `gpt-image-1.5`, `gpt-image-2` | Production text-to-image integration |
| Video | `/v1/video/generations` | `doubao-seedance-1-5-pro`, `doubao-seedance-2-0`, `doubao-seedance-2-0-fast`, `wan2.7-t2v` | Experimental probe first |
| TTS | No dedicated speech endpoint or TTS model found | The `audio` tag identifies audio-capable LLMs, not speech synthesis | Keep local TTS |

The image model detail page documents prompt, size, quality, style, count, and
response format. It does not document reference-image input. The video detail
page exposes the endpoint but shows a generic chat-style example that does not
describe a trustworthy image-to-video contract. These limitations are treated
as capability gaps, not silently ignored inputs.

## Alternatives Considered

### 1. Replace DashScope throughout LumenX

Patch every image, video, TTS, registry, catalog, and settings path to treat the
gateway as a complete DashScope substitute.

This has the largest blast radius and would encode unverified assumptions about
reference-image, asynchronous-task, and TTS behavior. It is rejected for the
first gateway iteration.

### 2. Add capability-scoped gateway adapters

Use LumenX's existing OpenAI-compatible LLM adapter, add a gateway text-to-image
adapter for the verified image endpoint, and add a separate video probe that
does not become production routing until a real smoke test passes. Keep local
TTS and the reviewed-role preview fallback.

This is the recommended approach. It delivers useful generation immediately
while making unsupported capabilities visible.

### 3. Put a compatibility proxy in front of LumenX

Create a local service that imitates DashScope endpoints and translates calls
to the gateway.

This would avoid some upstream edits but adds another deployed service and
still cannot translate undocumented video or reference-image behavior. It is
deferred unless multiple upstream applications later need the same bridge.

## Provider Profile

The factory will resolve one provider profile from process variables and
`external/lumenx/.env`. Existing DashScope settings remain supported.

```dotenv
LLM_PROVIDER=openai
OPENAI_BASE_URL=https://gateway.example.invalid/v1
OPENAI_MODEL=qwen3.6-plus

GATEWAY_API_KEY=
GATEWAY_BASE_URL=https://gateway.example.invalid
IMAGE_PROVIDER=gateway
GATEWAY_IMAGE_MODEL=qwen-image-2.0

VIDEO_PROVIDER=gateway
GATEWAY_VIDEO_MODEL=doubao-seedance-2-0-fast
ENABLE_GATEWAY_VIDEO=0

TTS_PROVIDER=auto
DOUBAO_SPEECH_API_KEY=
DOUBAO_SPEECH_VOICE_TYPE=
```

`GATEWAY_API_KEY` is the preferred single secret. The text adapter may use
`OPENAI_API_KEY` first for compatibility and fall back to `GATEWAY_API_KEY`.
Gateway image and video clients use `GATEWAY_API_KEY` first and may fall back to
`OPENAI_API_KEY`. Secret values are never written to reports.

## Architecture

### Factory Provider Resolution

Add a small provider-profile module under `factory/` that:

- Loads provider names, model names, base URLs, and key presence.
- Describes required credentials by stage instead of globally.
- Reports supported, degraded, experimental, and blocked capabilities.
- Preserves the current dotenv-over-process behavior rules where they are
  already part of the factory contract.

`env_readiness.json` moves to a v2 schema while keeping the existing top-level
`required`, `optional`, and `ready_for_lumenx_generation` fields for downstream
compatibility. It adds a `providers` section and stage-specific readiness.

### Text Generation

Reuse `LLMAdapter` with the OpenAI-compatible endpoint. The adapter will resolve
the API key from `OPENAI_API_KEY` or `GATEWAY_API_KEY` and use
`OPENAI_BASE_URL`. `qwen3.6-plus` is the default documented example because the
marketplace confirms chat completion and JSON response-format support.

Text extraction is ready when the selected provider has a key, base URL, and
model. A missing text key does not disable deterministic dry-run episode
generation.

### Image Generation

Add a `GatewayImageModel` implementing LumenX's existing `ImageGenModel`
interface. It will:

- POST to `/v1/images/generations`.
- Normalize LumenX sizes from `WIDTH*HEIGHT` to `WIDTHxHEIGHT`.
- Request one URL result by default.
- Download the returned image to the requested output path.
- Produce concise errors for authentication, validation, timeout, and malformed
  responses.

The adapter declares that reference images are unsupported. Calls with
`ref_image_path` or `ref_image_paths` fail before network access with a clear
capability error. The factory then keeps the existing reviewed-role static
preview/compositing path instead of silently generating inconsistent roles.

Gateway text-to-image is suitable for new character reference sheets,
backgrounds, scenes without references, and exploratory storyboard images.

### Video Generation

Add a gateway video probe client, not an automatically enabled production
route. The probe will:

- POST a minimal text-to-video request to `/v1/video/generations`.
- Record sanitized HTTP status, response shape, task identifiers, and polling
  hints without recording the token.
- Parse common immediate-URL and asynchronous-task response shapes.
- Write a structured report under `runs/<project_id>/`.

`ENABLE_GATEWAY_VIDEO=1` is necessary but not sufficient for production video
routing. A successful probe report for the selected model is also required.
Image-to-video remains unsupported until a real response contract proves how a
first-frame or role reference is supplied.

Until then, the factory continues to deliver its current FFmpeg/OpenMontage
motion preview.

### TTS

Reuse the Doubao Speech 2.0 configuration and async provider contract from the
configured OpenMontage checkout. The factory reads `DOUBAO_SPEECH_API_KEY` and
`DOUBAO_SPEECH_VOICE_TYPE` from process variables first, then from the factory
dotenv, and finally from `<openMontage>/.env`. It does not duplicate or log the
secret value.

`TTS_PROVIDER=auto` selects Doubao when both settings are present and otherwise
uses the existing macOS voiceover path. `TTS_PROVIDER=doubao` makes a missing
Doubao setting an explicit blocker. `TTS_PROVIDER=local` always uses the local
fallback.

The Doubao adapter uses the new-console `X-Api-Key` flow with resource
`seed-tts-2.0`, submits through `/api/v3/tts/submit`, polls
`/api/v3/tts/query`, downloads one audio clip per cue, and preserves the query
JSON containing sentence and word timestamps. Existing FFmpeg cue alignment,
mixing, and preview muxing remain unchanged.

## Stage Readiness

Readiness is computed for the selected stages:

- `assets`: requires the selected image provider credential; gateway mode is
  ready for text-to-image only.
- `storyboard`: requires the image credential. Reference-dependent frames are
  marked degraded and use the reviewed-role fallback.
- `audio`: auto mode is ready through Doubao when its OpenMontage configuration
  is present and otherwise through the local fallback; strict Doubao mode
  requires its API key and default voice.
- `video`: gateway mode requires a key, the enable flag, and a successful probe;
  otherwise the local motion preview remains available.

Start gates and operator handoff text will list the actual missing variable or
capability. They will no longer report `DASHSCOPE_API_KEY is missing` when the
selected stages use only gateway and local providers.

## Error Handling

- Never log or serialize API-key values.
- Fail before network access when a requested capability is unsupported.
- Treat HTTP 401/403 as credential errors, 429 as rate limiting, and 5xx as
  provider failures.
- Use bounded request and download timeouts.
- Preserve all existing dry-run and static-preview artifacts when a cloud stage
  fails.
- Do not mark video production-ready from marketplace metadata alone.

## Testing

Add focused tests for:

- Provider profile and stage-specific key requirements.
- `OPENAI_API_KEY` and `GATEWAY_API_KEY` alias resolution without exposing
  values.
- LLM gateway configuration and JSON response-format forwarding.
- Image size normalization, request payload, URL response parsing, download,
  and reference-image rejection using mocked HTTP calls.
- Video probe immediate, asynchronous, authentication-failure, and malformed
  response reports using mocked HTTP calls.
- Doubao configuration precedence, request headers/body, async polling, audio
  download, timestamp metadata, secret redaction, and automatic local fallback
  using mocked HTTP calls.
- Start gate, preflight, workflow status, operator handoff, and dashboard output
  under gateway/local and legacy DashScope configurations.
- Existing full test suite to protect the dry-run and preview workflow.

## Acceptance Criteria

- The factory can report text, image, audio, and video readiness independently.
- Selecting gateway text and image providers does not require
  `DASHSCOPE_API_KEY`.
- Local TTS remains ready without a cloud credential.
- The factory can reuse the existing OpenMontage Doubao Speech settings without
  copying secrets into tracked files.
- Gateway text-to-image calls use the verified OpenAI-compatible endpoint.
- Reference-dependent image calls never discard references silently.
- Gateway video cannot enter production routing before a successful live probe.
- Existing sample preview and mock LumenX tests continue to pass.
