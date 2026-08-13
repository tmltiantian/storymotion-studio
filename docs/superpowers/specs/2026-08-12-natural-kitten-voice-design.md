# Natural Kitten Voice Design

## Goal

Give the two pet characters a believable young-cat quality while preserving
clear Mandarin, natural acting, and stable character identity. The result must
sound like cute characters with feline habits, not pitch-shifted adults or
constant cartoon meowing.

## Character Direction

- Doubao, the black-and-white cat: soft, alert, curious, and slightly cautious.
  Use a light voice, gentle breath, short phrases, and a subtle upward turn on
  genuine questions.
- Naitang: lively, mischievous, and warm. Keep a little more energy and a
  slightly quicker response rhythm than Doubao, without rushing consonants.
- Each character keeps one immutable Seed-TTS 2.0 voice throughout an episode.

## Feline Language Rules

- Preserve story meaning and important evidence words exactly.
- Prefer short spoken clauses, natural pauses, small hesitations, and occasional
  quiet reactions such as `嗯`, `诶`, or `咪呜`.
- Use `喵` or another feline reaction at most once per three to five spoken
  lines, and only where the emotion motivates it.
- Do not append `喵` mechanically to every sentence.
- Do not describe physical actions inside spoken text. Actions remain in the
  shot prompt so the TTS does not read them aloud.

## Synthesis Strategy

- Keep the verified cute Seed-TTS 2.0 voices rather than applying an artificial
  pitch shifter.
- Use role-specific context instructions for softness, breath, response rhythm,
  and restrained feline reactions.
- Keep speech rate near natural conversation. Doubao is slightly slower;
  Naitang may be slightly quicker, but neither may sound hurried.
- Trim only leading and trailing silence. Do not time-stretch dialogue to fit a
  shot; adjust the line or shot timing instead.

## Evaluation

Generate one short A/B dialogue before changing production audio:

- A: current cute voices with improved natural acting instructions only.
- B: the same voices and meaning, with restrained feline wording and reactions.

Approve B only if both characters remain intelligible and distinct, the feline
quality is noticeable without sounding forced, there is no electronic pitch
effect, pauses feel conversational, and neither performance has an advertising
or announcer tone. The production pipeline must continue to reject voice drift,
role voice reuse, dialogue overlap, excessive leading/trailing silence, and
audio outside the assigned shot window.

## Scope

This change affects dialogue preparation, role-specific TTS context, voice
probes, and audio evaluation. It does not change image generation, video model
selection, lip-sync architecture, or final editing rules.
