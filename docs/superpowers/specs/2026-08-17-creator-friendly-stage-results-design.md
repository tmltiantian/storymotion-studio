# Creator-friendly stage results design

Date: 2026-08-17
Status: approved direction, pending implementation review

## Problem

The workbench currently treats JSON production artifacts as text files. A creator opening a stage sees schema names, internal identifiers, MIME types, filenames, and formatted JSON. These details are useful for debugging but do not help a user judge a story, character, shot, voice, or final video.

The user has explicitly chosen to remove raw JSON and technical file metadata from the frontend entirely. JSON remains an internal persistence format only.

## Product goal

Every stage result should answer the creator's review question in ordinary language:

| Stage | Review question | Primary presentation |
| --- | --- | --- |
| Concept | Is this the right story direction? | premise, mode, source, target format, character direction |
| Script | Is the story and dialogue right? | synopsis, character cards, story beats, dialogue |
| Storyboard | Will each shot connect and read clearly? | ordered shot list with action, camera, duration, dialogue, transition |
| Assets | Are characters, scenes, and props consistent? | categorized visual asset gallery and identity labels |
| Audio | Do voices, timing, and emotion work? | audio players, speaker labels, dialogue timing, voice notes |
| Video | Is each generated shot usable? | shot-grouped candidate players and review controls |
| Edit | Does the assembled episode flow? | main preview, duration, subtitle and soundtrack status |
| EVAL | What is wrong and what needs repair? | severity-grouped findings, affected shots, repair suggestions |
| Deliver | Is the result ready to publish? | final master, quality status, export actions |

## Chosen approach

Use a backend presentation boundary and stage-specific frontend views.

The backend continues reading authoritative stage artifacts, but converts them into a safe `StagePresentation` response. The frontend never downloads or renders a JSON artifact. It receives only fields intended for creators and renders a dedicated view for each stage.

This is preferred over a generic JSON renderer because field-name translation still exposes implementation structure. It is preferred over merely hiding JSON blocks because that would leave completed stages without meaningful results.

## Data contract

`StageDetail` gains an optional `presentation` field. The value is a discriminated object keyed by the fixed nine-stage name. Each presentation exposes only display-safe content. Internal fields such as `schema_version`, `project_id`, hashes, storage paths, executor names, MIME types, prompt payloads, and model request bodies are excluded.

Representative structures:

- `concept`: title, premise, mode label, source label, target duration/aspect ratio, character directions.
- `script`: title, synopsis, total duration, characters, ordered scenes, dialogue lines.
- `storyboard`: shots with index, scene title, action, camera, duration, dialogue, transition and continuity notes.
- `assets`: categorized items with display name, image artifact reference and consistency notes.
- `audio`: playable artifact references and registered dialogue timings.
- `video`: shot groups, candidate artifact references and registered timing metadata.
- `edit`: preview artifact reference and assembly facts.
- `eval`: checks, severity, findings, affected shots and suggested fixes.
- `deliver`: final output references, quality result and export labels.

Presentation builders are pure, stage-specific functions. Missing optional fields are omitted. Malformed or unsupported data returns an explicit user-facing empty state, never a raw-data fallback.

## Frontend behavior

The stage result area no longer displays artifact filenames or media types as captions. Media controls retain useful labels such as character name, shot number, candidate number, duration, and resolution.

Text-oriented results use these patterns:

- character cards for identity, role, description, appearance direction, and voice direction;
- a compact summary band for title, format, duration, and counts;
- an ordered scene or shot list for narrative review;
- dialogue rows that visually separate speaker, emotion, and line;
- plain-language empty and error states that explain what stage should be run or repaired.

The page remains desktop-only and follows the existing restrained workbench visual language. No nested cards, raw code blocks, developer terminology, or decorative dashboard widgets are introduced.

## Artifact access rules

- JSON and plain-text production manifests are not sent to the browser as viewable stage artifacts.
- The frontend contains no raw JSON viewer, source toggle, technical-details accordion, or download link for internal manifests.
- Image, audio, and video artifacts remain available through authorized media URLs.
- Unsupported binary deliverables can retain an explicit creator-facing export action when they are genuine outputs, not internal manifests.
- Existing backend files remain untouched so pipeline integrity and reproducibility are preserved.

## Failure handling

- Presentation parsing is defensive and schema-aware.
- A missing stage artifact produces “本阶段尚未生成可查看的成果”.
- An unreadable or incompatible artifact produces “成果暂时无法整理，请重新运行本阶段”.
- Unknown fields are ignored.
- Unknown JSON schemas never fall back to a `<pre>` element.
- One malformed optional section does not hide valid sections from the same stage.

## Testing

Backend tests verify each presentation builder, field allow-listing, malformed inputs, and the absence of technical fields in API responses.

Frontend tests verify creator labels and structured sections for concept, script, and storyboard first, then media and quality stages. Regression tests assert that no JSON text, MIME type, schema version, internal ID, or technical filename is rendered.

Playwright verifies the desktop stage review path at 1440x900, including long Chinese text, empty states, scrolling, media controls, and the absence of horizontal overflow.

## Acceptance criteria

1. A completed stage never shows raw JSON, a code block, a MIME type, or an internal manifest filename.
2. Concept, script, and storyboard results are understandable without knowing the pipeline data model.
3. Users can identify characters, dialogue, shot order, duration, and review targets directly in the interface.
4. Image, audio, and video review controls continue to work.
5. Unknown or malformed JSON produces a guided empty/error state rather than a technical fallback.
6. Existing stage execution, approval, repair, EVAL, and delivery behavior remains unchanged.

## Scope boundary

This change redesigns stage-result presentation only. It does not change generation prompts, stage execution, approval policy, provider configuration, artifact persistence, or pipeline schemas.
