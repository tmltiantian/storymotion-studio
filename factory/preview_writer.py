from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schema import Episode, NARRATOR_ID, episode_to_dict


def format_srt_time(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    hours = milliseconds // 3_600_000
    milliseconds %= 3_600_000
    minutes = milliseconds // 60_000
    milliseconds %= 60_000
    secs = milliseconds // 1000
    millis = milliseconds % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _speaker_name(episode: Episode, speaker_id: str) -> str:
    if speaker_id == NARRATOR_ID:
        return "旁白"
    for character in episode.characters:
        if character.id == speaker_id:
            return character.name
    return speaker_id


def write_storyboard_markdown(episode: Episode, output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {episode.title}",
        "",
        f"- Project: `{episode.project_id}`",
        f"- Style: {episode.style}",
        f"- Target: {episode.target_aspect_ratio} {episode.target_resolution}",
        "",
        "## Characters",
        "",
    ]
    for character in episode.characters:
        lines.extend(
            [
                f"### {character.name}",
                "",
                f"- Role: {character.role}",
                f"- Description: {character.description}",
                f"- Visual anchor: {character.visual_anchor}",
                f"- Voice: {character.voice_style}",
                "",
            ]
        )

    lines.extend(["## Shots", ""])
    for shot in episode.shots:
        lines.extend(
            [
                f"### {shot.index}. {shot.scene_title}",
                "",
                f"- Duration: {shot.duration_seconds:.1f}s",
                f"- Camera: {shot.camera}",
                f"- Audio mood: {shot.audio_mood}",
                f"- Action: {shot.action}",
                f"- Visual prompt: {shot.visual_prompt}",
                "",
                "Dialogue:",
                "",
            ]
        )
        for line in shot.dialogue:
            lines.append(f"- **{_speaker_name(episode, line.speaker_id)}** ({line.emotion}): {line.text}")
        lines.append("")

    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def write_subtitles(episode: Episode, output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    blocks: list[str] = []
    cursor = 0.0
    subtitle_index = 1
    for shot in episode.shots:
        lines = shot.dialogue or []
        per_line = shot.duration_seconds / max(1, len(lines))
        for line in lines:
            start = cursor
            end = cursor + per_line
            speaker = _speaker_name(episode, line.speaker_id)
            blocks.extend(
                [
                    str(subtitle_index),
                    f"{format_srt_time(start)} --> {format_srt_time(end)}",
                    f"{speaker}：{line.text}",
                    "",
                ]
            )
            subtitle_index += 1
            cursor = end

    output.write_text("\n".join(blocks), encoding="utf-8")
    return output


def write_timed_subtitles(
    episode: Episode,
    timings: list[dict[str, Any]],
    output_path: str | Path,
) -> Path:
    lines = [
        (shot.id, line)
        for shot in episode.shots
        for line in shot.dialogue
    ]
    if len(lines) != len(timings):
        raise ValueError("Voiceover timings must match episode dialogue count.")

    blocks: list[str] = []
    for subtitle_index, ((shot_id, line), timing) in enumerate(
        zip(lines, timings),
        start=1,
    ):
        if timing.get("shot_id") != shot_id or timing.get("speaker_id") != line.speaker_id:
            raise ValueError("Voiceover timing order does not match episode dialogue.")
        start = float(timing["start_seconds"])
        end = float(timing["end_seconds"])
        if start < 0 or end <= start:
            raise ValueError("Voiceover subtitle timing must have a positive duration.")
        speaker = _speaker_name(episode, line.speaker_id)
        blocks.extend(
            [
                str(subtitle_index),
                f"{format_srt_time(start)} --> {format_srt_time(end)}",
                f"{speaker}：{line.text}",
                "",
            ]
        )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(blocks), encoding="utf-8")
    return output


def write_preview_artifacts(episode: Episode, run_dir: str | Path) -> dict[str, str]:
    run_path = Path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)
    storyboard_path = write_storyboard_markdown(episode, run_path / "storyboard_preview.md")
    subtitles_path = write_subtitles(episode, run_path / "subtitles.srt")
    episode_snapshot_path = run_path / "episode.snapshot.json"
    episode_snapshot_path.write_text(
        json.dumps(episode_to_dict(episode), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "storyboard_preview": str(storyboard_path),
        "subtitles_srt": str(subtitles_path),
        "episode_snapshot": str(episode_snapshot_path),
    }
