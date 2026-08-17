# Creator-Friendly Stage Results Verification

Date: 2026-08-17

## Scope

This record verifies the creator-facing stage-result presentation introduced for the production workbench. The checks use the repository's isolated Playwright fixture API and Vite server at a 1440x900 desktop viewport. The browser flow blocks non-local network traffic, so it does not make provider calls.

## Automated Coverage

- Completed script stage: confirms the `剧本成果` heading and `主角A` creator label are visible; confirms no `pre` or `code` elements, schema/MIME/manifest text, or horizontal overflow.
- Completed concept, script, storyboard, EVAL, and delivery stages: exercised in the offline end-to-end production, approval, repair, recovery, and delivery flow.
- Video media inspection: exercises the media-only video viewer, candidate selection, frame controls, speed, mute, video-generation confirmation, reload, and time-coded review feedback with deterministic intercepted responses.
- Existing flow coverage also retains workbench, pipeline, provider recovery, authorization, approval, repair, EVAL, and delivery checks.

## Desktop Screenshots Inspected

The following viewport screenshots are emitted by the Playwright run under `sites/storymotion-studio/test-results/`:

- `workbench-flow-runs-the-re-3bfa0-ew-repair-and-recovery-flow-desktop/concept-stage-result.png`
- `workbench-flow-runs-the-re-3bfa0-ew-repair-and-recovery-flow-desktop/script-stage-result.png`
- `workbench-flow-runs-the-re-3bfa0-ew-repair-and-recovery-flow-desktop/storyboard-stage-result.png`
- `workbench-flow-runs-the-re-3bfa0-ew-repair-and-recovery-flow-desktop/eval-stage-result.png`
- `workbench-flow-runs-the-re-3bfa0-ew-repair-and-recovery-flow-desktop/delivery-stage-result.png`
- `workbench-media-video-insp-9fb2a-ain-usable-and-unobstructed-desktop/video-media-inspection.png`

At 1440x900, the inspected creator-result panels show readable Chinese labels, no clipping or overlapping controls, no horizontal overflow, and no exposed JSON, MIME types, or artifact filenames. The media check covers video because that is the fixture-backed media viewer available in the desktop suite; no unsupported project state was invented.

## Commands

Run from the repository root:

```sh
.venv/bin/pytest -q
```

Result: `3048 passed, 1 warning in 403.34s`.

Run from `sites/storymotion-studio`:

```sh
npm test -- --run
npm run typecheck
npm run lint
npm run build
npx playwright test tests/workbench-flow.spec.ts tests/workbench-media.spec.ts --project=desktop
```

Results: Vitest `11` files / `111` tests passed; typecheck, lint, and production build passed; Playwright `5` desktop tests passed in `35.2s`.

The Playwright configuration starts its own fixture API on port 18788 and Vite server on port 4175. It does not use or alter a live service on ports 5174 or 8799.

## Residual Limitations

The browser evidence uses deterministic offline fixtures and local media responses. It verifies the creator-facing presentation and interaction boundaries, not paid-provider rendering or externally hosted production media. The video-media and delivery screenshots show a black native-video surface because their fixtures do not provide decodable video bytes; controls and surrounding creator-facing content were inspected, but frame rendering is outside this fixture's coverage.
