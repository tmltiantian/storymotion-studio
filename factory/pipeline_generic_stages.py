from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .character_assets import write_character_asset_manifest
from .file_io import sha256_file, write_json_atomic
from .gateway_video_batch import render_gateway_video_batch
from .local_voiceover import render_voiceover_preview
from .media_validation import probe_media
from .novel_planner import plan_episode, read_novel
from .openmontage_adapter import write_openmontage_package
from .pipeline_context import StageContext, StageExecution
from .pipeline_contracts import StageState
from .pipeline_executors import register_executor
from .placeholder_renderer import render_placeholder_video
from .preview_writer import (
    write_storyboard_markdown,
    write_subtitles,
    write_timed_subtitles,
)
from .provider_profile import resolve_provider_profile
from .schema import Episode, episode_from_dict, episode_to_dict
from .shot_card_renderer import render_card_preview_video
from .video_handoff import write_video_handoff
from .video_provider import build_video_client, default_video_resolution


_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CONFIG = _REPO_ROOT / "config" / "factory.config.json"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def _factory_config(context: StageContext) -> dict[str, Any]:
    configured = str(context.spec.mode_options.get("factory_config") or "").strip()
    path = Path(configured).expanduser() if configured else _DEFAULT_CONFIG
    config = _read_json(path.resolve())
    config["workspace"] = str(_REPO_ROOT)
    return config


def _source_text(context: StageContext) -> tuple[str, str]:
    source_kind = str(context.spec.input.get("kind") or context.spec.mode.value)
    inline = str(context.spec.input.get("text") or "").strip()
    if inline:
        return inline, source_kind
    raw_path = str(context.spec.input.get("path") or "").strip()
    if not raw_path:
        raise ValueError("Project input requires text or path")
    source = Path(raw_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Project source is missing: {source}")
    return read_novel(source), source_kind


def _episode(context: StageContext) -> Episode:
    return episode_from_dict(
        _read_json(context.require_artifact("storyboard", "episode.json"))
    )


def _assets(context: StageContext) -> dict[str, Any]:
    return _read_json(context.require_artifact("assets", "character_assets.json"))


def _audio_manifest(context: StageContext) -> dict[str, Any]:
    return _read_json(context.require_artifact("audio", "audio_manifest.json"))


def _video_manifest(context: StageContext) -> dict[str, Any]:
    return _read_json(context.require_artifact("video", "video_manifest.json"))


def _copy_atomic(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)
    return destination


def _mux_audio(
    video: Path,
    audio: Path,
    output: Path,
    subtitles: Path | None = None,
) -> Path:
    temporary = output.with_name(f".{output.stem}.tmp{output.suffix}")
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video),
        "-i",
        str(audio),
    ]
    if subtitles is not None:
        command.extend(["-i", str(subtitles)])
    command.extend([
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
    ])
    if subtitles is not None:
        command.extend(["-map", "2:s:0", "-c:s", "mov_text"])
    command.extend([
        "-shortest",
        str(temporary),
    ])
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    os.replace(temporary, output)
    return output


def _concat_clips(clips: list[Path], output: Path) -> Path:
    if not clips:
        raise ValueError("Video stage did not produce any clips")
    manifest = output.with_suffix(".ffconcat")
    lines = ["ffconcat version 1.0"]
    for clip in clips:
        escaped = str(clip.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary = output.with_name(f".{output.stem}.tmp{output.suffix}")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest),
            "-c",
            "copy",
            str(temporary),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    os.replace(temporary, output)
    return output


@register_executor("generic.concept")
def execute_concept(context: StageContext) -> StageExecution:
    text, source_kind = _source_text(context)
    output = write_json_atomic(
        context.stage_dir / "concept.json",
        {
            "schema_version": "motion-comic-factory.concept.v1",
            "project_id": context.spec.project_id,
            "title": context.spec.title,
            "mode": context.spec.mode.value,
            "source_kind": source_kind,
            "premise": text,
            "target": dict(context.spec.target),
            "characters": list(context.spec.characters),
        },
    )
    return StageExecution.passed(executor=context.step.executor_id, artifacts=(output,))


def _execute_script(context: StageContext, *, adaptation_mode: str) -> StageExecution:
    concept = _read_json(context.require_artifact("concept", "concept.json"))
    text = str(concept["premise"])
    target_shots = int(context.spec.target.get("shots") or 8)
    episode = plan_episode(
        text,
        project_id=context.spec.project_id,
        title=context.spec.title,
        target_shots=target_shots,
        content_mode=adaptation_mode,
    )
    output = write_json_atomic(
        context.stage_dir / "script.json",
        {
            "schema_version": "motion-comic-factory.script.v1",
            "project_id": context.spec.project_id,
            "adaptation_mode": adaptation_mode,
            "source_kind": concept["source_kind"],
            "episode_draft": episode_to_dict(episode),
        },
    )
    return StageExecution.passed(executor=context.step.executor_id, artifacts=(output,))


@register_executor("original.script")
def execute_original_script(context: StageContext) -> StageExecution:
    return _execute_script(context, adaptation_mode="original")


@register_executor("novel.script")
def execute_novel_script(context: StageContext) -> StageExecution:
    return _execute_script(context, adaptation_mode="novel")


@register_executor("generic.storyboard")
def execute_storyboard(context: StageContext) -> StageExecution:
    script = _read_json(context.require_artifact("script", "script.json"))
    episode = episode_from_dict(dict(script["episode_draft"]))
    snapshot = write_json_atomic(context.stage_dir / "episode.json", episode_to_dict(episode))
    storyboard = write_storyboard_markdown(
        episode, context.stage_dir / "storyboard.md"
    )
    subtitles = write_subtitles(
        episode, context.stage_dir / "subtitle_draft.srt"
    )
    return StageExecution.passed(
        executor=context.step.executor_id,
        artifacts=(snapshot, storyboard, subtitles),
    )


@register_executor("generic.assets")
def execute_assets(context: StageContext) -> StageExecution:
    episode = _episode(context)
    configured = str(context.spec.mode_options.get("character_assets") or "").strip()
    source_manifest = Path(configured).expanduser().resolve() if configured else None
    character_manifest = write_character_asset_manifest(
        episode, source_manifest, context.stage_dir
    )
    character_assets = _read_json(character_manifest)
    review = write_json_atomic(
        context.stage_dir / "asset_review.json",
        {
            "schema_version": "motion-comic-factory.asset-review.v1",
            "project_id": context.spec.project_id,
            "approved": False,
            "production_ready": bool(character_assets.get("production_ready")),
            "review_items": [
                "角色身份与外观锚点是否稳定",
                "场景材质、光线和空间关系是否明确",
                "道具数量、位置和持有关系是否可连续追踪",
            ],
        },
    )
    return StageExecution.passed(
        executor=context.step.executor_id,
        artifacts=(character_manifest, review),
    )


@register_executor("generic.audio")
def execute_audio(context: StageContext) -> StageExecution:
    episode = _episode(context)
    work = context.stage_dir / "work"
    draft_subtitles = context.stage_dir / "subtitle_draft.srt"
    write_subtitles(episode, draft_subtitles)
    placeholder = render_placeholder_video(
        episode,
        draft_subtitles,
        context.stage_dir / "audio_placeholder.mp4",
        fps=int(context.spec.target.get("fps") or 30),
    )
    voiced_preview = context.stage_dir / "audio_sync_preview.mp4"
    env = None if context.enable_live else {"TTS_PROVIDER": "local"}
    rendered = render_voiceover_preview(
        episode,
        placeholder,
        voiced_preview,
        work,
        config=_factory_config(context),
        process_env=env,
    )
    subtitles = context.stage_dir / "subtitles.srt"
    timings = list(rendered.get("voiceover_timings") or [])
    if timings:
        write_timed_subtitles(episode, timings, subtitles)
    else:
        write_subtitles(episode, subtitles)
    provider_report = Path(str(rendered["voiceover_provider_report"]))
    audio = Path(str(rendered["voiceover_audio"]))
    script = Path(str(rendered["voiceover_script"]))
    manifest = write_json_atomic(
        context.stage_dir / "audio_manifest.json",
        {
            "schema_version": "motion-comic-factory.audio.v1",
            "project_id": context.spec.project_id,
            "voiceover_audio": str(audio.resolve()),
            "subtitles": str(subtitles.resolve()),
            "voiceover_script": str(script.resolve()),
            "provider_report": str(provider_report.resolve())
            if provider_report.is_file()
            else "",
            "timings": timings,
        },
    )
    artifacts = [manifest, audio, script, subtitles, draft_subtitles]
    if provider_report.is_file():
        artifacts.append(provider_report)
    placeholder.unlink(missing_ok=True)
    voiced_preview.unlink(missing_ok=True)
    return StageExecution.passed(
        executor=context.step.executor_id,
        artifacts=artifacts,
    )


@register_executor("generic.video")
def execute_video(context: StageContext) -> StageExecution:
    episode = _episode(context)
    audio = _audio_manifest(context)
    character_assets = _assets(context)
    output = context.stage_dir / "visual_preview.mp4"
    clips: list[Path] = []
    artifacts: list[Path] = []
    if context.enable_live:
        config = _factory_config(context)
        profile = resolve_provider_profile(config)
        if profile.video.provider not in {"gateway", "minimax"} or not profile.video.ready:
            blockers = profile.video.blockers or (
                "A ready cloud video provider is required.",
            )
            raise RuntimeError("; ".join(blockers))
        handoff = write_video_handoff(
            episode, config, context.stage_dir, character_assets
        )
        package = write_openmontage_package(
            episode,
            config,
            character_assets=character_assets,
            run_dir=context.stage_dir,
        )
        report_path = context.stage_dir / "gateway_video_batch.json"
        client = build_video_client(
            profile.video,
            model=str(
                context.spec.providers.get("video_model")
                or profile.video.model
            ),
        )
        report = render_gateway_video_batch(
            handoff,
            package,
            client,
            report_path,
            limit=int(context.spec.target.get("video_limit") or 0),
            resolution=str(
                context.spec.target.get("video_resolution")
                or default_video_resolution(profile.video.provider)
            ),
            generate_audio=False,
            allow_network=True,
        )
        if not report.get("success"):
            detail = report.get("errors") or report.get("blocked_reasons")
            raise RuntimeError(f"Cloud video generation failed: {detail}")
        package_payload = _read_json(package)
        clips = [
            Path(str(item["expected_assets"]["video_clip"]))
            for item in package_payload["timeline"]
        ]
        missing = [str(path) for path in clips if not path.is_file()]
        if missing:
            raise RuntimeError(f"Cloud video clips are missing: {missing}")
        artifacts.extend((handoff, package, report_path, *clips))
        generation_mode = f"{profile.video.provider}_video"
    else:
        render_card_preview_video(
            episode,
            Path(str(audio["subtitles"])),
            output,
            context.stage_dir / "cards",
            character_assets=character_assets,
            fps=int(context.spec.target.get("fps") or 30),
        )
        artifacts.append(output)
        generation_mode = "local_storyboard_preview"
    manifest = write_json_atomic(
        context.stage_dir / "video_manifest.json",
        {
            "schema_version": "motion-comic-factory.video.v1",
            "project_id": context.spec.project_id,
            "generation_mode": generation_mode,
            "primary_video": str(output.resolve()) if output.is_file() else "",
            "clips": [str(path.resolve()) for path in clips],
            "cloud_generation_requested": context.enable_live,
        },
    )
    return StageExecution.passed(
        executor=context.step.executor_id,
        artifacts=(manifest, *artifacts),
    )


@register_executor("generic.edit")
def execute_edit(context: StageContext) -> StageExecution:
    video = _video_manifest(context)
    audio = _audio_manifest(context)
    visual = Path(str(video["primary_video"]))
    if not visual.is_file():
        visual = _concat_clips(
            [Path(str(path)) for path in video.get("clips") or ()],
            context.stage_dir / "visual_assembly.mp4",
        )
    voiceover = Path(str(audio["voiceover_audio"]))
    output = context.stage_dir / "final_preview.mp4"
    _mux_audio(visual, voiceover, output, Path(str(audio["subtitles"])))
    manifest = write_json_atomic(
        context.stage_dir / "edit_manifest.json",
        {
            "schema_version": "motion-comic-factory.edit.v1",
            "project_id": context.spec.project_id,
            "final_preview": str(output.resolve()),
            "subtitles": str(audio["subtitles"]),
            "transition_policy": "cut_on_action_or_audio_motivation",
        },
    )
    return StageExecution.passed(
        executor=context.step.executor_id,
        artifacts=(manifest, output),
    )


@register_executor("generic.eval")
def execute_eval(context: StageContext) -> StageExecution:
    edit_manifest = _read_json(context.require_artifact("edit", "edit_manifest.json"))
    video = Path(str(edit_manifest["final_preview"]))
    probe = probe_media(video, required_stream="video")
    report = write_json_atomic(
        context.stage_dir / "eval_result.json",
        {
            "schema_version": "motion-comic-factory.eval.v1",
            "project_id": context.spec.project_id,
            "status": "REVIEW_REQUIRED" if probe.valid else "TECHNICAL_FAILURE",
            "technical": {
                "valid": probe.valid,
                "duration_seconds": probe.duration_seconds,
                "video_stream_count": probe.video_stream_count,
                "audio_stream_count": probe.audio_stream_count,
                "error": probe.error,
            },
            "review_dimensions": [
                "角色与场景一致性",
                "动作物理合理性与连续性",
                "口型、对白和字幕同步",
                "声音角色匹配、停顿和情绪自然度",
                "转场动机、节奏和观看舒适度",
            ],
        },
    )
    if not probe.valid:
        return StageExecution(
            state=StageState.FAILED,
            executor=context.step.executor_id,
            artifacts=(str(report),),
            error=f"Edit preview failed media validation: {probe.error}",
        )
    return StageExecution.passed(executor=context.step.executor_id, artifacts=(report,))


@register_executor("generic.deliver")
def execute_deliver(context: StageContext) -> StageExecution:
    edit_manifest = _read_json(context.require_artifact("edit", "edit_manifest.json"))
    source = Path(str(edit_manifest["final_preview"]))
    master = _copy_atomic(source, context.stage_dir / "master.mp4")
    digest = sha256_file(master)
    manifest = write_json_atomic(
        context.stage_dir / "delivery_manifest.json",
        {
            "schema_version": "motion-comic-factory.delivery.v1",
            "project_id": context.spec.project_id,
            "master": str(master.resolve()),
            "sha256": digest,
            "publication_status": "REVIEW_REQUIRED",
        },
    )
    return StageExecution.passed(
        executor=context.step.executor_id,
        artifacts=(manifest, master),
    )
