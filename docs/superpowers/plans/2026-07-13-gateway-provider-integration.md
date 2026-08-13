# Gateway Provider Integration Plan

**Goal:** Replace the factory's global DashScope credential gate with capability-aware routing for the Onion AI gateway, Doubao TTS, and the local/OpenMontage fallback pipeline.

**Architecture:** Keep the vendored LumenX checkout unmodified. Add provider resolution and gateway clients in the outer factory, pass OpenAI-compatible text settings into the LumenX backend process, generate gateway image assets through factory CLI commands, and keep gateway video behind a sanitized live probe. Existing local previews remain available when a cloud capability is unavailable.

**Tech Stack:** Python 3.12, standard-library HTTP clients, pytest, shell launch scripts, FFmpeg/OpenMontage.

---

### Task 1: Provider profile and stage readiness

**Files:**
- Create: `factory/provider_profile.py`
- Create: `tests/test_provider_profile.py`
- Modify: `.env.example`

1. Write failing tests for dotenv precedence, gateway key aliases, default models, secret redaction, and per-capability readiness.
2. Implement a provider profile that reads process, factory, LumenX, and OpenMontage environments without serializing secrets.
3. Make gateway text/image readiness independent of `DASHSCOPE_API_KEY`.
4. Run the focused tests.

### Task 2: Gateway image client

**Files:**
- Create: `factory/gateway_image.py`
- Create: `tests/test_gateway_image.py`

1. Write failing tests for `WIDTH*HEIGHT` normalization, bearer authentication, request payloads, URL and base64 responses, bounded downloads, and rejected reference inputs.
2. Implement synchronous `/v1/images/generations` calls and safe error mapping.
3. Ensure reports contain model, timing, and output paths but never credentials.
4. Run the focused tests.

### Task 3: Gateway text smoke and video probe

**Files:**
- Create: `factory/gateway_text.py`
- Create: `factory/gateway_video.py`
- Create: `tests/test_gateway_text.py`
- Create: `tests/test_gateway_video.py`

1. Write mocked tests for OpenAI-compatible JSON chat completion.
2. Write mocked tests for video immediate URL, async task, authentication failure, and malformed response shapes.
3. Implement a low-cost text smoke helper and a non-production video probe.
4. Require an explicit live flag before either client accesses the network.

### Task 4: Provider-aware CLI and environment report

**Files:**
- Modify: `factory_cli.py`
- Modify: `factory/env_readiness.py`
- Modify: `tests/test_cli_env_report.py`
- Create: `tests/test_cli_gateway.py`

1. Add failing tests for `provider-report`, `gateway-text-smoke`, `gateway-image`, and `gateway-video-probe` commands.
2. Upgrade the environment report with provider and per-capability readiness while retaining legacy fields.
3. Add CLI commands with structured JSON output and explicit network-enabling flags.
4. Run the focused tests.

### Task 5: Provider-aware start gates and backend environment

**Files:**
- Modify: `factory/real_generation_start_gate.py`
- Modify: `factory/real_generation_preflight.py`
- Modify: `factory/lumenx_generation_guard.py`
- Modify: `factory/lumenx_adapter.py`
- Modify: `scripts/start_lumenx_backend.sh`
- Modify: affected tests under `tests/`

1. Add regression tests proving gateway/local selections no longer emit a DashScope blocker.
2. Resolve blockers from the selected stage provider and capability limitations.
3. Export `LLM_PROVIDER=openai`, gateway base URL, model, and key alias only in the child backend process.
4. Keep legacy DashScope behavior when explicitly selected.
5. Run all gate, handoff, script, and workflow tests.

### Task 6: Documentation and verification

**Files:**
- Modify: `README.md`
- Modify: `docs/deployment.md`
- Modify: `docs/iteration-log.md`

1. Document gateway configuration and credential placement without including a real key.
2. Run `python -m compileall -q factory factory_cli.py`.
3. Run the full pytest suite.
4. Run provider and environment reports against the local configuration.
5. Run only mocked/no-cost gateway tests until the user completes OIDC login and provides a gateway token through `.env`.
