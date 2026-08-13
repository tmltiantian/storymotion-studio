from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

from .gateway_video import is_valid_mp4_file
from .hybrid_preview import render_hybrid_preview_video
from .local_voiceover import build_mux_voiced_preview_command
from .media_validation import probe_media, temporary_media_path
from .micro_preview import render_micro_preview_video
from .model_bakeoff import ModelBakeoffError, require_selected_production_model
from .openmontage_post import finalize_openmontage_preview
from .schema import Episode, episode_from_dict
from .shot_card_renderer import render_shot_cards


class PreviewRefreshError(RuntimeError):
    pass


def refresh_project_preview(
    config: dict[str, Any],
    project_id: str,
    *,
    card_renderer: Callable[..., list[Path]] = render_shot_cards,
    hybrid_renderer: Callable[..., dict[str, Any]] = render_hybrid_preview_video,
    micro_renderer: Callable[..., dict[str, Any]] = render_micro_preview_video,
    command_runner: Callable[..., Any] = subprocess.run,
    post_finalizer: Callable[..., Path] = finalize_openmontage_preview,
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, Any]:
    run_dir = Path(config["runsDir"]) / project_id
    output_dir = Path(config["outputDir"]) / project_id
    episode_path = run_dir / "episode.json"
    package_path = run_dir / "openmontage_package.json"
    subtitles_path = run_dir / "subtitles.srt"
    voiceover_path = run_dir / "voiceover" / "voiceover.m4a"
    character_assets_path = run_dir / "character_assets.json"
    quality_paths = (
        run_dir / "visual_timeline.json",
        run_dir / "visual_selection.json",
        run_dir / "model_bakeoff_report.json",
    )

    episode_payload = _read_json_object(episode_path, "episode")
    episode = episode_from_dict(episode_payload)
    _require_file(package_path, "OpenMontage package")
    _require_file(subtitles_path, "subtitle file")
    _require_file(voiceover_path, "existing voiceover")
    quality_count = sum(path.exists() or path.is_symlink() for path in quality_paths)
    if quality_count not in {0, len(quality_paths)}:
        raise PreviewRefreshError(
            "Project has an incomplete quality path; visual timeline, selection, "
            "and model bakeoff report must be present together."
        )

    if quality_count == len(quality_paths):
        for path in quality_paths:
            _require_file(path, path.name)
            if path.is_symlink():
                raise PreviewRefreshError(
                    f"Quality artifact must not be a symlink: {path}"
                )
        bakeoff_report = _read_json_object(quality_paths[2], "model bakeoff report")
        try:
            require_selected_production_model(bakeoff_report)
        except ModelBakeoffError as exc:
            raise PreviewRefreshError(f"Model bakeoff gate failed: {exc}") from exc
        return _refresh_quality_preview(
            episode=episode,
            project_id=project_id,
            run_dir=run_dir,
            output_dir=output_dir,
            package_path=package_path,
            subtitles_path=subtitles_path,
            voiceover_path=voiceover_path,
            timeline_path=quality_paths[0],
            selection_path=quality_paths[1],
            bakeoff_report_path=quality_paths[2],
            micro_renderer=micro_renderer,
            command_runner=command_runner,
            post_finalizer=post_finalizer,
            ffmpeg_bin=ffmpeg_bin,
        )

    character_assets = (
        _read_json_object(character_assets_path, "character assets")
        if character_assets_path.is_file()
        else None
    )
    cards = card_renderer(
        episode,
        run_dir / "cards",
        character_assets=character_assets,
    )
    hybrid_output = run_dir / "hybrid_preview.mp4"
    hybrid_report_path = run_dir / "hybrid_preview_report.json"
    hybrid_report = hybrid_renderer(
        episode,
        package_path=package_path,
        cards=cards,
        output_path=hybrid_output,
        report_path=hybrid_report_path,
        ffmpeg_bin=ffmpeg_bin,
    )

    voiced_output = run_dir / "hybrid_preview_voiced.mp4"
    run_voiceover_mux(
        build_mux_voiced_preview_command(
            source_video_path=hybrid_output,
            voiceover_audio_path=voiceover_path,
            output_path=voiced_output,
            ffmpeg_bin=ffmpeg_bin,
        ),
        command_runner=command_runner,
    )
    if not is_valid_mp4_file(voiced_output):
        raise PreviewRefreshError(
            f"Voiced hybrid preview is missing or is not a valid MP4: {voiced_output}"
        )

    final_output = output_dir / "final_preview.mp4"
    post_report_path = run_dir / "openmontage_post_report.json"
    written_post_report = post_finalizer(
        package_path=package_path,
        source_video_path=voiced_output,
        subtitles_path=subtitles_path,
        output_video_path=final_output,
        report_path=post_report_path,
    )
    if not is_valid_mp4_file(final_output):
        raise PreviewRefreshError(
            f"Final refreshed preview is missing or is not a valid MP4: {final_output}"
        )

    report_path = run_dir / "preview_refresh_report.json"
    report = {
        "schema_version": "motion-comic-factory.preview-refresh.v1",
        "success": True,
        "project_id": project_id,
        "voiceover_reused": True,
        "voiceover_audio": str(voiceover_path),
        "hybrid_preview_video": str(hybrid_output),
        "hybrid_preview_report": str(hybrid_report_path),
        "voiced_preview_video": str(voiced_output),
        "final_preview_video": str(final_output),
        "openmontage_post_report": str(written_post_report),
        "shot_count": int(hybrid_report.get("shot_count") or 0),
        "dynamic_shot_count": int(hybrid_report.get("dynamic_shot_count") or 0),
        "fallback_shot_count": int(hybrid_report.get("fallback_shot_count") or 0),
        "shots": hybrid_report.get("shots") or [],
    }
    _write_json(report_path, report)

    status_path = run_dir / "status.json"
    status = _read_json_object(status_path, "status") if status_path.is_file() else {}
    status.update(
        {
            "hybrid_preview_video": str(hybrid_output),
            "hybrid_preview_report": str(hybrid_report_path),
            "hybrid_voiced_preview_video": str(voiced_output),
            "dynamic_shot_count": report["dynamic_shot_count"],
            "fallback_shot_count": report["fallback_shot_count"],
            "final_preview_video": str(final_output),
            "openmontage_post_report": str(written_post_report),
            "preview_refresh_report": str(report_path),
        }
    )
    _write_json(status_path, status)
    return report


def _refresh_quality_preview(
    *,
    episode: Episode,
    project_id: str,
    run_dir: Path,
    output_dir: Path,
    package_path: Path,
    subtitles_path: Path,
    voiceover_path: Path,
    timeline_path: Path,
    selection_path: Path,
    bakeoff_report_path: Path,
    micro_renderer: Callable[..., dict[str, Any]],
    command_runner: Callable[..., Any],
    post_finalizer: Callable[..., Path],
    ffmpeg_bin: str,
) -> dict[str, Any]:
    micro_output = run_dir / "micro_preview.mp4"
    micro_report_path = run_dir / "micro_preview_report.json"
    voiced_output = run_dir / "micro_preview_voiced.mp4"
    final_output = output_dir / "final_preview.mp4"
    post_report_path = run_dir / "openmontage_post_report.json"
    report_path = run_dir / "preview_refresh_report.json"
    status_path = run_dir / "status.json"
    protected = (
        micro_output,
        micro_report_path,
        voiced_output,
        final_output,
        post_report_path,
        report_path,
        status_path,
    )
    with _RefreshArtifactTransaction(protected, backup_parent=run_dir.parent):
        micro_report = micro_renderer(
            episode,
            timeline_path=timeline_path,
            selection_path=selection_path,
            bakeoff_report_path=bakeoff_report_path,
            run_dir=run_dir,
            output_path=micro_output,
            report_path=micro_report_path,
            ffmpeg_bin=ffmpeg_bin,
        )
        if not is_valid_mp4_file(micro_output):
            raise PreviewRefreshError(
                f"Micro preview is missing or is not a valid MP4: {micro_output}"
            )
        run_voiceover_mux(
            build_mux_voiced_preview_command(
                source_video_path=micro_output,
                voiceover_audio_path=voiceover_path,
                output_path=voiced_output,
                ffmpeg_bin=ffmpeg_bin,
            ),
            command_runner=command_runner,
        )
        if not is_valid_mp4_file(voiced_output):
            raise PreviewRefreshError(
                f"Voiced micro preview is missing or is not a valid MP4: {voiced_output}"
            )
        written_post_report = post_finalizer(
            package_path=package_path,
            source_video_path=voiced_output,
            subtitles_path=subtitles_path,
            output_video_path=final_output,
            report_path=post_report_path,
        )
        if not is_valid_mp4_file(final_output):
            raise PreviewRefreshError(
                f"Final refreshed preview is missing or is not a valid MP4: {final_output}"
            )

        sources = micro_report.get("sources") or []
        video_count = sum(
            isinstance(source, dict) and source.get("kind") == "video"
            for source in sources
        )
        still_count = sum(
            isinstance(source, dict) and source.get("kind") == "still"
            for source in sources
        )
        report = {
            "schema_version": "motion-comic-factory.preview-refresh.v1",
            "success": True,
            "project_id": project_id,
            "render_path": "quality_micro",
            "voiceover_reused": True,
            "voiceover_audio": str(voiceover_path),
            "micro_preview_video": str(micro_output),
            "micro_preview_report": str(micro_report_path),
            "voiced_preview_video": str(voiced_output),
            "final_preview_video": str(final_output),
            "openmontage_post_report": str(written_post_report),
            "micro_shot_count": int(micro_report.get("micro_shot_count") or 0),
            "duration_seconds": float(micro_report.get("duration_seconds") or 0.0),
            "video_source_count": video_count,
            "still_source_count": still_count,
            "dynamic_shot_count": video_count,
            "fallback_shot_count": 0,
            "sources": sources,
        }
        _write_json(report_path, report)
        status = (
            _read_json_object(status_path, "status") if status_path.is_file() else {}
        )
        for stale_key in (
            "hybrid_preview_video",
            "hybrid_preview_report",
            "hybrid_voiced_preview_video",
        ):
            status.pop(stale_key, None)
        status.update(
            {
                "micro_preview_video": str(micro_output),
                "micro_preview_report": str(micro_report_path),
                "micro_voiced_preview_video": str(voiced_output),
                "micro_shot_count": report["micro_shot_count"],
                "dynamic_shot_count": video_count,
                "fallback_shot_count": 0,
                "final_preview_video": str(final_output),
                "openmontage_post_report": str(written_post_report),
                "preview_refresh_report": str(report_path),
            }
        )
        _write_json(status_path, status)
        return report


def run_voiceover_mux(
    command: list[str],
    *,
    command_runner: Callable[..., Any] = subprocess.run,
    media_validator: Callable[[Path], bool] | None = None,
) -> None:
    if not command:
        raise PreviewRefreshError("Voiceover mux command is empty.")
    output = Path(command[-1])
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = temporary_media_path(output)
    temporary_command = [*command[:-1], str(temporary_output)]
    try:
        command_runner(
            temporary_command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        temporary_output.unlink(missing_ok=True)
        detail = str(exc.stderr or exc.stdout or exc).strip()[-1200:]
        raise PreviewRefreshError(f"Voiceover mux failed: {detail}") from exc
    except OSError as exc:
        temporary_output.unlink(missing_ok=True)
        raise PreviewRefreshError(f"Unable to run voiceover mux: {exc}") from exc
    validator = media_validator or (
        lambda path: probe_media(path, required_stream="video").valid
    )
    if not validator(temporary_output):
        temporary_output.unlink(missing_ok=True)
        raise PreviewRefreshError("Voiceover mux did not produce a valid video stream.")
    temporary_output.replace(output)


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise PreviewRefreshError(f"{label.capitalize()} not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise PreviewRefreshError(f"Unable to read {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise PreviewRefreshError(f"{label.capitalize()} must contain a JSON object.")
    return payload


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise PreviewRefreshError(f"{label.capitalize()} not found: {path}")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class _RefreshArtifactTransaction:
    def __init__(self, paths: tuple[Path, ...], *, backup_parent: Path):
        self._paths = paths
        self._backup_parent = backup_parent
        self._backup_root: Path | None = None
        self._snapshots: list[tuple[Path, Path, bool]] = []

    def __enter__(self) -> _RefreshArtifactTransaction:
        self._backup_parent.mkdir(parents=True, exist_ok=True)
        self._backup_root = Path(
            tempfile.mkdtemp(
                prefix=".preview-refresh-rollback-", dir=self._backup_parent
            )
        )
        try:
            for index, source in enumerate(self._paths):
                backup = self._backup_root / str(index)
                existed = source.exists() or source.is_symlink()
                self._snapshots.append((source, backup, existed))
                if not existed:
                    continue
                if source.is_symlink() or not source.is_file():
                    raise PreviewRefreshError(
                        f"Refusing to preserve non-file preview artifact: {source}"
                    )
                shutil.copy2(source, backup)
        except BaseException:
            self._cleanup()
            raise
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        try:
            if exc_type is not None:
                for destination, backup, existed in self._snapshots:
                    if destination.is_symlink() or destination.is_file():
                        destination.unlink(missing_ok=True)
                    if existed:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(backup, destination)
        finally:
            self._cleanup()
        return False

    def _cleanup(self) -> None:
        if self._backup_root is not None:
            shutil.rmtree(self._backup_root, ignore_errors=True)
            self._backup_root = None
