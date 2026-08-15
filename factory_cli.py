from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shlex
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit

from factory.character_assets import write_character_asset_source_template
from factory.character_brief import (
    write_confirmed_character_assets_manifest,
    write_character_assets_manifest_from_brief,
    write_character_assets_status_from_brief,
    write_character_generation_brief,
    write_reviewed_role_images_intake_from_directory,
    write_reviewed_role_images_manifest_from_directory,
    write_reviewed_role_images_template_from_brief,
    install_character_references_from_manifest,
)
from factory.doubao_tts import DoubaoTTSClient, resolve_doubao_tts_config
from factory.gateway_image import GatewayImageClient, GatewayImageConfig, GatewayImageError
from factory.gateway_text import GatewayTextClient, GatewayTextConfig, GatewayTextError
from factory.gateway_video import (
    GatewayVideoClient,
    GatewayVideoConfig,
    GatewayVideoError,
    GatewayVideoProbe,
)
from factory.gateway_video_batch import (
    GatewayVideoBatchError,
    render_gateway_video_batch,
    render_gateway_video_single,
)
from factory.novel_planner import plan_episode, read_novel
from factory.micro_video_batch import candidate_output_path
from factory.model_bakeoff import ModelBakeoffError, finalize_bakeoff
from factory.openmontage_adapter import load_config
from factory.preview_refresh import PreviewRefreshError, refresh_project_preview
from factory.provider_profile import resolve_provider_profile
from factory.video_provider import build_video_client, default_video_resolution
from factory.quality_bakeoff_runner import (
    QualityBakeoffRunnerError,
    run_quality_bakeoff_candidates,
)
from factory.quality_production_runner import (
    QualityProductionRunnerError,
    run_quality_production_candidates,
    write_quality_visual_selection,
)
from factory.visual_qc import (
    VisualQCError,
    analyze_visual_candidate,
    record_visual_review,
)
from factory.visual_timeline import visual_timeline_from_dict
from factory.pet_sitcom import (
    IMAGE_MODEL as PET_IMAGE_MODEL,
    VIDEO_MODEL as PET_VIDEO_MODEL,
    build_pet_sitcom_plan,
    write_pet_sitcom_plan,
)
from factory.pet_sitcom_generation import (
    PET_RETRY_SUFFIXES,
    PetSitcomGenerationError,
    _require_approved_anchors,
    approve_pet_anchors,
    generate_pet_sitcom_anchors,
    generate_pet_sitcom_shots,
    sanitize_pet_sitcom_report,
    select_pet_shot_candidate,
)
from factory.pet_sitcom_audio_first import (
    PetSitcomAudioFirstError,
    generate_pet_speech_assets,
    load_pet_speech_assets,
)
from factory.pet_sitcom_audio_probe import (
    require_approved_pet_audio_probe,
    run_pet_audio_drive_probe,
)
from factory.pet_sitcom_compose import (
    PetSitcomComposeError,
    compose_pet_sitcom,
)
from factory.pet_sitcom_sound import (
    PetSoundError,
    load_pet_sound_design,
    prepare_pet_sound_design,
)
from factory import pet_sitcom_audio_probe as pet_sitcom_audio_probe_module
from factory import pet_sitcom_audio_first as pet_sitcom_audio_first_module
from factory import pet_sitcom_generation as pet_sitcom_generation_module
from factory import pet_sitcom_review as pet_sitcom_review_module
from factory import pet_sitcom_sound as pet_sitcom_sound_module
from factory.pet_sitcom_review import (
    PetSitcomReviewError,
    build_final_evidence,
    build_pet_shot_evidence,
    build_source_evidence,
    validate_final_evidence,
    validate_owner_native_audio_review,
    validate_pet_shot_review,
    validate_pet_shot_reviews,
    validate_source_evidence,
    write_pet_sitcom_review_markdown,
)
from factory.pet_replica_cli import add_pet_replica_parser
from factory.pipeline_cli import add_factory_parser


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def refresh_preview_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    report_path = Path(config["runsDir"]) / args.project / "preview_refresh_report.json"
    try:
        report = refresh_project_preview(config, args.project)
    except (PreviewRefreshError, RuntimeError, ValueError, OSError) as exc:
        report = {
            "schema_version": "motion-comic-factory.preview-refresh.v1",
            "success": False,
            "project_id": args.project,
            "error": str(exc),
        }
        write_json(report_path, report)
    write_json(report_path, report)
    print(
        json.dumps(
            {
                "preview_refresh_report": str(report_path),
                "success": report["success"],
                "dynamic_shot_count": report.get("dynamic_shot_count", 0),
                "fallback_shot_count": report.get("fallback_shot_count", 0),
                "final_preview_video": report.get("final_preview_video", ""),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["success"] else 1


def character_assets_template_command(args: argparse.Namespace) -> int:
    text = read_novel(args.input)
    episode = plan_episode(
        text,
        project_id=args.project,
        title=args.title,
        target_shots=args.shots,
    )
    if args.output:
        output_path = Path(args.output)
    else:
        config = load_config(args.config)
        output_path = Path(config["runsDir"]) / args.project / "character_assets.template.json"
    written = write_character_asset_source_template(episode, output_path)
    print(
        json.dumps(
            {
                "character_assets_template": str(written),
                "project_id": episode.project_id,
                "character_count": len(episode.characters),
                "characters": [character.name for character in episode.characters],
            },
            ensure_ascii=False,
        )
    )
    return 0


def character_brief_command(args: argparse.Namespace) -> int:
    text = read_novel(args.input)
    episode = plan_episode(
        text,
        project_id=args.project,
        title=args.title,
        target_shots=args.shots,
    )
    if args.output:
        output_path = Path(args.output)
    else:
        config = load_config(args.config)
        output_path = Path(config["runsDir"]) / args.project / "character_generation_brief.json"
    written = write_character_generation_brief(episode, output_path)
    print(
        json.dumps(
            {
                "character_generation_brief": str(written),
                "project_id": episode.project_id,
                "character_count": len(episode.characters),
                "characters": [character.name for character in episode.characters],
            },
            ensure_ascii=False,
        )
    )
    return 0


def character_assets_from_brief_command(args: argparse.Namespace) -> int:
    brief_path = Path(args.brief)
    output_path = Path(args.output) if args.output else brief_path.with_name("character_assets.from_brief.json")
    try:
        written = write_character_assets_manifest_from_brief(
            brief_path,
            output_path,
            require_files=args.require_files,
        )
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "character_assets": str(output_path),
                    "success": False,
                    "errors": [str(exc)],
                    "require_files": args.require_files,
                },
                ensure_ascii=False,
            )
        )
        return 1
    data = json.loads(written.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "character_assets": str(written),
                "success": True,
                "project_id": data["project_id"],
                "character_count": len(data["characters"]),
                "require_files": args.require_files,
            },
            ensure_ascii=False,
        )
    )
    return 0


def character_assets_reviewed_template_command(args: argparse.Namespace) -> int:
    brief_path = Path(args.brief)
    output_path = Path(args.output) if args.output else brief_path.with_name("reviewed_role_images.template.json")
    try:
        written = write_reviewed_role_images_template_from_brief(brief_path, output_path)
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "reviewed_role_images_template": str(output_path),
                    "success": False,
                    "errors": [str(exc)],
                },
                ensure_ascii=False,
            )
        )
        return 1

    data = json.loads(written.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "reviewed_role_images_template": str(written),
                "success": True,
                "project_id": data.get("project_id", ""),
                "character_count": len(data["characters"]),
                "filled_manifest_path": data.get("filled_manifest_path", ""),
            },
            ensure_ascii=False,
        )
    )
    return 0


def character_assets_reviewed_from_dir_command(args: argparse.Namespace) -> int:
    brief_path = Path(args.brief)
    image_dir = Path(args.image_dir) if args.image_dir else brief_path.with_name("reviewed_role_images")
    output_path = Path(args.output) if args.output else brief_path.with_name("reviewed_role_images.json")
    try:
        written = write_reviewed_role_images_manifest_from_directory(brief_path, image_dir, output_path)
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "reviewed_role_images": str(output_path),
                    "success": False,
                    "errors": [str(exc)],
                    "image_dir": str(image_dir),
                },
                ensure_ascii=False,
            )
        )
        return 1

    data = json.loads(written.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "reviewed_role_images": str(written),
                "success": True,
                "project_id": data.get("project_id", ""),
                "character_count": len(data["characters"]),
                "image_dir": str(image_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


def character_assets_reviewed_intake_command(args: argparse.Namespace) -> int:
    brief_path = Path(args.brief)
    image_dir = Path(args.image_dir) if args.image_dir else brief_path.with_name("reviewed_role_images")
    output_path = Path(args.output) if args.output else brief_path.with_name("reviewed_role_images_intake.json")
    manifest_output_path = Path(args.manifest_output) if args.manifest_output else brief_path.with_name("reviewed_role_images.json")
    confirmed_output_path = (
        Path(args.confirmed_output) if args.confirmed_output else brief_path.with_name("character_assets.confirmed.json")
    )
    written = write_reviewed_role_images_intake_from_directory(
        brief_path,
        image_dir,
        output_path,
        manifest_output_path=manifest_output_path,
        confirmed_output_path=confirmed_output_path,
    )
    data = json.loads(written.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "reviewed_role_images_intake": str(written),
                "ready": data["ready"],
                "installed": data["installed"],
                "summary": data["summary"],
                "next_actions": [item["id"] for item in data["next_actions"]],
                "image_dir": str(image_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


def character_assets_confirm_source_command(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    output_path = Path(args.output) if args.output else manifest_path.with_name("character_assets.confirmed.json")
    require_files = not args.skip_file_check
    try:
        written = write_confirmed_character_assets_manifest(
            manifest_path,
            output_path,
            asset_source=args.asset_source,
            require_files=require_files,
        )
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "character_assets": str(output_path),
                    "success": False,
                    "errors": [str(exc)],
                    "asset_source": args.asset_source,
                    "require_files": require_files,
                },
                ensure_ascii=False,
            )
        )
        return 1

    data = json.loads(written.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "character_assets": str(written),
                "success": True,
                "project_id": data.get("project_id", ""),
                "asset_source": args.asset_source,
                "character_count": len(data["characters"]),
                "require_files": require_files,
            },
            ensure_ascii=False,
        )
    )
    return 0


def character_assets_install_references_command(args: argparse.Namespace) -> int:
    brief_path = Path(args.brief)
    output_path = Path(args.output) if args.output else brief_path.with_name("character_assets.confirmed.json")
    try:
        written = install_character_references_from_manifest(
            brief_path,
            args.source_manifest,
            output_path,
            asset_source=args.asset_source,
            overwrite=args.overwrite,
        )
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "character_assets": str(output_path),
                    "success": False,
                    "errors": [str(exc)],
                    "asset_source": args.asset_source,
                    "overwrite": args.overwrite,
                },
                ensure_ascii=False,
            )
        )
        return 1

    data = json.loads(written.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "character_assets": str(written),
                "success": True,
                "project_id": data.get("project_id", ""),
                "asset_source": args.asset_source,
                "installed_count": len(data["characters"]),
                "overwrite": args.overwrite,
            },
            ensure_ascii=False,
        )
    )
    return 0


def character_assets_status_command(args: argparse.Namespace) -> int:
    brief_path = Path(args.brief)
    output_path = Path(args.output) if args.output else brief_path.with_name("character_assets_status.json")
    asset_root = Path(args.asset_root) if args.asset_root else brief_path.parent
    written = write_character_assets_status_from_brief(brief_path, output_path, asset_root=asset_root)
    data = json.loads(written.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "character_assets_status": str(written),
                "project_id": data["project_id"],
                "asset_ready": data["asset_ready"],
                "summary": data["summary"],
                "next_actions": [item["id"] for item in data["next_actions"]],
            },
            ensure_ascii=False,
        )
    )
    return 0


def provider_report_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    profile = resolve_provider_profile(config)
    output_path = Path(args.output) if args.output else Path(config["runsDir"]) / "provider_profile.json"
    report = profile.to_report()
    write_json(output_path, report)
    print(
        json.dumps(
            {
                "provider_report": str(output_path),
                "providers": {
                    name: item["provider"]
                    for name, item in report["capabilities"].items()
                },
                "ready": {
                    name: item["ready"]
                    for name, item in report["capabilities"].items()
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


def gateway_text_smoke_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    profile = resolve_provider_profile(config)
    output_path = Path(args.output) if args.output else Path(config["runsDir"]) / "gateway_text_smoke.json"
    report = {
        "schema_version": "motion-comic-factory.gateway-text-smoke.v1",
        "provider": profile.text.provider,
        "model": profile.text.model,
        "executed": False,
        "success": False,
        "blocked_reasons": [],
        "error": "",
    }
    if not args.enable_live:
        report["blocked_reasons"] = ["Live gateway text smoke is disabled."]
    elif profile.text.provider != "gateway":
        report["blocked_reasons"] = ["Text provider is not configured as gateway."]
    elif not profile.text.ready:
        report["blocked_reasons"] = list(profile.text.blockers)
    else:
        try:
            result = GatewayTextClient(
                GatewayTextConfig(
                    api_key=profile.text.api_key,
                    base_url=profile.text.base_url,
                    model=profile.text.model,
                    timeout_seconds=args.timeout,
                )
            ).chat(
                [{"role": "user", "content": args.prompt}],
                response_format={"type": "json_object"},
                allow_network=True,
            )
            report.update(
                {
                    "executed": True,
                    "success": True,
                    "result": result.to_report(),
                    "content": result.content,
                }
            )
        except GatewayTextError as exc:
            report.update({"executed": True, "error": str(exc)})
    write_json(output_path, report)
    print(
        json.dumps(
            {
                "gateway_text_smoke": str(output_path),
                "executed": report["executed"],
                "success": report["success"],
                "blocked_reasons": report["blocked_reasons"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["success"] or not args.enable_live else 1


def gateway_image_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    profile = resolve_provider_profile(config)
    output_image = Path(args.output)
    report_path = (
        Path(args.report_output)
        if args.report_output
        else Path(config["runsDir"]) / "gateway_image_report.json"
    )
    report = {
        "schema_version": "motion-comic-factory.gateway-image.v1",
        "provider": profile.image.provider,
        "model": profile.image.model,
        "output_path": str(output_image),
        "executed": False,
        "success": False,
        "blocked_reasons": [],
        "error": "",
    }
    if not args.enable_live:
        report["blocked_reasons"] = ["Live gateway image generation is disabled."]
    elif profile.image.provider != "gateway":
        report["blocked_reasons"] = ["Image provider is not configured as gateway."]
    elif not profile.image.ready:
        report["blocked_reasons"] = list(profile.image.blockers)
    else:
        try:
            result = GatewayImageClient(
                GatewayImageConfig(
                    api_key=profile.image.api_key,
                    base_url=profile.image.base_url,
                    model=profile.image.model,
                    timeout_seconds=args.timeout,
                )
            ).generate(args.prompt, output_image, size=args.size)
            report.update(
                {
                    "executed": True,
                    "success": True,
                    "result": result.to_report(),
                }
            )
        except (GatewayImageError, ValueError) as exc:
            report.update({"executed": True, "error": str(exc)})
    write_json(report_path, report)
    print(
        json.dumps(
            {
                "gateway_image_report": str(report_path),
                "output_path": str(output_image),
                "executed": report["executed"],
                "success": report["success"],
                "blocked_reasons": report["blocked_reasons"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["success"] or not args.enable_live else 1


def gateway_video_probe_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    profile = resolve_provider_profile(config)
    model = args.model.strip() or profile.video.model
    output_path = (
        Path(args.output)
        if args.output
        else Path(config["runsDir"]) / args.project / "gateway_video_probe.json"
    )
    if args.enable_live and profile.video.provider != "gateway":
        report = {
            "schema_version": "motion-comic-factory.gateway-video-probe.v2",
            "model": model,
            "executed": False,
            "success": False,
            "production_ready": False,
            "validation_scope": "submission_only",
            "billable_submission": False,
            "blocked_reasons": ["Video provider is not configured as gateway."],
            "error": "",
        }
    elif args.enable_live and not profile.video.ready:
        report = {
            "schema_version": "motion-comic-factory.gateway-video-probe.v2",
            "model": model,
            "executed": False,
            "success": False,
            "production_ready": False,
            "validation_scope": "submission_only",
            "billable_submission": False,
            "blocked_reasons": list(profile.video.blockers),
            "error": "",
        }
    else:
        report = GatewayVideoProbe(
            GatewayVideoConfig(
                api_key=profile.video.api_key,
                base_url=profile.video.base_url,
                model=model,
                timeout_seconds=args.timeout,
                submit_timeout_seconds=args.submit_timeout,
            )
        ).run(
            args.prompt,
            images=args.image,
            duration=args.duration,
            ratio=args.ratio,
            resolution=args.resolution,
            generate_audio=args.generate_audio,
            allow_network=args.enable_live,
        )
    write_json(output_path, report)
    print(
        json.dumps(
            {
                "gateway_video_probe": str(output_path),
                "executed": report["executed"],
                "success": report["success"],
                "blocked_reasons": report["blocked_reasons"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["success"] or not args.enable_live else 1


def _gateway_video_client(profile, args: argparse.Namespace):
    model = args.model.strip() or profile.video.model
    return build_video_client(
        profile.video,
        model=model,
        timeout_seconds=args.timeout,
        submit_timeout_seconds=args.submit_timeout,
        download_timeout_seconds=args.download_timeout,
        poll_interval_seconds=args.poll_interval,
        max_wait_seconds=args.max_wait,
    )


def gateway_video_generate_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    profile = resolve_provider_profile(config)
    model = args.model.strip() or profile.video.model
    provider_supported = profile.video.provider in {"gateway", "minimax"}
    resolution = args.resolution.strip() or default_video_resolution(
        profile.video.provider
    )
    output_path = Path(args.output)
    report_path = (
        Path(args.report_output)
        if args.report_output
        else Path(config["runsDir"]) / "gateway_video_report.json"
    )
    fallback_report = {
        "schema_version": "motion-comic-factory.gateway-video.v2",
        "provider": profile.video.provider,
        "model": model,
        "output_path": str(output_path),
        "reference_image_count": len(args.image),
        "reference_audio_provided": bool(args.audio),
        "plan_ready": False,
        "planned_count": 0,
        "executed": False,
        "success": False,
        "completed_count": 0,
        "skipped_count": 0,
        "resumed_count": 0,
        "failed_count": 0,
        "overwrite": args.overwrite,
        "blocked_reasons": [],
        "jobs": [],
        "results": [],
        "errors": [],
        "error": "",
    }
    if not provider_supported:
        report = fallback_report
        report["blocked_reasons"] = ["A supported cloud video provider is not configured."]
        write_json(report_path, report)
    elif args.enable_live and not profile.video.ready:
        report = fallback_report
        report["blocked_reasons"] = list(profile.video.blockers)
        write_json(report_path, report)
    else:
        video_client = _gateway_video_client(profile, args)
        try:
            report = render_gateway_video_single(
                args.prompt,
                output_path,
                video_client,
                report_path,
                images=args.image,
                audio=args.audio or None,
                duration=args.duration,
                ratio=args.ratio,
                resolution=resolution,
                generate_audio=args.generate_audio,
                allow_network=args.enable_live,
                overwrite=args.overwrite,
            )
            report["provider"] = profile.video.provider
            report["reference_audio_provided"] = bool(args.audio)
            write_json(report_path, report)
        except (GatewayVideoBatchError, ValueError) as exc:
            report = fallback_report
            error = str(exc)
            if profile.video.api_key:
                error = error.replace(profile.video.api_key, "[redacted]")
            report["errors"] = [{"error": error}]
            report["error"] = error
            write_json(report_path, report)
    print(
        json.dumps(
            {
                "gateway_video_report": str(report_path),
                "output_path": str(output_path),
                "executed": report["executed"],
                "success": report["success"],
                "blocked_reasons": report["blocked_reasons"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["success"] or (
        not args.enable_live and report["plan_ready"]
    ) else 1


def gateway_video_batch_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    profile = resolve_provider_profile(config)
    model = args.model.strip() or profile.video.model
    resolution = args.resolution.strip() or default_video_resolution(
        profile.video.provider
    )
    run_dir = Path(config["runsDir"]) / args.project
    handoff_path = Path(args.handoff) if args.handoff else run_dir / "video_handoff.json"
    package_path = (
        Path(args.package) if args.package else run_dir / "openmontage_package.json"
    )
    report_path = (
        Path(args.output) if args.output else run_dir / "gateway_video_batch.json"
    )
    if profile.video.provider not in {"gateway", "minimax"}:
        report = {
            "schema_version": "motion-comic-factory.gateway-video-batch.v1",
            "provider": profile.video.provider,
            "model": model,
            "plan_ready": False,
            "planned_count": 0,
            "executed": False,
            "success": False,
            "completed_count": 0,
            "skipped_count": 0,
            "resumed_count": 0,
            "failed_count": 0,
            "overwrite": args.overwrite,
            "blocked_reasons": [
                "A supported cloud video provider is not configured."
            ],
            "jobs": [],
            "results": [],
            "errors": [],
        }
        write_json(report_path, report)
        print(
            json.dumps(
                {
                    "gateway_video_batch": str(report_path),
                    "plan_ready": False,
                    "planned_count": 0,
                    "executed": False,
                    "success": False,
                    "skipped_count": 0,
                    "blocked_reasons": report["blocked_reasons"],
                },
                ensure_ascii=False,
            )
        )
        return 1
    video_client = _gateway_video_client(profile, args)
    can_execute = (
        args.enable_live
        and profile.video.provider in {"gateway", "minimax"}
        and profile.video.ready
    )
    try:
        report = render_gateway_video_batch(
            handoff_path,
            package_path,
            video_client,
            report_path,
            limit=args.limit,
            resolution=resolution,
            generate_audio=args.generate_audio,
            overwrite=args.overwrite,
            allow_network=can_execute,
        )
        if args.enable_live and not profile.video.ready:
            report["blocked_reasons"] = list(profile.video.blockers)
            write_json(report_path, report)
    except GatewayVideoBatchError as exc:
        error = str(exc)
        if profile.video.api_key:
            error = error.replace(profile.video.api_key, "[redacted]")
        report = {
            "schema_version": "motion-comic-factory.gateway-video-batch.v1",
            "provider": profile.video.provider,
            "model": video_client.config.model,
            "plan_ready": False,
            "planned_count": 0,
            "executed": False,
            "success": False,
            "completed_count": 0,
            "skipped_count": 0,
            "resumed_count": 0,
            "failed_count": 0,
            "overwrite": args.overwrite,
            "blocked_reasons": [],
            "jobs": [],
            "results": [],
            "errors": [{"error": error}],
        }
        write_json(report_path, report)
    write_json(report_path, report)
    print(
        json.dumps(
            {
                "gateway_video_batch": str(report_path),
                "plan_ready": report["plan_ready"],
                "planned_count": report["planned_count"],
                "executed": report["executed"],
                "success": report["success"],
                "skipped_count": report["skipped_count"],
                "blocked_reasons": report["blocked_reasons"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["success"] or (
        not args.enable_live and report["plan_ready"]
    ) else 1


def quality_bakeoff_candidates_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    try:
        report = run_quality_bakeoff_candidates(
            config,
            args.project,
            candidate_number=args.candidate,
            kind=args.kind,
            micro_shot_ids=getattr(args, "micro_shot", None) or None,
            allow_network=args.enable_live,
            overwrite=args.overwrite,
            timeout_seconds=args.timeout,
            submit_timeout_seconds=args.submit_timeout,
            download_timeout_seconds=args.download_timeout,
            poll_interval_seconds=args.poll_interval,
            max_wait_seconds=args.max_wait,
        )
        error = ""
    except (QualityBakeoffRunnerError, KeyError, TypeError, ValueError) as exc:
        report = {
            "plan_ready": False,
            "executed": False,
            "success": False,
            "planned_count": 0,
            "completed_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "blocked_reasons": [],
        }
        error = str(exc)
    print(
        json.dumps(
            {
                "quality_bakeoff_candidates": str(
                    Path(config["runsDir"])
                    / args.project
                    / "model_bakeoff_candidates.json"
                ),
                "plan_ready": report["plan_ready"],
                "executed": report["executed"],
                "success": report["success"],
                "planned_count": report["planned_count"],
                "completed_count": report["completed_count"],
                "skipped_count": report["skipped_count"],
                "failed_count": report["failed_count"],
                "blocked_reasons": report["blocked_reasons"],
                "error": error,
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["success"] or (
        not args.enable_live and report["plan_ready"]
    ) else 1


def quality_visual_qc_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    run_dir = Path(config["runsDir"]) / args.project
    report_path = (
        run_dir
        / "micro_qc"
        / args.micro_shot
        / args.model
        / f"candidate_{args.candidate:03d}"
        / "visual_qc.json"
    )
    try:
        timeline = visual_timeline_from_dict(
            json.loads(
                (run_dir / "visual_timeline.json").read_text(encoding="utf-8")
            )
        )
        shot = next(
            (item for item in timeline.micro_shots if item.id == args.micro_shot),
            None,
        )
        if shot is None:
            raise VisualQCError(f"Unknown micro-shot ID: {args.micro_shot}.")
        candidate = candidate_output_path(
            run_dir,
            args.micro_shot,
            args.model,
            args.candidate,
        )
        output_dir = report_path.parent
        if report_path.is_file() and not args.refresh:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        else:
            report = analyze_visual_candidate(
                candidate,
                shot,
                output_dir=output_dir,
                reference_image_labels=shot.character_ids,
            )
        if args.review:
            review = json.loads(Path(args.review).read_text(encoding="utf-8"))
            report = record_visual_review(
                report_path,
                review,
                expected_micro_shot=shot,
                expected_reference_image_labels=shot.character_ids,
            )
        contact = report.get("contact_sheet")
        evidence = contact.get("evidence") if isinstance(contact, dict) else {}
        contact_path = (
            str(evidence.get("path") or "") if isinstance(evidence, dict) else ""
        )
        error = ""
    except (
        VisualQCError,
        OSError,
        json.JSONDecodeError,
        StopIteration,
        ValueError,
    ) as exc:
        report = {
            "automatic_passed": False,
            "automatic_hard_failures": [],
            "passed": False,
        }
        contact_path = ""
        error = str(exc)
    print(
        json.dumps(
            {
                "visual_qc_report": str(report_path),
                "automatic_passed": bool(report.get("automatic_passed")),
                "automatic_hard_failures": report.get(
                    "automatic_hard_failures", []
                ),
                "passed": bool(report.get("passed")),
                "contact_sheet": contact_path,
                "error": error,
            },
            ensure_ascii=False,
        )
    )
    return 0 if not error else 1


def quality_finalize_bakeoff_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    run_dir = Path(config["runsDir"]) / args.project
    report_path = run_dir / "model_bakeoff_report.json"
    try:
        plan = json.loads(
            (run_dir / "model_bakeoff_plan.json").read_text(encoding="utf-8")
        )
        reviews = json.loads(Path(args.review).read_text(encoding="utf-8"))
        report = finalize_bakeoff(plan, reviews)
        error = ""
    except (ModelBakeoffError, OSError, json.JSONDecodeError, ValueError) as exc:
        report = {
            "production_ready": False,
            "selected_model": "",
            "selected_still_model": "",
            "video_results": [],
            "still_results": [],
        }
        error = str(exc)
    print(
        json.dumps(
            {
                "model_bakeoff_report": str(report_path),
                "production_ready": bool(report.get("production_ready")),
                "selected_model": str(report.get("selected_model") or ""),
                "selected_still_model": str(
                    report.get("selected_still_model") or ""
                ),
                "video_result_count": len(report.get("video_results") or []),
                "still_result_count": len(report.get("still_results") or []),
                "error": error,
            },
            ensure_ascii=False,
        )
    )
    return 0 if report.get("production_ready") is True and not error else 1


def quality_production_candidates_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    report_path = (
        Path(config["runsDir"])
        / args.project
        / "quality_production_candidates.json"
    )
    try:
        report = run_quality_production_candidates(
            config,
            args.project,
            candidate_number=args.candidate,
            kind=args.kind,
            micro_shot_ids=getattr(args, "micro_shot", None) or None,
            limit=args.limit,
            allow_network=args.enable_live,
            overwrite=args.overwrite,
            timeout_seconds=args.timeout,
            submit_timeout_seconds=args.submit_timeout,
            download_timeout_seconds=args.download_timeout,
            poll_interval_seconds=args.poll_interval,
            max_wait_seconds=args.max_wait,
        )
        error = ""
    except (
        QualityProductionRunnerError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        report = {
            "plan_ready": False,
            "executed": False,
            "success": False,
            "planned_count": 0,
            "completed_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "blocked_count": 0,
            "blocked_reasons": [],
            "selected_video_model": "",
            "selected_still_model": "",
        }
        error = str(exc)
    print(
        json.dumps(
            {
                "quality_production_candidates": str(report_path),
                "plan_ready": bool(report.get("plan_ready")),
                "executed": bool(report.get("executed")),
                "success": bool(report.get("success")),
                "planned_count": int(report.get("planned_count") or 0),
                "completed_count": int(report.get("completed_count") or 0),
                "skipped_count": int(report.get("skipped_count") or 0),
                "failed_count": int(report.get("failed_count") or 0),
                "blocked_count": int(report.get("blocked_count") or 0),
                "blocked_reasons": report.get("blocked_reasons") or [],
                "selected_video_model": str(
                    report.get("selected_video_model") or ""
                ),
                "selected_still_model": str(
                    report.get("selected_still_model") or ""
                ),
                "error": error,
            },
            ensure_ascii=False,
        )
    )
    return 0 if report.get("success") is True or (
        not args.enable_live and report.get("plan_ready") is True
    ) else 1


def quality_select_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    output_path = Path(config["runsDir"]) / args.project / "visual_selection.json"
    try:
        report = write_quality_visual_selection(
            config,
            args.project,
            args.selection,
        )
        error = ""
    except (
        QualityProductionRunnerError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        report = {
            "success": False,
            "output_path": str(output_path),
            "selected_count": 0,
            "video_count": 0,
            "still_count": 0,
        }
        error = str(exc)
    print(
        json.dumps(
            {
                "visual_selection": str(
                    report.get("output_path") or output_path
                ),
                "success": bool(report.get("success")),
                "selected_count": int(report.get("selected_count") or 0),
                "video_count": int(report.get("video_count") or 0),
                "still_count": int(report.get("still_count") or 0),
                "error": error,
            },
            ensure_ascii=False,
        )
    )
    return 0 if report.get("success") is True and not error else 1


_PET_RETRY_REASONS = frozenset(
    {
        "identity",
        "paw_anatomy",
        "mouth_anatomy",
        "wrong_speaker",
        "lip_timing",
        "continuity",
        "extra_content",
    }
)
PET_STAGE_ORDER = (
    "plan",
    "anchors",
    "audio",
    "audio-probe",
    "shots",
    "review",
    "compose",
    "status",
)
_PET_ANCHORS = ("naitang", "doubao", "living_room", "kitchen")
_PET_SHOTS = tuple(f"shot_{number:02d}" for number in range(1, 11))
_PET_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_PET_BEARER = re.compile(
    r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|bearer\s+)[^\s,;\"']+"
)
_PET_DATA_URI = re.compile(r"(?i)data:[^\s\"'<>]+")
_PET_RAW_BODY = re.compile(r"(?i)raw[-_ ]?(?:provider[-_ ]?)?(?:response|body)")
_PET_COOKIE_TEXT = re.compile(
    r"(?im)\b(?:set[-_ ]?cookie|cookie)\s*[:=][^\r\n]*"
)
_PET_CREDENTIAL_TEXT = re.compile(
    r"""(?ix)
    (?<![\w-])
    (?P<label>
        x[-_ ]?api[-_ ]?key
        | api[-_ ]?key
        | client[-_ ]?secret
        | refresh[-_ ]?token
        | access[-_ ]?token
        | key
        | token
    )
    (?![\w-])
    (?P<separator>\s*[:=]\s*)
    (?:
        "(?:\\.|[^"\\\r\n])*"
        | '(?:\\.|[^'\\\r\n])*'
        | [^\s,;&\r\n]+
    )
    """
)
_PET_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "api-key",
        "apikey",
        "key",
        "token",
        "access-token",
        "refresh-token",
        "client-secret",
        "x-api-key",
        "cookie",
        "set-cookie",
        "raw-body",
        "response-body",
        "provider-response",
    }
)


def _pet_sanitize(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    """Remove credentials and provider payloads from the CLI boundary."""
    sanitized = sanitize_pet_sitcom_report(value, known_secrets=secrets)
    return _pet_cli_sanitize(sanitized, secrets)


def _pet_cli_sanitize(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            normalized = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            if normalized in _PET_SENSITIVE_KEYS:
                result[name] = "[redacted]"
            else:
                result[name] = _pet_cli_sanitize(item, secrets)
        return result
    if isinstance(value, list):
        return [_pet_cli_sanitize(item, secrets) for item in value]
    if isinstance(value, tuple):
        return [_pet_cli_sanitize(item, secrets) for item in value]
    if not isinstance(value, str):
        return value
    if _PET_RAW_BODY.search(value):
        return "[redacted provider error]"
    sanitized = value
    for secret in secrets:
        if secret:
            sanitized = sanitized.replace(secret, "[redacted]")
    sanitized = _PET_COOKIE_TEXT.sub("[redacted cookie]", sanitized)
    sanitized = _PET_CREDENTIAL_TEXT.sub(
        r"\g<label>\g<separator>[redacted]", sanitized
    )
    sanitized = _PET_BEARER.sub(r"\1[redacted]", sanitized)
    sanitized = _PET_DATA_URI.sub("[redacted data URI]", sanitized)
    sanitized = _PET_URL.sub(_pet_sanitize_url, sanitized)
    return sanitized


def _pet_sanitize_url(match: re.Match[str]) -> str:
    url = match.group(0)
    try:
        query = parse_qsl(urlsplit(url).query, keep_blank_values=True)
    except ValueError:
        return "[redacted URL]"
    for key, _value in query:
        normalized = key.strip().lower().replace("_", "-")
        if (
            normalized in {"sig", "signature", "expires"}
            or normalized.endswith("-signature")
            or "token" in normalized
            or "credential" in normalized
        ):
            return "[redacted signed URL]"
    return "[redacted URL]"


def _pet_secrets(profile: Any | None = None, tts_config: Any | None = None) -> tuple[str, ...]:
    values: list[str] = []
    for source in (profile, tts_config):
        if source is None:
            continue
        if source is profile:
            capabilities = ("image", "video", "audio")
            values.extend(
                str(getattr(getattr(source, name, None), "api_key", "") or "")
                for name in capabilities
            )
        else:
            values.extend(
                str(getattr(source, name, "") or "")
                for name in ("api_key", "access_key", "app_id")
            )
    return tuple(value for value in values if value)


def _pet_output(
    plan: Any,
    args: argparse.Namespace,
    *,
    executed: bool = False,
    success: bool = True,
    composed: bool = False,
    planned_count: int = 0,
    completed_count: int = 0,
    reused_count: int = 0,
    resumed_count: int = 0,
    failed_count: int = 0,
    approved: bool = False,
    candidate_number: int | None = None,
    targets: list[str] | None = None,
    blocked_reasons: list[str] | None = None,
    error: str = "",
    artifacts: Mapping[str, Any] | None = None,
    next_stage: str = "",
    status: Mapping[str, Any] | None = None,
    reuse_sound_design: bool = False,
    secrets: tuple[str, ...] = (),
) -> dict[str, Any]:
    safe_stage = next_stage or args.stage
    payload: dict[str, Any] = {
        "stage": args.stage,
        "output_dir": str(plan.output_dir),
        "plan_path": str(plan.plan_path),
        "targets": targets or [],
        "candidate": candidate_number if candidate_number is not None else args.candidate,
        "retry_reason": args.retry_reason or "",
        "enable_live": bool(args.enable_live),
        "executed": executed,
        "composed": composed,
        "approved": approved,
        "success": success,
        "planned_count": planned_count,
        "completed_count": completed_count,
        "reused_count": reused_count,
        "resumed_count": resumed_count,
        "failed_count": failed_count,
        "blocked_reasons": blocked_reasons or [],
        "error": error,
        "artifacts": dict(artifacts or {}),
        "anchor_count": 4,
        "audio_count": 8,
        "audio_probe_count": 1,
        "shot_count": 10,
        "production_plan": {
            "anchor_count": 4,
            "audio_count": 8,
            "audio_probe_count": 1,
            "shot_count": 10,
            "total_count": 23,
        },
        "next_stage": safe_stage,
        "next_command": _pet_next_command(
            plan,
            args,
            safe_stage,
            reuse_sound_design=reuse_sound_design,
        ),
    }
    payload.update(dict(status or {}))
    return _pet_sanitize(payload, secrets)


def _pet_next_command(
    plan: Any,
    args: argparse.Namespace,
    stage: str,
    *,
    reuse_sound_design: bool = False,
) -> str:
    command_stage = "status" if stage == "blocked" else stage
    if stage == "audio-probe":
        probe = _pet_inspect_probe(plan, _pet_inspect_audio(plan))
        if probe.get("state") in {
            "approved",
            "pending",
            "unsupported",
            "inconclusive",
        }:
            command_stage = "status"
    command = [
        "python",
        "factory_cli.py",
        "--config",
        str(args.config),
        "pet-sitcom",
        "--stage",
        command_stage,
        "--output-dir",
        str(plan.output_dir),
    ]
    if command_stage in {"anchors", "audio", "audio-probe", "shots"}:
        command.append("--enable-live")
    if command_stage == "compose" and args.music_source:
        command.extend(["--music-source", str(args.music_source)])
    elif command_stage == "compose" and not reuse_sound_design:
        sound = _pet_inspect_sound(plan)
        command.extend(
            [
                "--music-source",
                str(
                    sound.get("music_source")
                    or "/absolute/path/to/approved/music.m4a"
                ),
            ]
        )
    return shlex.join(command)


def _pet_provider_error(profile: Any, capability: str) -> str:
    item = getattr(profile, capability)
    if getattr(item, "provider", "") != "gateway":
        return f"{capability} provider must be configured as gateway for pet sitcom."
    if not getattr(item, "ready", False):
        blockers = list(getattr(item, "blockers", ()) or ())
        return blockers[0] if blockers else f"{capability} provider is not ready."
    if not getattr(item, "api_key", "") or not getattr(item, "base_url", ""):
        return f"{capability} gateway credentials are not ready."
    return ""


def _pet_image_client(profile: Any, args: argparse.Namespace) -> GatewayImageClient:
    return GatewayImageClient(
        GatewayImageConfig(
            api_key=profile.image.api_key,
            base_url=profile.image.base_url,
            model=PET_IMAGE_MODEL,
            timeout_seconds=args.timeout,
            download_timeout_seconds=args.download_timeout,
        )
    )


def _pet_video_client(profile: Any, args: argparse.Namespace) -> GatewayVideoClient:
    return GatewayVideoClient(
        GatewayVideoConfig(
            api_key=profile.video.api_key,
            base_url=profile.video.base_url,
            model=PET_VIDEO_MODEL,
            timeout_seconds=args.timeout,
            submit_timeout_seconds=args.submit_timeout,
            download_timeout_seconds=args.download_timeout,
            poll_interval_seconds=args.poll_interval,
            max_wait_seconds=args.max_wait,
        )
    )


def _pet_validate_candidate_request(args: argparse.Namespace) -> None:
    reason = args.retry_reason or ""
    if args.candidate == 1 and reason:
        raise ValueError("candidate 1 does not accept a retry reason.")
    if args.candidate == 1:
        return
    if reason not in _PET_RETRY_REASONS:
        raise ValueError(
            f"candidate {args.candidate} requires one allowed retry reason."
        )
    if args.stage == "shots" and len(args.shot) != 1:
        raise ValueError(
            f"candidate {args.candidate} requires exactly one --shot target."
        )
    if args.stage == "anchors":
        raise ValueError(
            f"anchors do not support candidate {args.candidate}."
        )
    if args.stage != "shots":
        raise ValueError(
            f"candidate {args.candidate} is not applicable to stage {args.stage}."
        )
    if args.stage == "shots" and reason not in PET_RETRY_SUFFIXES:
        raise ValueError(
            "shot retry reason must be supported by the Task 5 generator."
        )


def _pet_stage_targets(plan: Any, args: argparse.Namespace) -> list[str]:
    if args.stage == "anchors":
        return list(args.anchor or _PET_ANCHORS)
    if args.stage == "audio":
        return [
            shot.shot_id for shot in plan.shots if getattr(shot, "dialogue", "")
        ]
    if args.stage == "audio-probe":
        return ["shot_04"]
    if args.stage == "shots":
        return list(args.shot or [shot.shot_id for shot in plan.shots])
    if args.stage == "compose":
        return ["clean_output", "release_output"]
    if args.stage == "review":
        return ["source_evidence", "manual_reviews"]
    if args.stage == "status":
        return list(PET_STAGE_ORDER[:-1])
    return ["immutable_plan"]


def _pet_all_current_selections(plan: Any) -> bool:
    """Use Task 5's strong selection validation rather than parsing selection JSON."""
    try:
        build_source_evidence(plan)
    except (PetSitcomReviewError, PetSitcomGenerationError, PetSitcomComposeError, OSError, ValueError):
        return False
    return True


def _pet_read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _pet_anchor_review_path(plan: Any) -> Path:
    return Path(plan.output_dir) / "anchor_review_template.json"


def _require_failed_previous_pet_shot_review(
    plan: Any,
    shot_id: str,
    candidate_number: int,
    retry_reason: str,
) -> None:
    record = validate_pet_shot_review(plan, shot_id)
    previous_candidate = candidate_number - 1
    if record.get("candidate") != previous_candidate:
        raise PetSitcomReviewError(
            f"candidate {candidate_number} requires current candidate "
            f"{previous_candidate} selection for {shot_id}."
        )
    if record.get("passed") is not False:
        raise PetSitcomReviewError(
            f"candidate {candidate_number} requires a failed structured candidate "
            f"{previous_candidate} review for {shot_id}."
        )
    if record.get("retry_reason") != retry_reason:
        raise PetSitcomReviewError(
            f"candidate {candidate_number} retry reason does not match the failed "
            f"review for {shot_id}."
        )


def _pet_path_has_symlink(path: Path) -> bool:
    current = path.absolute()
    while True:
        if current.is_symlink():
            return True
        if current.parent == current:
            return False
        current = current.parent


def _pet_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _pet_json_constant(value: str) -> Any:
    raise ValueError(f"Non-finite JSON constant is forbidden: {value}")


def _pet_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field is forbidden: {key}")
        result[key] = value
    return result


def _pet_read_strict_json(
    path: Path,
    *,
    fields: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    if _pet_path_has_symlink(path):
        raise ValueError(f"{path.name} may not traverse a symlink.")
    try:
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode):
            raise ValueError(f"{path.name} must be a regular file.")
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_pet_json_constant,
            object_pairs_hook=_pet_json_pairs,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(f"{path.name} is missing or invalid.") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain one JSON object.")
    if fields is not None and set(value) != set(fields):
        raise ValueError(f"{path.name} fields do not match its exact schema.")
    return value


def _pet_digest(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("SHA-256 value is invalid.")
    return value


def _pet_canonical_file(
    path_value: Any,
    *,
    root: Path | None,
    expected: Path | None = None,
) -> Path:
    if not isinstance(path_value, str) or not path_value:
        raise ValueError("Artifact path is missing.")
    path = Path(path_value)
    if not path.is_absolute() or _pet_path_has_symlink(path):
        raise ValueError("Artifact path must be absolute and symlink-free.")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("Artifact path is missing.") from exc
    if path != resolved or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Artifact path must be canonical and regular.")
    if root is not None:
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("Artifact path escapes the project.") from exc
    if expected is not None and path != expected:
        raise ValueError("Artifact path does not match its fixed target.")
    return path


def _pet_bound_file(
    path_value: Any,
    digest_value: Any,
    *,
    root: Path | None,
    expected: Path | None = None,
) -> Path:
    path = _pet_canonical_file(path_value, root=root, expected=expected)
    digest = _pet_digest(digest_value)
    if _pet_file_sha256(path) != digest:
        raise ValueError(f"{path.name} hash is stale.")
    return path


def _pet_finite_number(
    value: Any,
    *,
    minimum: float | None = None,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ValueError("Numeric evidence must be finite and not boolean.")
    number = float(value)
    if minimum is not None and number < minimum:
        raise ValueError("Numeric evidence is outside its allowed range.")
    return number


def _pet_plan_is_current(plan: Any) -> bool:
    path = Path(plan.plan_path)
    try:
        expected = plan.to_report()
        persisted = _pet_read_strict_json(
            path,
            fields=set(expected),
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return persisted == expected


def _pet_inspect_anchors(plan: Any) -> dict[str, Any]:
    root = Path(plan.output_dir)
    try:
        jobs = pet_sitcom_generation_module._anchor_jobs(plan)
        source_hashes: dict[str, str] = {}
        state_fields = {
            "schema_version",
            "signature",
            "provider",
            "model",
            "prompt",
            "size",
            "candidate_number",
            "output_sha256",
        }
        for job in jobs:
            output = Path(job["output"])
            _pet_canonical_file(
                str(output),
                root=root,
                expected=output,
            )
            signature = pet_sitcom_generation_module._hash_payload(
                {
                    "provider": pet_sitcom_generation_module.IMAGE_PROVIDER,
                    "model": pet_sitcom_generation_module.IMAGE_MODEL,
                    "prompt": job["prompt"],
                    "size": pet_sitcom_generation_module.IMAGE_SIZE,
                    "candidate_number": 1,
                }
            )
            state_path = (
                pet_sitcom_generation_module._anchor_state_path(output)
            )
            state = _pet_read_strict_json(
                state_path,
                fields=state_fields,
            )
            digest = _pet_file_sha256(output)
            expected_state = {
                "schema_version": (
                    pet_sitcom_generation_module.ANCHOR_STATE_SCHEMA
                ),
                "signature": signature,
                "provider": pet_sitcom_generation_module.IMAGE_PROVIDER,
                "model": pet_sitcom_generation_module.IMAGE_MODEL,
                "prompt": job["prompt"],
                "size": pet_sitcom_generation_module.IMAGE_SIZE,
                "candidate_number": 1,
                "output_sha256": digest,
            }
            if state != expected_state:
                raise ValueError("Anchor state is stale.")
        source_hashes = pet_sitcom_generation_module._anchor_hashes(plan)
        review_fields = {
            "schema_version",
            "completed",
            "approved",
            "source_hashes",
            *pet_sitcom_generation_module._ANCHOR_REVIEW_FIELDS,
        }
        review = _pet_read_strict_json(
            _pet_anchor_review_path(plan),
            fields=review_fields,
        )
        if (
            review.get("schema_version")
            != pet_sitcom_generation_module.ANCHOR_REVIEW_SCHEMA
            or review.get("completed") is not True
            or review.get("approved") is not True
            or review.get("source_hashes") != source_hashes
            or any(
                review.get(field) is not True
                for field in (
                    pet_sitcom_generation_module._ANCHOR_REVIEW_FIELDS
                )
            )
        ):
            raise ValueError("Anchor approval is missing or stale.")
        return {"approved": True, "source_hashes": source_hashes}
    except (AttributeError, OSError, TypeError, ValueError):
        return {"approved": False, "source_hashes": {}}


def _pet_inspect_audio(plan: Any) -> dict[str, Any]:
    root = Path(plan.output_dir)
    manifest_fields = {
        "schema_version",
        "plan_schema_version",
        "project_id",
        "plan_sha256",
        "duration_seconds",
        "assets",
    }
    asset_fields = {
        "shot_id",
        "speaker",
        "text",
        "voice_id",
        "speech_rate",
        "output_path",
        "output_sha256",
        "duration_seconds",
        "absolute_start_seconds",
        "absolute_end_seconds",
    }
    try:
        document = _pet_read_strict_json(
            Path(plan.audio_manifest_path),
            fields=manifest_fields,
        )
        if (
            document.get("schema_version")
            != pet_sitcom_audio_first_module.AUDIO_FIRST_SCHEMA
            or document.get("plan_schema_version")
            != pet_sitcom_audio_first_module.PLAN_SCHEMA_VERSION
            or document.get("project_id") != plan.project_id
            or document.get("plan_sha256")
            != pet_sitcom_audio_first_module._plan_hash(plan)
            or document.get("duration_seconds") != plan.duration_seconds
        ):
            raise ValueError("Audio manifest is not current-plan bound.")
        records = document.get("assets")
        shots = tuple(shot for shot in plan.shots if shot.dialogue)
        if not isinstance(records, list) or len(records) != len(shots):
            raise ValueError("Audio manifest must contain eight spoken shots.")
        starts = pet_sitcom_audio_first_module._shot_start_times(plan)
        assets: dict[str, dict[str, Any]] = {}
        dataclasses: dict[str, Any] = {}
        for shot, record in zip(shots, records, strict=True):
            if not isinstance(record, Mapping) or set(record) != asset_fields:
                raise ValueError("Audio asset fields are invalid.")
            duration = _pet_finite_number(
                record.get("duration_seconds"),
                minimum=pet_sitcom_audio_first_module.MINIMUM_DURATION_SECONDS,
            )
            absolute_start = _pet_finite_number(
                record.get("absolute_start_seconds"),
                minimum=0,
            )
            absolute_end = _pet_finite_number(
                record.get("absolute_end_seconds"),
                minimum=0,
            )
            voice = pet_sitcom_audio_first_module.PET_VOICES.get(
                str(shot.speaker)
            )
            expected_path = pet_sitcom_audio_first_module._speech_output(
                plan,
                shot,
            )
            _pet_bound_file(
                record.get("output_path"),
                record.get("output_sha256"),
                root=root,
                expected=expected_path,
            )
            expected_start = (
                starts[shot.shot_id] + shot.dialogue_offset_seconds
            )
            expected_end = expected_start + duration
            available_end = (
                starts[shot.shot_id]
                + shot.duration_seconds
                - pet_sitcom_audio_first_module.DIALOGUE_TAIL_SECONDS
            )
            if (
                voice is None
                or record.get("shot_id") != shot.shot_id
                or record.get("speaker") != shot.speaker
                or record.get("text") != shot.dialogue
                or record.get("voice_id") != voice.voice_id
                or type(record.get("speech_rate")) is not int
                or record.get("speech_rate") != voice.speech_rate
                or abs(absolute_start - expected_start) > 1e-6
                or abs(absolute_end - expected_end) > 1e-6
                or absolute_end > available_end + 1e-9
            ):
                raise ValueError("Audio asset metadata is stale.")
            assets[shot.shot_id] = dict(record)
            dataclasses[shot.shot_id] = (
                pet_sitcom_audio_first_module._asset_from_record(record)
            )
        return {
            "ready": len(assets) == 8,
            "manifest": document,
            "manifest_sha256": _pet_file_sha256(plan.audio_manifest_path),
            "assets": assets,
            "asset_objects": dataclasses,
            "reason": "",
        }
    except (AttributeError, OSError, TypeError, ValueError):
        return {
            "ready": False,
            "manifest": {},
            "manifest_sha256": "",
            "assets": {},
            "asset_objects": {},
            "reason": "current immutable audio evidence is missing or stale",
        }


def _pet_inspect_drive(
    plan: Any,
    shot: Any,
    audio: Mapping[str, Any],
) -> dict[str, Any]:
    root = Path(plan.output_dir)
    state_fields = {
        "schema_version",
        "status",
        "signature",
        "shot_id",
        "dialogue_offset_seconds",
        "generation_duration_seconds",
        "source_path",
        "source_sha256",
        "source_duration_seconds",
        "absolute_start_seconds",
        "absolute_end_seconds",
        "output_path",
        "output_sha256",
    }
    asset = (audio.get("assets") or {}).get(shot.shot_id)
    asset_object = (audio.get("asset_objects") or {}).get(shot.shot_id)
    if not isinstance(asset, Mapping) or asset_object is None:
        raise ValueError("Drive audio has no current Task 2 source.")
    output = (
        root / "audio" / "drive" / f"{shot.shot_id}_drive.wav"
    )
    state_path = output.with_suffix(".state.json")
    state = _pet_read_strict_json(state_path, fields=state_fields)
    _pet_bound_file(
        state.get("output_path"),
        state.get("output_sha256"),
        root=root,
        expected=output,
    )
    expected = {
        "schema_version": (
            pet_sitcom_audio_first_module.DRIVE_AUDIO_STATE_SCHEMA
        ),
        "status": "completed",
        "signature": pet_sitcom_audio_first_module._drive_signature(
            shot,
            asset_object,
        ),
        "shot_id": shot.shot_id,
        "dialogue_offset_seconds": shot.dialogue_offset_seconds,
        "generation_duration_seconds": shot.generation_duration_seconds,
        "source_path": asset["output_path"],
        "source_sha256": asset["output_sha256"],
        "source_duration_seconds": asset["duration_seconds"],
        "absolute_start_seconds": asset["absolute_start_seconds"],
        "absolute_end_seconds": asset["absolute_end_seconds"],
        "output_path": str(output),
        "output_sha256": _pet_file_sha256(output),
    }
    if state != expected:
        raise ValueError("Drive audio state is stale.")
    return {
        "path": output,
        "sha256": state["output_sha256"],
        "state": state,
    }


def _pet_probe_result(
    state: str,
    reason: str,
    *,
    capability: str = "",
) -> dict[str, Any]:
    return {
        "state": state,
        "approved": state == "approved",
        "capability": capability,
        "reason": reason,
    }


def _pet_inspect_probe(
    plan: Any,
    audio: Mapping[str, Any],
) -> dict[str, Any]:
    path = Path(plan.audio_probe_path)
    if not path.exists() and not path.is_symlink():
        return _pet_probe_result(
            "missing",
            "audio-drive probe evidence is missing",
        )
    root = Path(plan.output_dir)
    try:
        report = _pet_read_strict_json(path)
        capability = report.get("capability")
        if (
            not isinstance(capability, str)
            or capability not in pet_sitcom_audio_probe_module._CAPABILITIES
            or set(report)
            != set(pet_sitcom_audio_probe_module._OUTCOME_FIELDS[capability])
            or report.get("schema_version")
            != pet_sitcom_audio_probe_module.PROBE_SCHEMA
            or report.get("executed") is not True
            or type(report.get("success")) is not bool
            or report.get("source_shot_id")
            != pet_sitcom_audio_probe_module.PROBE_SOURCE_SHOT_ID
            or report.get("model")
            != pet_sitcom_audio_probe_module.PROBE_MODEL
        ):
            raise ValueError("Probe outcome schema is invalid.")
        if capability == "supported" and report["success"] is not True:
            raise ValueError("Supported probe must be successful.")
        if capability != "supported" and report["success"] is not False:
            raise ValueError("Terminal probe must be unsuccessful.")
        if (
            capability == "unsupported"
            and (
                isinstance(report.get("http_status_code"), bool)
                or report.get("http_status_code") != 400
            )
        ):
            raise ValueError("Unsupported probe must bind HTTP 400.")
        if capability == "inconclusive":
            pet_sitcom_audio_probe_module._validate_inconclusive_task(report)
        if audio.get("ready") is not True:
            raise ValueError("Probe requires current audio.")
        shot = next(
            item
            for item in plan.shots
            if item.shot_id
            == pet_sitcom_audio_probe_module.PROBE_SOURCE_SHOT_ID
        )
        asset = (audio.get("assets") or {}).get(shot.shot_id)
        drive = _pet_inspect_drive(plan, shot, audio)
        character = next(
            item for item in plan.characters if item.slug == "doubao"
        )
        scene = next(
            item for item in plan.scenes if item.slug == "kitchen"
        )
        references = []
        for role, expected in (
            ("doubao_character", character.reference_path),
            ("kitchen_scene", scene.anchor_path),
        ):
            _pet_canonical_file(
                str(expected),
                root=root,
                expected=expected,
            )
            references.append(
                {
                    "role": role,
                    "path": str(expected),
                    "sha256": _pet_file_sha256(expected),
                }
            )
        manifest_path = _pet_bound_file(
            report.get("audio_manifest_path"),
            report.get("audio_manifest_sha256"),
            root=root,
            expected=Path(plan.audio_manifest_path),
        )
        expected_common = {
            "model": pet_sitcom_audio_probe_module.PROBE_MODEL,
            "prompt_sha256": pet_sitcom_audio_probe_module._hash_text(
                pet_sitcom_audio_probe_module._probe_prompt(plan)
            ),
            "references": references,
            "source_tts_path": asset["output_path"],
            "source_tts_sha256": asset["output_sha256"],
            "audio_manifest_path": str(manifest_path),
            "audio_manifest_sha256": audio["manifest_sha256"],
            "drive_audio_path": str(drive["path"]),
            "drive_audio_sha256": drive["sha256"],
        }
        if any(report.get(key) != value for key, value in expected_common.items()):
            raise ValueError("Probe source bindings are stale.")
        _pet_bound_file(
            report.get("gateway_report_path"),
            report.get("gateway_report_sha256"),
            root=root,
        )
        if capability == "unsupported":
            return _pet_probe_result(
                "unsupported",
                "Seedance model does not support reference audio; inspect the "
                "persisted HTTP 400 evidence and do not retry automatically",
                capability=capability,
            )
        if capability == "inconclusive":
            return _pet_probe_result(
                "inconclusive",
                "Seedance probe task status is uncertain; inspect the persisted "
                "task evidence and do not retry automatically",
                capability=capability,
            )
        _pet_bound_file(
            report.get("probe_mp4_path"),
            report.get("probe_mp4_sha256"),
            root=root,
        )
        frames = report.get("frame_evidence")
        timestamps = pet_sitcom_audio_probe_module.PROBE_FRAME_TIMESTAMPS
        if not isinstance(frames, list) or len(frames) != len(timestamps):
            raise ValueError("Probe frame evidence is incomplete.")
        for frame, timestamp in zip(frames, timestamps, strict=True):
            if (
                not isinstance(frame, Mapping)
                or set(frame) != {"timestamp_seconds", "path", "sha256"}
                or isinstance(frame.get("timestamp_seconds"), bool)
                or frame.get("timestamp_seconds") != timestamp
            ):
                raise ValueError("Probe frame evidence is invalid.")
            _pet_bound_file(
                frame.get("path"),
                frame.get("sha256"),
                root=root,
            )
        review_path = Path(plan.audio_probe_review_path)
        if not review_path.exists() and not review_path.is_symlink():
            return _pet_probe_result(
                "pending",
                "supported probe requires completed hash-bound human review",
                capability=capability,
            )
        review_bindings = {
            "probe_report_path": str(path),
            "probe_report_sha256": _pet_file_sha256(path),
            **{
                key: report[key]
                for key in (
                    "model",
                    "prompt_sha256",
                    "references",
                    "source_tts_path",
                    "source_tts_sha256",
                    "drive_audio_path",
                    "drive_audio_sha256",
                    "gateway_report_path",
                    "gateway_report_sha256",
                    "probe_mp4_path",
                    "probe_mp4_sha256",
                    "frame_evidence",
                )
            },
        }
        review_fields = {
            "schema_version",
            *review_bindings,
            "completed",
            "approved",
            "audio_onset_seconds",
            "mouth_onset_seconds",
            "audio_offset_seconds",
            "mouth_offset_seconds",
            *pet_sitcom_audio_probe_module.PROBE_REVIEW_GATES,
            "notes",
        }
        review = _pet_read_strict_json(
            review_path,
            fields=review_fields,
        )
        if (
            review.get("schema_version")
            != pet_sitcom_audio_probe_module.PROBE_REVIEW_SCHEMA
            or any(
                review.get(key) != value
                for key, value in review_bindings.items()
            )
            or review.get("completed") is not True
            or review.get("approved") is not True
            or any(
                review.get(gate) is not True
                for gate in (
                    pet_sitcom_audio_probe_module.PROBE_REVIEW_GATES
                )
            )
            or not isinstance(review.get("notes"), str)
        ):
            return _pet_probe_result(
                "pending",
                "supported probe human review is incomplete or stale",
                capability=capability,
            )
        pet_sitcom_audio_probe_module._validate_review_timing(review)
        return _pet_probe_result(
            "approved",
            "",
            capability=capability,
        )
    except (
        AttributeError,
        OSError,
        StopIteration,
        TypeError,
        ValueError,
        PetSitcomGenerationError,
    ):
        return _pet_probe_result(
            "stale",
            "audio-drive probe evidence is invalid, stale, or unsafe",
        )


def _pet_inspect_continuity(
    plan: Any,
    shot: Any,
    candidate: Path,
) -> dict[str, Any]:
    root = Path(plan.output_dir)
    frame = pet_sitcom_generation_module._pet_continuity_frame_path(
        plan,
        shot.shot_id,
    )
    sidecar = (
        pet_sitcom_generation_module._pet_continuity_state_path(frame)
    )
    _pet_canonical_file(str(frame), root=root, expected=frame)
    state = _pet_read_strict_json(
        sidecar,
        fields=pet_sitcom_generation_module._PET_CONTINUITY_FIELDS,
    )
    source_duration = _pet_finite_number(
        state.get("source_video_duration_seconds"),
        minimum=0.08,
    )
    edit_duration = _pet_finite_number(
        state.get("edit_duration_seconds"),
        minimum=0.08,
    )
    timestamp = _pet_finite_number(
        state.get("timestamp_seconds"),
        minimum=0,
    )
    expected_timestamp = min(edit_duration - 0.08, source_duration - 0.08)
    if (
        state.get("schema_version")
        != pet_sitcom_generation_module.PET_CONTINUITY_SCHEMA
        or state.get("source_video_path") != str(candidate)
        or state.get("source_video_sha256") != _pet_file_sha256(candidate)
        or edit_duration != float(shot.duration_seconds)
        or abs(timestamp - expected_timestamp) > 1e-9
        or not pet_sitcom_review_module._iso(state.get("extracted_at"))
        or state.get("frame_sha256") != _pet_file_sha256(frame)
    ):
        raise ValueError("Continuity evidence is stale.")
    return {
        "frame": frame,
        "frame_sha256": state["frame_sha256"],
        "sidecar": sidecar,
        "state": state,
    }


def _pet_selection_state_fields(
    state: Mapping[str, Any],
) -> frozenset[str]:
    schema_version = state.get("schema_version")
    if (
        schema_version
        == pet_sitcom_generation_module.PET_LOCAL_RECUT_SCHEMA
    ):
        return pet_sitcom_generation_module._PET_LOCAL_RECUT_FIELDS
    if (
        schema_version
        == pet_sitcom_generation_module.PET_SHOT_GENERATION_SCHEMA
    ):
        return pet_sitcom_generation_module._PET_PROVENANCE_FIELDS
    raise ValueError("Selection provenance schema is invalid.")


def _pet_inspect_selections(
    plan: Any,
    audio: Mapping[str, Any],
) -> dict[str, Any]:
    path = Path(plan.selection_path)
    if not path.exists() and not path.is_symlink():
        return {"count": 0, "sources": {}, "reason": ""}
    root = Path(plan.output_dir)
    sources: dict[str, dict[str, Any]] = {}
    try:
        document = _pet_read_strict_json(
            path,
            fields=pet_sitcom_review_module._SELECTION_TOP_FIELDS,
        )
        if (
            document.get("schema_version")
            != pet_sitcom_generation_module.PET_SELECTION_SCHEMA
            or not isinstance(document.get("shots"), Mapping)
            or not isinstance(document.get("history"), Mapping)
        ):
            raise ValueError("Selection schema is invalid.")
        shot_ids = [shot.shot_id for shot in plan.shots]
        records = document["shots"]
        if any(key not in shot_ids for key in records):
            raise ValueError("Selection contains an unknown shot.")
        history = document["history"]
        if any(key not in shot_ids for key in history):
            raise ValueError("Selection history contains an unknown shot.")
        for records_list in history.values():
            if not isinstance(records_list, list):
                raise ValueError("Selection history must contain lists.")
            for item in records_list:
                if (
                    not isinstance(item, Mapping)
                    or set(item)
                    != set(
                        pet_sitcom_review_module._SELECTION_ENTRY_FIELDS
                    )
                    or item.get("status") != "selected"
                    or type(item.get("candidate_number")) is not int
                    or item["candidate_number"]
                    not in {1, 2, 3, 4, 5, 6}
                    or not pet_sitcom_review_module._iso(
                        item.get("selected_at")
                    )
                ):
                    raise ValueError("Selection history schema is invalid.")
        seen_gap = False
        for shot in plan.shots:
            entry = records.get(shot.shot_id)
            if entry is None:
                seen_gap = True
                continue
            if seen_gap:
                raise ValueError("Selections must form one dependency-safe prefix.")
            if (
                not isinstance(entry, Mapping)
                or set(entry)
                != set(pet_sitcom_review_module._SELECTION_ENTRY_FIELDS)
                or entry.get("status") != "selected"
                or type(entry.get("candidate_number")) is not int
                or entry["candidate_number"] not in {1, 2, 3, 4, 5, 6}
                or not pet_sitcom_review_module._iso(entry.get("selected_at"))
            ):
                raise ValueError("Selection entry schema is invalid.")
            candidate_number = entry["candidate_number"]
            candidate = (
                pet_sitcom_generation_module._pet_candidate_path(
                    shot,
                    candidate_number,
                )
            )
            _pet_bound_file(
                entry.get("video_path"),
                entry.get("video_sha256"),
                root=root,
                expected=candidate,
            )
            state_path = (
                pet_sitcom_generation_module._pet_candidate_state_path(
                    candidate
                )
            )
            state = _pet_read_strict_json(state_path)
            expected_state_fields = _pet_selection_state_fields(state)
            if set(state) != set(expected_state_fields):
                raise ValueError("Selection provenance schema is invalid.")
            is_local_recut = (
                state.get("schema_version")
                == pet_sitcom_generation_module.PET_LOCAL_RECUT_SCHEMA
            )
            gateway_path = (
                pet_sitcom_generation_module._pet_gateway_report_path(
                    candidate
                )
            )
            gateway = _pet_read_strict_json(gateway_path)
            retry_reason = state.get("retry_reason")
            if candidate_number == 1:
                retry_reason = ""
            if (
                candidate_number > 1
                and retry_reason
                not in pet_sitcom_generation_module.PET_RETRY_SUFFIXES
            ):
                raise ValueError("Selection retry reason is invalid.")
            prompt = (
                ""
                if is_local_recut
                else pet_sitcom_generation_module._pet_shot_prompt(
                    shot,
                    candidate_number,
                    str(retry_reason),
                )
            )
            scene = next(
                item for item in plan.scenes if item.slug == shot.scene_slug
            )
            references = [
                plan.characters[0].reference_path,
                plan.characters[1].reference_path,
                scene.anchor_path,
            ]
            dependency_hashes: dict[str, str] = {}
            for source_id in shot.continuity_source_ids:
                source = sources.get(source_id)
                if source is None:
                    raise ValueError("Selection dependency is missing.")
                if not (
                    shot.shot_id == "shot_07"
                    and source_id == "shot_05"
                ):
                    references.append(source["continuity_frame_path"])
                dependency_hashes[source_id] = source["sha256"]
            reference_paths = []
            reference_hashes = []
            for reference in references:
                _pet_canonical_file(
                    str(reference),
                    root=root,
                    expected=reference,
                )
                reference_paths.append(str(reference))
                reference_hashes.append(_pet_file_sha256(reference))
            drive_path = ""
            drive_hash = ""
            source_tts_hash = ""
            asset = (audio.get("assets") or {}).get(shot.shot_id)
            if shot.speaker in {"naitang", "doubao"}:
                drive = _pet_inspect_drive(plan, shot, audio)
                drive_path = str(drive["path"])
                drive_hash = str(drive["sha256"])
                if not isinstance(asset, Mapping):
                    raise ValueError("Selection TTS source is missing.")
                source_tts_hash = str(asset["output_sha256"])
            if (
                state.get("schema_version")
                == pet_sitcom_generation_module.PET_LOCAL_RECUT_SCHEMA
            ):
                pet_sitcom_generation_module._validate_candidate_for_selection(
                    plan,
                    shot,
                    candidate_number,
                    candidate,
                    state,
                    [Path(path) for path in reference_paths],
                    records,
                    Path(drive_path) if drive_path else None,
                    source_tts_hash,
                )
            else:
                expected_binding = {
                    "schema_version": (
                        pet_sitcom_generation_module.PET_SHOT_GENERATION_SCHEMA
                    ),
                    "shot_id": shot.shot_id,
                    "candidate_number": candidate_number,
                    "provider": pet_sitcom_generation_module.VIDEO_PROVIDER,
                    "model": pet_sitcom_generation_module.VIDEO_MODEL,
                    "base_prompt_sha256": (
                        pet_sitcom_generation_module._hash_payload(
                            {"prompt": shot.base_prompt}
                        )
                    ),
                    "prompt_sha256": (
                        pet_sitcom_generation_module._hash_payload(
                            {"prompt": prompt}
                        )
                    ),
                    "retry_reason": (
                        "" if candidate_number == 1 else retry_reason
                    ),
                    "retry_suffix": (
                        ""
                        if candidate_number == 1
                        else pet_sitcom_generation_module.PET_RETRY_SUFFIXES[
                            str(retry_reason)
                        ]
                    ),
                    "reference_paths": reference_paths,
                    "reference_sha256": reference_hashes,
                    "dependency_video_sha256": dependency_hashes,
                    "source_tts_sha256": source_tts_hash,
                    "reference_audio_path": drive_path,
                    "reference_audio_sha256": drive_hash,
                    "generation_duration_seconds": (
                        shot.generation_duration_seconds
                    ),
                    "generate_audio": bool(drive_path),
                }
                if (
                    any(
                        state.get(key) != value
                        for key, value in expected_binding.items()
                    )
                    or state.get("gateway_report_path") != str(gateway_path)
                    or state.get("video_sha256")
                    != _pet_file_sha256(candidate)
                    or state.get("provider_success") is not True
                    or gateway.get("success") is not True
                    or gateway.get("pet_sitcom_provenance") != state
                ):
                    raise ValueError("Selection provenance is stale.")
            continuity = _pet_inspect_continuity(
                plan,
                shot,
                candidate,
            )
            expected_entry = {
                "candidate_number": candidate_number,
                "status": "selected",
                "video_path": str(candidate),
                "video_sha256": state["video_sha256"],
                "prompt_sha256": state["prompt_sha256"],
                "reference_paths": state["reference_paths"],
                "reference_sha256": state["reference_sha256"],
                "dependency_video_sha256": dependency_hashes,
                "source_tts_sha256": source_tts_hash,
                "reference_audio_sha256": drive_hash,
                "continuity_frame_path": str(continuity["frame"]),
                "continuity_sidecar_path": str(continuity["sidecar"]),
                "continuity_frame_sha256": continuity["frame_sha256"],
                "continuity_timestamp_seconds": (
                    continuity["state"]["timestamp_seconds"]
                ),
            }
            if any(
                entry.get(key) != value
                for key, value in expected_entry.items()
            ):
                raise ValueError("Selection entry is stale.")
            source = {
                "path": candidate,
                "sha256": state["video_sha256"],
                "candidate_number": candidate_number,
                "reference_audio_path": drive_path,
                "reference_audio_sha256": drive_hash,
                "source_tts_sha256": source_tts_hash,
                "dependency_video_sha256": dependency_hashes,
                "audio_onset_seconds": (
                    float(shot.dialogue_offset_seconds)
                    if drive_path
                    else None
                ),
                "audio_offset_seconds": (
                    float(shot.dialogue_offset_seconds)
                    + float(asset["duration_seconds"])
                    if drive_path and isinstance(asset, Mapping)
                    else None
                ),
                "continuity_source_video_duration_seconds": (
                    continuity["state"][
                        "source_video_duration_seconds"
                    ]
                ),
                "edit_duration_seconds": shot.duration_seconds,
                "continuity_timestamp_seconds": (
                    continuity["state"]["timestamp_seconds"]
                ),
                "continuity_frame_path": continuity["frame"],
            }
            sources[shot.shot_id] = source
        return {"count": len(sources), "sources": sources, "reason": ""}
    except (
        AttributeError,
        KeyError,
        OSError,
        StopIteration,
        TypeError,
        ValueError,
    ):
        return {
            "count": len(sources),
            "sources": sources,
            "reason": "selection evidence is invalid, stale, or unsafe",
        }


def _pet_inspect_owner_review(
    plan: Any,
    sources: Mapping[str, Any],
) -> bool:
    try:
        owner_path = Path(plan.output_dir) / "owner_native_audio_review.json"
        owner = _pet_read_strict_json(
            owner_path,
            fields=pet_sitcom_review_module._OWNER_TOP_FIELDS,
        )
        owner_records = owner.get("shots")
        owner_ids = {
            shot.shot_id
            for shot in plan.shots
            if shot.speaker == "owner"
        }
        if not (
            owner.get("schema_version")
            == pet_sitcom_review_module.OWNER_NATIVE_AUDIO_REVIEW_SCHEMA
            and owner.get("reviewed") is True
            and owner.get("verified") is True
            and pet_sitcom_review_module._iso(owner.get("generated_at"))
            and owner.get("reviewer_method")
            == pet_sitcom_review_module.OWNER_REVIEW_METHOD
            and isinstance(owner_records, Mapping)
            and set(owner_records) == owner_ids
        ):
            return False
        for shot_id in owner_ids:
            record = owner_records[shot_id]
            source = sources[shot_id]
            if not (
                isinstance(record, Mapping)
                and set(record)
                == set(pet_sitcom_review_module._OWNER_RECORD_FIELDS)
                and record.get("selected_mp4_path")
                == str(source["path"])
                and record.get("selected_mp4_sha256") == source["sha256"]
                and record.get("no_native_voice") is True
                and record.get("room_tone_allowed") is True
                and record.get("reviewer_method")
                == pet_sitcom_review_module.OWNER_REVIEW_METHOD
                and pet_sitcom_review_module._iso(
                    record.get("reviewed_at")
                )
                and isinstance(record.get("notes"), str)
            ):
                return False
        return True
    except (
        AttributeError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ):
        return False


def _pet_inspect_reviews(
    plan: Any,
    selections: Mapping[str, Any],
) -> dict[str, Any]:
    sources = selections.get("sources")
    if not isinstance(sources, Mapping):
        return {
            "passed_count": 0,
            "owner_verified": False,
            "source_valid": False,
        }
    source_evidence = _pet_inspect_source_evidence(plan, selections)
    if (
        source_evidence.get("valid") is not True
        or not _pet_inspect_incremental_evidence(plan, selections)
    ):
        return {
            "passed_count": 0,
            "owner_verified": False,
            "source_valid": False,
        }
    owner_verified = _pet_inspect_owner_review(plan, sources)
    try:
        document = _pet_read_strict_json(
            Path(plan.shot_review_path),
            fields=pet_sitcom_review_module._SHOT_REVIEW_TOP_FIELDS,
        )
        records = document.get("shots")
        mouth = document.get("mouth_timing")
        if (
            document.get("schema_version")
            != pet_sitcom_review_module.SHOT_REVIEW_SCHEMA
            or not pet_sitcom_review_module._iso(
                document.get("generated_at")
            )
            or not isinstance(records, Mapping)
            or set(records) != set(sources)
            or not isinstance(mouth, Mapping)
            or set(mouth)
            != (
                set(sources)
                & set(pet_sitcom_review_module._MOUTH_SHOTS)
            )
        ):
            raise ValueError("Shot review document is invalid.")
        qc = source_evidence.get("qc")
        qc_records = qc.get("records") if isinstance(qc, Mapping) else None
        if not isinstance(qc_records, list):
            raise ValueError("Source QC records are missing.")
        durations = {
            record["name"]: float(record["duration_seconds"])
            for record in qc_records
            if isinstance(record, Mapping)
        }
        if set(durations) != set(sources):
            raise ValueError("Source QC durations are incomplete.")
    except (
        AttributeError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ):
        return {
            "passed_count": 0,
            "owner_verified": owner_verified,
            "source_valid": True,
        }
    passed_count = 0
    for shot in plan.shots:
        source = sources[shot.shot_id]
        try:
            result = pet_sitcom_review_module._validate_shot_review_record(
                shot.shot_id,
                records[shot.shot_id],
                source,
                durations[shot.shot_id],
            )
            if shot.shot_id in pet_sitcom_review_module._MOUTH_SHOTS:
                pet_sitcom_review_module._validate_mouth_timing_record(
                    shot.shot_id,
                    mouth[shot.shot_id],
                    source,
                    durations[shot.shot_id],
                )
        except (
            AttributeError,
            KeyError,
            PetSitcomReviewError,
            TypeError,
            ValueError,
        ):
            continue
        if result.get("passed") is True:
            passed_count += 1
    return {
        "passed_count": passed_count,
        "owner_verified": owner_verified,
        "source_valid": True,
    }


def _pet_inspect_sound(plan: Any) -> dict[str, Any]:
    root = Path(plan.output_dir)
    music_source = ""
    try:
        manifest_path = root / "sound_design.json"
        document = _pet_read_strict_json(
            manifest_path,
            fields=pet_sitcom_sound_module._TOP_LEVEL_FIELDS,
        )
        config = pet_sitcom_sound_module._sound_config()
        config_hash = pet_sitcom_sound_module._json_hash(config)
        plan_hash = pet_sitcom_sound_module._json_hash(plan.to_report())
        if (
            document.get("schema_version")
            != pet_sitcom_sound_module.SOUND_DESIGN_SCHEMA
            or document.get("project_id") != plan.project_id
            or document.get("plan_sha256") != plan_hash
            or document.get("duration_seconds")
            != pet_sitcom_sound_module.FINAL_DURATION_SECONDS
            or document.get("sample_rate")
            != pet_sitcom_sound_module.SAMPLE_RATE
            or document.get("channels")
            != pet_sitcom_sound_module.CHANNELS
            or document.get("config_sha256") != config_hash
            or document.get("music_cues") != config["music_cues"]
            or document.get("dialogue_fades")
            != config["dialogue_fades"]
            or document.get("ending_button") != config["ending_button"]
            or document.get("foley") != config["foley"]
            or document.get("room_tone") != config["room_tone"]
        ):
            raise ValueError("Sound manifest is stale.")
        source_record = document.get("source")
        if (
            not isinstance(source_record, Mapping)
            or set(source_record)
            != set(pet_sitcom_sound_module._SOURCE_FIELDS)
        ):
            raise ValueError("Sound source schema is invalid.")
        source = _pet_bound_file(
            source_record.get("path"),
            source_record.get("sha256"),
            root=None,
        )
        approval_record = document.get("approval")
        if (
            not isinstance(approval_record, Mapping)
            or set(approval_record)
            != set(pet_sitcom_sound_module._MANIFEST_APPROVAL_FIELDS)
        ):
            raise ValueError("Sound approval binding is invalid.")
        approval_path = _pet_bound_file(
            approval_record.get("path"),
            approval_record.get("sha256"),
            root=None,
            expected=pet_sitcom_sound_module.music_approval_path(source),
        )
        approval = _pet_read_strict_json(
            approval_path,
            fields=pet_sitcom_sound_module._APPROVAL_FIELDS,
        )
        if (
            approval.get("schema_version")
            != pet_sitcom_sound_module.MUSIC_APPROVAL_SCHEMA
            or approval.get("source_path") != str(source)
            or approval.get("source_sha256") != source_record["sha256"]
            or approval.get("reviewed") is not True
            or approval.get("approved") is not True
            or approval.get("not_harsh") is not True
            or approval.get("not_repetitive") is not True
            or approval.get("dialogue_compatible") is not True
            or not pet_sitcom_sound_module._valid_iso_timestamp(
                approval.get("reviewed_at")
            )
            or approval_record.get("reviewed_at")
            != approval.get("reviewed_at")
        ):
            raise ValueError("Sound approval is stale.")
        music_source = str(source)
        source_duration = _pet_finite_number(
            source_record.get("duration_seconds"),
            minimum=pet_sitcom_sound_module.FINAL_DURATION_SECONDS,
        )
        source_stream_duration = _pet_finite_number(
            source_record.get("stream_duration_seconds"),
            minimum=pet_sitcom_sound_module.FINAL_DURATION_SECONDS,
        )
        source_sample_rate = source_record.get("sample_rate")
        source_channels = source_record.get("channels")
        if (
            source_duration < pet_sitcom_sound_module.FINAL_DURATION_SECONDS
            or source_stream_duration
            < pet_sitcom_sound_module.FINAL_DURATION_SECONDS
            or isinstance(source_sample_rate, bool)
            or not isinstance(source_sample_rate, int)
            or source_sample_rate
            < pet_sitcom_sound_module.MINIMUM_MUSIC_SAMPLE_RATE
            or isinstance(source_channels, bool)
            or not isinstance(source_channels, int)
            or source_channels != pet_sitcom_sound_module.CHANNELS
            or source_record.get("codec_type") != "audio"
            or not isinstance(source_record.get("codec_name"), str)
            or not source_record.get("codec_name")
            or source_record.get("channel_layout") != "stereo"
            or source_record.get("looped") is not False
        ):
            raise ValueError("Sound source media contract is invalid.")
        stems = document.get("stems")
        if (
            not isinstance(stems, Mapping)
            or set(stems) != set(pet_sitcom_sound_module._STEM_NAMES)
        ):
            raise ValueError("Sound stems are incomplete.")
        content_root = _pet_digest(
            document.get("stems_content_root_sha256")
        )
        expected_durations = pet_sitcom_sound_module._stem_durations()
        actual: dict[str, dict[str, Any]] = {}
        for name in pet_sitcom_sound_module._STEM_NAMES:
            record = stems[name]
            if (
                not isinstance(record, Mapping)
                or set(record)
                != set(pet_sitcom_sound_module._STEM_FIELDS)
            ):
                raise ValueError("Sound stem schema is invalid.")
            expected_path = pet_sitcom_sound_module._stem_path(
                plan,
                content_root,
                name,
            )
            path = _pet_bound_file(
                record.get("path"),
                record.get("sha256"),
                root=root,
                expected=expected_path,
            )
            for numeric in (
                "duration_seconds",
                "stream_duration_seconds",
                "sample_rate",
                "channels",
            ):
                _pet_finite_number(record.get(numeric), minimum=0)
            stream_duration = float(record["stream_duration_seconds"])
            if (
                record.get("source_sha256") != source_record["sha256"]
                or record.get("approval_sha256")
                != approval_record["sha256"]
                or record.get("config_sha256") != config_hash
                or record.get("duration_seconds")
                != expected_durations[name]
                or abs(stream_duration - expected_durations[name])
                > pet_sitcom_sound_module._DURATION_TOLERANCE_SECONDS
                or record.get("codec_type") != "audio"
                or record.get("codec_name") != "pcm_s16le"
                or isinstance(record.get("sample_rate"), bool)
                or not isinstance(record.get("sample_rate"), int)
                or record.get("sample_rate")
                != pet_sitcom_sound_module.SAMPLE_RATE
                or isinstance(record.get("channels"), bool)
                or not isinstance(record.get("channels"), int)
                or record.get("channels")
                != pet_sitcom_sound_module.CHANNELS
                or record.get("channel_layout") != "stereo"
            ):
                raise ValueError("Sound stem metadata is stale.")
            actual[name] = {**dict(record), "path": str(path)}
        if (
            pet_sitcom_sound_module._stems_content_root(actual)
            != content_root
        ):
            raise ValueError("Sound content root is stale.")
        binding = pet_sitcom_sound_module._binding_sha256(
            pet_sitcom_sound_module._binding_base(
                plan=plan,
                plan_sha256=plan_hash,
                source=source,
                source_sha256=source_record["sha256"],
                source_metadata=source_record,
                approval_sha256=approval_record["sha256"],
                config_sha256=config_hash,
            ),
            content_root,
        )
        if (
            document.get("binding_sha256") != binding
            or any(
                record.get("binding_sha256") != binding
                for record in stems.values()
            )
        ):
            raise ValueError("Sound binding hash is stale.")
        return {
            "approved": True,
            "manifest": document,
            "music_source": music_source,
        }
    except (
        AttributeError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ):
        return {
            "approved": False,
            "manifest": {},
            "music_source": music_source,
        }


def _pet_inspect_source_evidence(
    plan: Any,
    selections: Mapping[str, Any],
) -> dict[str, Any]:
    root = Path(plan.output_dir)
    evidence_root = pet_sitcom_review_module._evidence_root(plan)
    sources = selections.get("sources")
    if (
        selections.get("count") != len(plan.shots)
        or not isinstance(sources, Mapping)
        or set(sources) != {shot.shot_id for shot in plan.shots}
    ):
        return {"valid": False, "manifest_sha256": ""}
    try:
        manifest_path = pet_sitcom_review_module._source_manifest_path(plan)
        manifest = _pet_read_strict_json(
            manifest_path,
            fields=pet_sitcom_review_module._SOURCE_MANIFEST_FIELDS,
        )
        if (
            manifest.get("schema_version")
            != pet_sitcom_review_module.SOURCE_EVIDENCE_SCHEMA
            or manifest.get("phase") != "source"
            or not pet_sitcom_review_module._iso(
                manifest.get("generated_at")
            )
            or manifest.get("automation_limitations")
            != pet_sitcom_review_module._AUTOMATION_LIMITATIONS
        ):
            raise ValueError("Source evidence manifest is stale.")
        qc_path = evidence_root / "source_technical_qc.json"
        _pet_bound_file(
            manifest.get("source_technical_qc_path"),
            manifest.get("source_technical_qc_sha256"),
            root=root,
            expected=qc_path,
        )
        _pet_read_strict_json(
            qc_path,
            fields=pet_sitcom_review_module._QC_TOP_FIELDS,
        )
        expected_sources = {
            shot.shot_id: sources[shot.shot_id]
            for shot in plan.shots
        }
        qc = pet_sitcom_review_module._validate_qc_document(
            plan,
            qc_path,
            phase="source",
            expected=expected_sources,
        )
        records = {record["name"]: record for record in qc["records"]}
        durations = {
            name: float(record["duration_seconds"])
            for name, record in records.items()
        }
        video_durations = {
            name: float(record["video_duration_seconds"])
            for name, record in records.items()
        }
        shot_sheets = manifest.get("shot_sheets")
        if (
            not isinstance(shot_sheets, list)
            or len(shot_sheets) != len(plan.shots)
        ):
            raise ValueError("Source shot sheets are incomplete.")
        for shot, item in zip(plan.shots, shot_sheets, strict=True):
            pet_sitcom_review_module._validate_source_sequence(
                plan,
                item,
                shot.shot_id,
                shot.shot_id,
                sources[shot.shot_id],
                durations[shot.shot_id],
                video_durations[shot.shot_id],
                9,
                "3x3",
                evidence_root / "shot_sheets" / f"{shot.shot_id}.png",
            )
        for key, shot_ids, frame_count, layout, folder in (
            (
                "mouth_sequences",
                pet_sitcom_review_module._MOUTH_SHOTS,
                13,
                "4x4",
                "mouth",
            ),
            (
                "paw_sequences",
                pet_sitcom_review_module._PAW_SHOTS,
                9,
                "3x3",
                "paws",
            ),
        ):
            pet_sitcom_review_module._validate_sequence_group(
                plan,
                manifest.get(key),
                shot_ids,
                sources,
                durations,
                video_durations,
                frame_count=frame_count,
                layout=layout,
                folder=folder,
            )
        props = manifest.get("prop_sequences")
        if (
            not isinstance(props, Mapping)
            or set(props) != set(pet_sitcom_review_module._PROP_SHOTS)
        ):
            raise ValueError("Source prop evidence is incomplete.")
        for label, shot_ids in pet_sitcom_review_module._PROP_SHOTS.items():
            group = props.get(label)
            if not isinstance(group, Mapping) or set(group) != set(shot_ids):
                raise ValueError("Source prop evidence is incomplete.")
            for shot_id in shot_ids:
                pet_sitcom_review_module._validate_source_sequence(
                    plan,
                    group[shot_id],
                    shot_id,
                    label,
                    sources[shot_id],
                    durations[shot_id],
                    video_durations[shot_id],
                    9,
                    "3x3",
                    evidence_root
                    / "props"
                    / label
                    / shot_id
                    / f"{label}.png",
                )
        edges = pet_sitcom_review_module._continuity_edges(plan)
        comparisons = manifest.get("continuity_comparisons")
        if (
            not isinstance(comparisons, list)
            or len(comparisons) != len(edges)
        ):
            raise ValueError("Source continuity evidence is incomplete.")
        for edge, item in zip(edges, comparisons, strict=True):
            pet_sitcom_review_module._validate_continuity_item(
                plan,
                item,
                edge[0],
                edge[1],
                sources,
                durations,
                video_durations,
            )
        expected_review_paths = {
            "shot_reviews": str(Path(plan.shot_review_path).resolve()),
            "owner_native_audio": str(
                (
                    root / "owner_native_audio_review.json"
                ).resolve()
            ),
        }
        if manifest.get("manual_review_paths") != expected_review_paths:
            raise ValueError("Source manual review paths are stale.")
        for expected in (
            Path(plan.shot_review_path),
            root / "owner_native_audio_review.json",
        ):
            _pet_canonical_file(
                str(expected),
                root=root,
                expected=expected,
            )
        return {
            "valid": True,
            "manifest_sha256": _pet_file_sha256(manifest_path),
            "qc": qc,
            "manifest": manifest,
        }
    except (
        AttributeError,
        KeyError,
        OSError,
        PetSitcomReviewError,
        TypeError,
        ValueError,
    ):
        return {
            "valid": False,
            "manifest_sha256": "",
            "qc": {},
            "manifest": {},
        }


def _pet_inspect_incremental_evidence(
    plan: Any,
    selections: Mapping[str, Any],
) -> bool:
    sources = selections.get("sources")
    if (
        selections.get("count") != len(plan.shots)
        or not isinstance(sources, Mapping)
        or set(sources) != {shot.shot_id for shot in plan.shots}
    ):
        return False
    try:
        evidence_root = pet_sitcom_review_module._evidence_root(plan)
        for shot in plan.shots:
            shot_id = shot.shot_id
            evidence = _pet_read_strict_json(
                evidence_root / "incremental" / f"{shot_id}.json",
                fields=pet_sitcom_review_module._SHOT_EVIDENCE_FIELDS,
            )
            if (
                evidence.get("schema_version")
                != pet_sitcom_review_module.SHOT_EVIDENCE_SCHEMA
                or evidence.get("shot_id") != shot_id
                or not pet_sitcom_review_module._iso(
                    evidence.get("generated_at")
                )
                or evidence.get("manual_review_path")
                != str(Path(plan.shot_review_path).resolve())
                or evidence.get("automation_limitations")
                != pet_sitcom_review_module._AUTOMATION_LIMITATIONS
            ):
                raise ValueError("Incremental evidence is stale.")
            source = sources[shot_id]
            technical = pet_sitcom_review_module._validate_qc_record(
                plan,
                evidence.get("source_technical_qc"),
                phase="source",
                expected_name=shot_id,
                expected_item=source,
                allow_failed=True,
            )
            if technical.get("passed") is not True:
                raise ValueError("Incremental technical QC did not pass.")
            duration = float(technical["duration_seconds"])
            video_duration = float(technical["video_duration_seconds"])
            pet_sitcom_review_module._validate_source_sequence(
                plan,
                evidence.get("shot_sheet"),
                shot_id,
                shot_id,
                source,
                duration,
                video_duration,
                9,
                "3x3",
                evidence_root / "shot_sheets" / f"{shot_id}.png",
            )
            pet_sitcom_review_module._validate_optional_incremental_sequence(
                plan,
                evidence.get("mouth_sequence"),
                shot_id=shot_id,
                source=source,
                duration=duration,
                video_duration=video_duration,
                expected_shots=pet_sitcom_review_module._MOUTH_SHOTS,
                frame_count=13,
                layout="4x4",
                folder="mouth",
            )
            pet_sitcom_review_module._validate_optional_incremental_sequence(
                plan,
                evidence.get("paw_sequence"),
                shot_id=shot_id,
                source=source,
                duration=duration,
                video_duration=video_duration,
                expected_shots=pet_sitcom_review_module._PAW_SHOTS,
                frame_count=9,
                layout="3x3",
                folder="paws",
            )
            pet_sitcom_review_module._validate_incremental_props(
                plan,
                evidence.get("prop_sequences"),
                shot_id,
                source,
                duration,
                video_duration,
            )
            pet_sitcom_review_module._validate_incremental_continuity(
                plan,
                evidence.get("continuity_comparison"),
                shot,
                sources,
                duration,
                video_duration,
            )
        return True
    except (
        AttributeError,
        KeyError,
        OSError,
        PetSitcomReviewError,
        TypeError,
        ValueError,
    ):
        return False


def _pet_inspect_final_evidence(
    plan: Any,
    selections: Mapping[str, Any],
) -> dict[str, Any]:
    source = _pet_inspect_source_evidence(plan, selections)
    if source.get("valid") is not True:
        return {"valid": False}
    root = Path(plan.output_dir)
    evidence_root = pet_sitcom_review_module._evidence_root(plan)
    try:
        manifest_path = pet_sitcom_review_module._final_manifest_path(plan)
        manifest = _pet_read_strict_json(
            manifest_path,
            fields=pet_sitcom_review_module._FINAL_MANIFEST_FIELDS,
        )
        if (
            manifest.get("schema_version")
            != pet_sitcom_review_module.FINAL_EVIDENCE_SCHEMA
            or manifest.get("phase") != "final"
            or not pet_sitcom_review_module._iso(
                manifest.get("generated_at")
            )
            or manifest.get("automation_limitations")
            != pet_sitcom_review_module._AUTOMATION_LIMITATIONS
            or manifest.get("source_manifest_sha256")
            != source["manifest_sha256"]
        ):
            raise ValueError("Final evidence manifest is stale.")
        outputs: dict[str, dict[str, Any]] = {}
        for name, expected in (
            ("clean", Path(plan.clean_output)),
            ("release", Path(plan.release_output)),
        ):
            path = _pet_canonical_file(
                str(expected),
                root=root,
                expected=expected,
            )
            outputs[name] = {
                "path": path,
                "sha256": _pet_file_sha256(path),
            }
        qc_path = evidence_root / "final_technical_qc.json"
        _pet_bound_file(
            manifest.get("final_technical_qc_path"),
            manifest.get("final_technical_qc_sha256"),
            root=root,
            expected=qc_path,
        )
        _pet_read_strict_json(
            qc_path,
            fields=pet_sitcom_review_module._QC_TOP_FIELDS,
        )
        qc = pet_sitcom_review_module._validate_qc_document(
            plan,
            qc_path,
            phase="final",
            expected=outputs,
        )
        records = {record["name"]: record for record in qc["records"]}
        pet_sitcom_review_module._validate_final_sequence(
            plan,
            manifest.get("whole_cut_sheet"),
            "whole_cut",
            Path(plan.release_output),
            records["release"],
            16,
            "4x4",
            evidence_root / "final" / "whole_cut.png",
        )
        checks = manifest.get("final_checks")
        if (
            not isinstance(checks, Mapping)
            or set(checks) != {"clean", "release"}
        ):
            raise ValueError("Final evidence checks are incomplete.")
        for name, path in (
            ("clean", Path(plan.clean_output)),
            ("release", Path(plan.release_output)),
        ):
            pet_sitcom_review_module._validate_final_sequence(
                plan,
                checks[name],
                f"{name}_start_cut_end",
                path,
                records[name],
                3,
                "3x1",
                evidence_root
                / "final"
                / f"{name}_start_cut_end.png",
            )
        return {"valid": True}
    except (
        AttributeError,
        KeyError,
        OSError,
        PetSitcomReviewError,
        TypeError,
        ValueError,
    ):
        return {"valid": False}


def _pet_bound_drive_path(
    plan: Any,
    record: Mapping[str, Any],
    *,
    path_field: str,
    hash_field: str,
) -> Path:
    path = Path(str(record.get(path_field) or ""))
    expected_hash = str(record.get(hash_field) or "")
    if not expected_hash:
        raise ValueError("Drive audio hash is missing.")
    try:
        path.resolve().relative_to(Path(plan.output_dir).resolve())
    except (OSError, ValueError) as exc:
        raise ValueError("Drive audio path is outside the project.") from exc
    if (
        _pet_path_has_symlink(path)
        or not path.is_file()
        or _pet_file_sha256(path) != expected_hash
    ):
        raise ValueError("Drive audio is missing, stale, or unsafe.")
    return path


def _pet_selection_drive_path(plan: Any, shot: Any) -> Path | None:
    document = _pet_read_json_object(Path(plan.selection_path))
    selections = document.get("shots")
    if not isinstance(selections, Mapping):
        raise ValueError("Selection document is missing.")
    entry = selections.get(shot.shot_id)
    if not isinstance(entry, Mapping) or entry.get("status") != "selected":
        raise ValueError("Selection entry is missing.")
    candidate_number = entry.get("candidate_number")
    if type(candidate_number) is not int:
        raise ValueError("Selection candidate is invalid.")
    candidate = Path(shot.candidate_dir) / f"candidate_{candidate_number:03d}.mp4"
    provenance = _pet_read_json_object(candidate.with_suffix(".provenance.json"))
    audio_hash = str(entry.get("reference_audio_sha256") or "")
    if not audio_hash:
        return None
    path = _pet_bound_drive_path(
        plan,
        {
            "path": provenance.get("reference_audio_path"),
            "sha256": audio_hash,
        },
        path_field="path",
        hash_field="sha256",
    )
    expected = (
        Path(plan.output_dir)
        / "audio"
        / "drive"
        / f"{shot.shot_id}_drive.wav"
    )
    if path != expected:
        raise ValueError("Selection drive audio path is not canonical.")
    return path


def _pet_selection_side_effect_preflight(plan: Any, shot: Any) -> bool:
    try:
        _pet_selection_drive_path(plan, shot)
    except (OSError, TypeError, ValueError):
        return False
    return True


@contextmanager
def _pet_read_only_selection_drives(
    plan: Any,
    shots: list[Any],
):
    paths: dict[str, Path] = {}
    for shot in shots:
        path = _pet_selection_drive_path(plan, shot)
        if path is not None:
            paths[shot.shot_id] = path
    original = pet_sitcom_generation_module.build_pet_drive_audio

    def read_only_drive(_plan: Any, shot_id: str, **_kwargs: Any) -> Path:
        expected = paths.get(shot_id)
        if expected is None:
            raise PetSitcomAudioFirstError(
                f"{shot_id} has no current read-only drive WAV."
            )
        path = original(
            _plan,
            shot_id,
            command_runner=_pet_deny_media_command,
        )
        if path != expected:
            raise PetSitcomAudioFirstError(
                f"{shot_id} drive WAV path is not canonical."
            )
        return path

    pet_sitcom_generation_module.build_pet_drive_audio = read_only_drive
    try:
        yield
    finally:
        pet_sitcom_generation_module.build_pet_drive_audio = original


def _pet_require_approved_probe_read_only(plan: Any) -> dict[str, Any]:
    if (
        getattr(require_approved_pet_audio_probe, "__module__", "")
        != pet_sitcom_audio_probe_module.__name__
    ):
        return require_approved_pet_audio_probe(plan)
    report = _pet_read_json_object(Path(plan.audio_probe_path))
    drive = _pet_bound_drive_path(
        plan,
        report,
        path_field="drive_audio_path",
        hash_field="drive_audio_sha256",
    )
    expected = (
        Path(plan.output_dir) / "audio" / "drive" / "shot_04_drive.wav"
    )
    if drive != expected:
        raise ValueError("Probe drive audio path is not canonical.")
    original = pet_sitcom_audio_probe_module.build_pet_drive_audio

    def read_only_drive(_plan: Any, shot_id: str, **_kwargs: Any) -> Path:
        path = original(
            _plan,
            shot_id,
            command_runner=_pet_deny_media_command,
        )
        if path != drive:
            raise PetSitcomAudioFirstError(
                "Probe drive WAV path is not canonical."
            )
        return path

    pet_sitcom_audio_probe_module.build_pet_drive_audio = read_only_drive
    try:
        return require_approved_pet_audio_probe(plan)
    finally:
        pet_sitcom_audio_probe_module.build_pet_drive_audio = original


def _pet_deny_media_command(*_args: Any, **_kwargs: Any) -> Any:
    raise PetSitcomAudioFirstError(
        "Status cannot rebuild stale drive audio."
    )


def _pet_current_selection_and_review_counts(plan: Any) -> tuple[int, int]:
    selected_count = 0
    passed_count = 0
    prefix: list[Any] = []
    for shot in plan.shots:
        if not _pet_selection_side_effect_preflight(plan, shot):
            break
        prefix.append(shot)
        try:
            with _pet_read_only_selection_drives(plan, prefix):
                pet_sitcom_review_module._selected_source_chain(plan, shot)
        except Exception:
            break
        selected_count += 1
        try:
            with _pet_read_only_selection_drives(plan, prefix):
                review = validate_pet_shot_review(plan, shot.shot_id)
        except Exception:
            continue
        if review.get("passed") is True:
            passed_count += 1
    return selected_count, passed_count


def _pet_status(plan: Any) -> dict[str, Any]:
    plan_ready = _pet_plan_is_current(plan)
    anchors = _pet_inspect_anchors(plan)
    audio = _pet_inspect_audio(plan)
    probe = _pet_inspect_probe(plan, audio)
    selections = _pet_inspect_selections(plan, audio)
    reviews = _pet_inspect_reviews(plan, selections)
    sound = _pet_inspect_sound(plan)
    final = {"valid": False}
    review_ready = (
        reviews.get("source_valid") is True
        and reviews.get("passed_count") == len(plan.shots)
        and reviews.get("owner_verified") is True
    )
    if (
        selections.get("count") == len(plan.shots)
        and review_ready
        and sound.get("approved") is True
    ):
        final = _pet_inspect_final_evidence(plan, selections)
    anchors_approved = anchors.get("approved") is True
    audio_ready = audio.get("ready") is True
    audio_probe_approved = probe.get("approved") is True
    selected_count = int(selections.get("count", 0))
    review_count = int(reviews.get("passed_count", 0))
    sound_design_approved = sound.get("approved") is True
    composition_ready = (
        selected_count == len(plan.shots)
        and review_ready
        and sound_design_approved
        and final.get("valid") is True
    )
    checks = (
        ("plan", plan_ready),
        ("anchors", anchors_approved),
        ("audio", audio_ready),
        ("audio-probe", audio_probe_approved),
        ("shots", selected_count == len(plan.shots)),
        ("review", review_ready),
        ("compose", composition_ready),
    )
    if probe.get("state") in {"unsupported", "inconclusive"}:
        next_stage = "blocked"
    else:
        next_stage = next(
            (stage for stage, complete in checks if not complete),
            "status",
        )
    return {
        "plan_ready": plan_ready,
        "anchors_approved": anchors_approved,
        "audio_ready": audio_ready,
        "audio_probe_approved": audio_probe_approved,
        "selected_shot_count": selected_count,
        "shot_review_passed_count": review_count,
        "sound_design_approved": sound_design_approved,
        "composition_ready": composition_ready,
        "next_stage": next_stage,
    }


def _pet_status_blockers(plan: Any) -> list[str]:
    audio = _pet_inspect_audio(plan)
    probe = _pet_inspect_probe(plan, audio)
    if probe.get("state") in {"unsupported", "inconclusive"}:
        return [str(probe.get("reason") or "Audio probe is terminally blocked.")]
    return []


def _pet_music_source(raw: str) -> Path:
    source = Path(raw)
    if not source.is_absolute():
        raise PetSoundError(
            "--music-source must be an absolute canonical local path."
        )
    if _pet_path_has_symlink(source):
        raise PetSoundError("--music-source may not contain symlinks.")
    try:
        canonical = source.resolve(strict=True)
    except OSError as exc:
        raise PetSoundError("--music-source must be an existing local file.") from exc
    if source != canonical or not canonical.is_file():
        raise PetSoundError(
            "--music-source must be an absolute canonical local file."
        )
    return canonical


def _refresh_pet_incremental_evidence(plan: Any) -> tuple[str, ...]:
    refreshed: list[str] = []
    for shot in plan.shots:
        try:
            validate_pet_shot_review(plan, shot.shot_id)
        except PetSitcomReviewError:
            build_pet_shot_evidence(plan, shot.shot_id)
            validate_pet_shot_review(plan, shot.shot_id)
            refreshed.append(shot.shot_id)
    return tuple(refreshed)


def pet_sitcom_command(args: argparse.Namespace) -> int:
    """Expose the fixed audio-first workflow through its strong stage gates."""
    profile = None
    tts_config = None
    plan = None
    targets: list[str] = []
    try:
        config = load_config(args.config)
        plan = build_pet_sitcom_plan(config, args.output_dir or None)
        _pet_validate_candidate_request(args)
        targets = _pet_stage_targets(plan, args)
        base_artifacts = {
            "plan": str(plan.plan_path),
            "anchor_report": str(
                plan.output_dir / "anchor_generation_report.json"
            ),
            "anchor_review": str(_pet_anchor_review_path(plan)),
            "audio_manifest": str(plan.audio_manifest_path),
            "audio_probe": str(plan.audio_probe_path),
            "audio_probe_review": str(plan.audio_probe_review_path),
            "selections": str(plan.selection_path),
            "source_evidence": str(plan.output_dir / "evidence" / "source_evidence.json"),
            "owner_native_audio_review": str(plan.output_dir / "owner_native_audio_review.json"),
            "shot_reviews": str(plan.shot_review_path),
            "dialogue_timings": str(plan.output_dir / "dialogue_timings.json"),
            "sound_design": str(plan.output_dir / "sound_design.json"),
            "clean_output": str(plan.clean_output),
            "release_output": str(plan.release_output),
            "review_markdown": str(plan.review_markdown_path),
        }
        if args.stage == "status":
            current = _pet_status(plan)
            payload = _pet_output(
                plan,
                args,
                targets=targets,
                blocked_reasons=_pet_status_blockers(plan),
                artifacts=base_artifacts,
                next_stage=str(current["next_stage"]),
                status=current,
                reuse_sound_design=bool(current["sound_design_approved"]),
            )
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        if args.stage == "plan":
            write_pet_sitcom_plan(plan)
            payload = _pet_output(
                plan,
                args,
                planned_count=23,
                targets=targets,
                artifacts=base_artifacts,
                next_stage="anchors",
            )
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        if args.stage == "anchors" and not args.enable_live:
            review_path = _pet_anchor_review_path(plan)
            review = _pet_read_json_object(review_path)
            if review.get("completed") is True:
                approval = approve_pet_anchors(plan)
                payload = _pet_output(
                    plan,
                    args,
                    approved=approval.get("approved") is True,
                    planned_count=len(targets),
                    targets=targets,
                    artifacts=base_artifacts,
                    next_stage="audio",
                )
                print(json.dumps(payload, ensure_ascii=False))
                return 0
            payload = _pet_output(
                plan,
                args,
                planned_count=len(targets),
                targets=targets,
                blocked_reasons=["Live provider execution is disabled; this is a dry-run plan."],
                artifacts=base_artifacts,
                next_stage="anchors",
            )
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        if args.stage == "audio" and not args.enable_live:
            if _pet_inspect_anchors(plan).get("approved") is not True:
                payload = _pet_output(
                    plan,
                    args,
                    success=False,
                    planned_count=8,
                    targets=targets,
                    blocked_reasons=[
                        "Approved current anchor evidence is required before audio."
                    ],
                    artifacts=base_artifacts,
                    next_stage="anchors",
                )
                print(json.dumps(payload, ensure_ascii=False))
                return 1
            audio = _pet_inspect_audio(plan)
            if audio.get("ready") is True:
                payload = _pet_output(
                    plan,
                    args,
                    success=True,
                    planned_count=8,
                    completed_count=8,
                    reused_count=8,
                    targets=targets,
                    artifacts=base_artifacts,
                    next_stage="audio-probe",
                )
            else:
                payload = _pet_output(
                    plan,
                    args,
                    planned_count=8,
                    targets=targets,
                    blocked_reasons=[
                        "Live TTS execution is disabled; this is a dry-run plan."
                    ],
                    artifacts=base_artifacts,
                    next_stage="audio",
                )
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        if args.stage == "audio-probe" and not args.enable_live:
            if _pet_inspect_anchors(plan).get("approved") is not True:
                payload = _pet_output(
                    plan,
                    args,
                    success=False,
                    planned_count=1,
                    targets=targets,
                    blocked_reasons=[
                        "Approved current anchor evidence is required before audio probe."
                    ],
                    artifacts=base_artifacts,
                    next_stage="anchors",
                )
                print(json.dumps(payload, ensure_ascii=False))
                return 1
            audio = _pet_inspect_audio(plan)
            probe = _pet_inspect_probe(plan, audio)
            approved = probe.get("approved") is True
            if audio.get("ready") is not True:
                next_stage = "audio"
                reasons = [str(audio.get("reason") or "Current audio is missing.")]
            elif probe.get("state") in {"unsupported", "inconclusive"}:
                next_stage = "blocked"
                reasons = [str(probe["reason"])]
            else:
                next_stage = "shots" if approved else "audio-probe"
                reasons = [] if approved else [str(probe["reason"])]
            payload = _pet_output(
                plan,
                args,
                approved=approved,
                planned_count=1,
                completed_count=int(approved),
                targets=targets,
                blocked_reasons=reasons,
                artifacts=base_artifacts,
                next_stage=next_stage,
            )
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        if args.stage == "shots":
            audio = _pet_inspect_audio(plan)
            probe = _pet_inspect_probe(plan, audio)
            if probe.get("approved") is not True:
                raise PetSitcomGenerationError(
                    str(
                        probe.get("reason")
                        or "Approved current audio-drive probe is required."
                    )
                )
            if not args.enable_live:
                payload = _pet_output(
                    plan,
                    args,
                    planned_count=len(targets),
                    targets=targets,
                    blocked_reasons=[
                        "Live provider execution is disabled; this is a dry-run plan."
                    ],
                    artifacts=base_artifacts,
                    next_stage="shots",
                )
                print(json.dumps(payload, ensure_ascii=False))
                return 0
            require_approved_pet_audio_probe(plan)
        if args.stage == "anchors":
            profile = resolve_provider_profile(config)
            blocker = _pet_provider_error(profile, "image")
            if blocker:
                raise PetSitcomGenerationError(blocker)
            report = generate_pet_sitcom_anchors(
                plan,
                image_client=_pet_image_client(profile, args),
                allow_network=True,
                anchor_names=tuple(args.anchor) or None,
            )
            review_path = _pet_anchor_review_path(plan)
            approval: dict[str, Any] = {}
            if (
                report.get("success")
                and _pet_read_json_object(review_path).get("completed") is True
            ):
                approval = approve_pet_anchors(plan)
            payload = _pet_output(
                plan, args, executed=bool(report.get("executed")), success=bool(report.get("success")),
                approved=approval.get("approved") is True,
                planned_count=int(report.get("planned_count", len(targets))),
                completed_count=int(report.get("completed_count", 0)),
                reused_count=int(report.get("reused_count", 0)),
                failed_count=len(report.get("errors", []) or []), targets=targets,
                blocked_reasons=list(report.get("blocked_reasons", []) or []),
                artifacts=base_artifacts,
                next_stage=(
                    "audio" if approval.get("approved") is True else "anchors"
                ),
                secrets=_pet_secrets(profile),
            )
            print(json.dumps(payload, ensure_ascii=False))
            return 0 if payload["success"] else 1
        if args.stage == "audio":
            if _pet_inspect_anchors(plan).get("approved") is not True:
                raise PetSitcomGenerationError(
                    "Approved current anchor evidence is required before audio."
                )
            _require_approved_anchors(plan)
            tts_config = resolve_doubao_tts_config(config)
            if tts_config is None:
                raise PetSitcomAudioFirstError(
                    "Ready Doubao Seed-TTS configuration is required."
                )
            report = generate_pet_speech_assets(
                plan,
                tts_client=DoubaoTTSClient(tts_config),
                allow_network=True,
            )
            payload = _pet_output(
                plan,
                args,
                executed=bool(report.get("executed")),
                success=bool(report.get("success")),
                planned_count=int(report.get("planned_count", 8)),
                completed_count=int(report.get("completed_count", 0)),
                reused_count=int(report.get("reused_count", 0)),
                failed_count=len(report.get("errors", []) or []),
                targets=targets,
                blocked_reasons=list(report.get("blocked_reasons", []) or []),
                artifacts=base_artifacts,
                next_stage=(
                    "audio-probe" if report.get("success") else "audio"
                ),
                secrets=_pet_secrets(tts_config=tts_config),
            )
            print(json.dumps(payload, ensure_ascii=False))
            return 0 if payload["success"] else 1
        if args.stage == "audio-probe":
            if _pet_inspect_anchors(plan).get("approved") is not True:
                raise PetSitcomGenerationError(
                    "Approved current anchor evidence is required before audio probe."
                )
            audio = _pet_inspect_audio(plan)
            if audio.get("ready") is not True:
                raise PetSitcomAudioFirstError(str(audio.get("reason")))
            probe = _pet_inspect_probe(plan, audio)
            if probe.get("state") in {"unsupported", "inconclusive"}:
                raise PetSitcomGenerationError(str(probe["reason"]))
            if probe.get("state") == "pending":
                raise PetSitcomGenerationError(str(probe["reason"]))
            if probe.get("state") == "approved":
                payload = _pet_output(
                    plan,
                    args,
                    success=True,
                    approved=True,
                    planned_count=1,
                    completed_count=1,
                    reused_count=1,
                    targets=targets,
                    artifacts=base_artifacts,
                    next_stage="shots",
                )
                print(json.dumps(payload, ensure_ascii=False))
                return 0
            _require_approved_anchors(plan)
            load_pet_speech_assets(plan)
            profile = resolve_provider_profile(config)
            blocker = _pet_provider_error(profile, "video")
            if blocker:
                raise PetSitcomGenerationError(blocker)
            report = run_pet_audio_drive_probe(
                plan,
                video_client=_pet_video_client(profile, args),
                allow_network=True,
            )
            try:
                require_approved_pet_audio_probe(plan)
                approved = True
            except PetSitcomGenerationError:
                approved = False
            payload = _pet_output(
                plan,
                args,
                executed=bool(report.get("executed")),
                success=bool(report.get("success")),
                approved=approved,
                planned_count=int(report.get("planned_count", 1)),
                completed_count=1 if report.get("success") else 0,
                targets=targets,
                blocked_reasons=list(report.get("blocked_reasons", []) or []),
                artifacts=base_artifacts,
                next_stage="shots" if approved else "audio-probe",
                secrets=_pet_secrets(profile),
            )
            print(json.dumps(payload, ensure_ascii=False))
            return 0 if payload["success"] else 1
        if args.stage == "shots":
            if args.candidate in {2, 3, 4, 5}:
                _require_failed_previous_pet_shot_review(
                    plan, targets[0], args.candidate, args.retry_reason
                )
            profile = resolve_provider_profile(config)
            blocker = _pet_provider_error(profile, "video")
            if blocker:
                raise PetSitcomGenerationError(blocker)
            reports: list[dict[str, Any]] = []
            for shot_id in targets:
                report = generate_pet_sitcom_shots(
                    plan, video_client=_pet_video_client(profile, args), allow_network=True,
                    shot_id=shot_id, candidate_number=args.candidate,
                    retry_reason=args.retry_reason or "",
                )
                reports.append(report)
                if not report.get("success"):
                    break
                for entry in report.get("shots", []) or []:
                    selected = str(entry.get("shot_id") or "")
                    if not selected:
                        continue
                    if args.candidate in {2, 3, 4, 5}:
                        select_pet_shot_candidate(
                            plan, selected, args.candidate
                        )
                    build_pet_shot_evidence(plan, selected)
            succeeded = bool(reports) and all(item.get("success") for item in reports)
            payload = _pet_output(
                plan, args, executed=any(item.get("executed") for item in reports),
                success=succeeded, planned_count=sum(int(item.get("planned_count", 0)) for item in reports),
                completed_count=sum(int(item.get("completed_count", 0)) for item in reports),
                reused_count=sum(int(item.get("reused_count", 0)) for item in reports),
                failed_count=sum(len(item.get("errors", []) or []) for item in reports), targets=targets,
                blocked_reasons=[reason for item in reports for reason in item.get("blocked_reasons", []) or []],
                artifacts=base_artifacts,
                next_stage="review" if succeeded else "shots",
                secrets=_pet_secrets(profile),
            )
            print(json.dumps(payload, ensure_ascii=False))
            return 0 if payload["success"] else 1
        if args.stage == "review":
            audio = _pet_inspect_audio(plan)
            selections = _pet_inspect_selections(plan, audio)
            if selections.get("count") != len(plan.shots):
                raise PetSitcomReviewError(
                    "Review requires ten current hash-bound shot selections."
                )
            build_source_evidence(plan)
            refreshed = _refresh_pet_incremental_evidence(plan)
            if refreshed:
                build_source_evidence(plan)
                if _refresh_pet_incremental_evidence(plan):
                    raise PetSitcomReviewError(
                        "Incremental and full source evidence did not converge."
                    )
            validate_source_evidence(plan)
            shot_reviews = validate_pet_shot_reviews(plan)
            validate_owner_native_audio_review(plan)
            if not shot_reviews.get("passed"):
                raise PetSitcomReviewError("Current manual shot reviews are not all passing.")
            payload = _pet_output(
                plan, args, executed=True, planned_count=len(plan.shots),
                completed_count=len(plan.shots), targets=targets,
                artifacts=base_artifacts, next_stage="compose",
            )
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        if args.stage == "compose":
            audio = _pet_inspect_audio(plan)
            selections = _pet_inspect_selections(plan, audio)
            reviews = _pet_inspect_reviews(plan, selections)
            if selections.get("count") != len(plan.shots):
                raise PetSitcomReviewError(
                    "Compose requires ten current hash-bound shot selections."
                )
            if (
                reviews.get("passed_count") != len(plan.shots)
                or reviews.get("owner_verified") is not True
            ):
                raise PetSitcomReviewError(
                    "Compose requires all current manual reviews to pass."
                )
            validate_source_evidence(plan)
            shot_reviews = validate_pet_shot_reviews(plan)
            if not shot_reviews.get("passed"):
                raise PetSitcomReviewError("Current manual shot reviews are not all passing.")
            validate_owner_native_audio_review(plan)
            if args.music_source:
                prepare_pet_sound_design(
                    plan,
                    music_source=_pet_music_source(args.music_source),
                )
            else:
                if _pet_inspect_sound(plan).get("approved") is not True:
                    raise PetSoundError(
                        "Current hash-bound sound design is missing or stale."
                    )
                load_pet_sound_design(plan)
            composition = compose_pet_sitcom(plan)
            build_final_evidence(plan)
            validate_final_evidence(plan)
            markdown = write_pet_sitcom_review_markdown(plan)
            payload = _pet_output(
                plan, args, executed=True, composed=True, planned_count=2, completed_count=2,
                targets=targets, artifacts={**base_artifacts, "review_markdown": str(markdown), "composition": composition},
                next_stage="status",
            )
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        raise ValueError(f"Unsupported pet sitcom stage: {args.stage}")
    except (
        GatewayImageError,
        GatewayVideoError,
        GatewayVideoBatchError,
        PetSitcomAudioFirstError,
        PetSitcomGenerationError,
        PetSitcomComposeError,
        PetSitcomReviewError,
        PetSoundError,
        ValueError,
        OSError,
        RuntimeError,
    ) as exc:
        if plan is None:
            safe = _pet_sanitize(str(exc), _pet_secrets(profile, tts_config))
            stage = str(getattr(args, "stage", "") or "plan")
            print(
                json.dumps(
                    {
                        "stage": stage,
                        "executed": False,
                        "success": False,
                        "blocked_reasons": [safe],
                        "artifacts": {},
                        "next_command": shlex.join(
                            [
                                "python",
                                "factory_cli.py",
                                "--config",
                                str(args.config),
                                "pet-sitcom",
                                "--stage",
                                stage,
                            ]
                        ),
                        "error": safe,
                    },
                    ensure_ascii=False,
                )
            )
            return 1
        safe = _pet_sanitize(
            str(exc),
            _pet_secrets(profile, tts_config),
        )
        current = _pet_status(plan)
        status_blockers = _pet_status_blockers(plan)
        reasons = [safe]
        reasons.extend(
            reason for reason in status_blockers if reason not in reasons
        )
        payload = _pet_output(
            plan,
            args,
            success=False,
            planned_count=len(targets),
            targets=targets,
            blocked_reasons=reasons,
            error=safe,
            artifacts={"plan": str(plan.plan_path)},
            next_stage=str(current["next_stage"]),
            reuse_sound_design=bool(current["sound_design_approved"]),
            secrets=_pet_secrets(profile, tts_config),
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="StoryMotion Studio AI narrative video pipeline"
    )
    parser.add_argument("--config", default="config/factory.config.json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    refresh_preview_parser = subparsers.add_parser(
        "refresh-preview",
        help="Rebuild the voiced OpenMontage preview from generated clips and card fallbacks",
        description="Rebuild the voiced OpenMontage preview from generated clips and card fallbacks",
    )
    refresh_preview_parser.add_argument("--project", default="sample_episode")
    refresh_preview_parser.set_defaults(func=refresh_preview_command)

    template_parser = subparsers.add_parser("character-assets-template", help="Create a fillable character reference image manifest from a novel")
    template_parser.add_argument("--input", required=True)
    template_parser.add_argument("--project", required=True)
    template_parser.add_argument("--title", default="小说漫剧样片")
    template_parser.add_argument("--shots", type=int, default=8)
    template_parser.add_argument("--output", default="")
    template_parser.set_defaults(func=character_assets_template_command)

    brief_parser = subparsers.add_parser("character-brief", help="Create AI image prompts and asset targets for episode characters")
    brief_parser.add_argument("--input", required=True)
    brief_parser.add_argument("--project", required=True)
    brief_parser.add_argument("--title", default="小说漫剧样片")
    brief_parser.add_argument("--shots", type=int, default=8)
    brief_parser.add_argument("--output", default="")
    brief_parser.set_defaults(func=character_brief_command)

    assets_from_brief_parser = subparsers.add_parser(
        "character-assets-from-brief",
        help="Export a plan-ready character asset manifest from a character generation brief",
    )
    assets_from_brief_parser.add_argument("--brief", required=True)
    assets_from_brief_parser.add_argument("--output", default="")
    assets_from_brief_parser.add_argument("--require-files", action="store_true")
    assets_from_brief_parser.set_defaults(func=character_assets_from_brief_command)

    reviewed_template_parser = subparsers.add_parser(
        "character-assets-reviewed-template",
        help="Create a fillable reviewed role image source manifest from a character generation brief",
    )
    reviewed_template_parser.add_argument("--brief", required=True)
    reviewed_template_parser.add_argument("--output", default="")
    reviewed_template_parser.set_defaults(func=character_assets_reviewed_template_command)

    reviewed_from_dir_parser = subparsers.add_parser(
        "character-assets-reviewed-from-dir",
        help="Create reviewed_role_images.json by matching brief characters to image files in a directory",
    )
    reviewed_from_dir_parser.add_argument("--brief", required=True)
    reviewed_from_dir_parser.add_argument("--image-dir", default="")
    reviewed_from_dir_parser.add_argument("--output", default="")
    reviewed_from_dir_parser.set_defaults(func=character_assets_reviewed_from_dir_command)

    reviewed_intake_parser = subparsers.add_parser(
        "character-assets-reviewed-intake",
        help="Write a machine-readable report for reviewed role images in a drop folder",
    )
    reviewed_intake_parser.add_argument("--brief", required=True)
    reviewed_intake_parser.add_argument("--image-dir", default="")
    reviewed_intake_parser.add_argument("--output", default="")
    reviewed_intake_parser.add_argument("--manifest-output", default="")
    reviewed_intake_parser.add_argument("--confirmed-output", default="")
    reviewed_intake_parser.set_defaults(func=character_assets_reviewed_intake_command)

    assets_confirm_source_parser = subparsers.add_parser(
        "character-assets-confirm-source",
        help="Stamp reviewed AI character references as production-ready source assets",
    )
    assets_confirm_source_parser.add_argument("--manifest", required=True)
    assets_confirm_source_parser.add_argument("--output", default="")
    assets_confirm_source_parser.add_argument("--asset-source", default="user_generated_ai")
    assets_confirm_source_parser.add_argument("--skip-file-check", action="store_true")
    assets_confirm_source_parser.set_defaults(func=character_assets_confirm_source_command)

    assets_install_references_parser = subparsers.add_parser(
        "character-assets-install-references",
        help="Copy reviewed AI role images into brief target paths and write a confirmed manifest",
    )
    assets_install_references_parser.add_argument("--brief", required=True)
    assets_install_references_parser.add_argument("--source-manifest", required=True)
    assets_install_references_parser.add_argument("--output", default="")
    assets_install_references_parser.add_argument("--asset-source", default="user_generated_ai")
    assets_install_references_parser.add_argument("--overwrite", action="store_true")
    assets_install_references_parser.set_defaults(func=character_assets_install_references_command)

    assets_status_parser = subparsers.add_parser(
        "character-assets-status",
        help="Report per-character reference image readiness from a character generation brief",
    )
    assets_status_parser.add_argument("--brief", required=True)
    assets_status_parser.add_argument("--output", default="")
    assets_status_parser.add_argument("--asset-root", default="")
    assets_status_parser.set_defaults(func=character_assets_status_command)

    provider_parser = subparsers.add_parser(
        "provider-report",
        help="Report selected text, image, video, and audio providers",
    )
    provider_parser.add_argument("--output", default="")
    provider_parser.set_defaults(func=provider_report_command)

    gateway_text_parser = subparsers.add_parser(
        "gateway-text-smoke",
        help="Run a guarded OpenAI-compatible gateway text smoke",
    )
    gateway_text_parser.add_argument(
        "--prompt",
        default='Return a JSON object with the single field "ok" set to true.',
    )
    gateway_text_parser.add_argument("--output", default="")
    gateway_text_parser.add_argument("--timeout", type=float, default=60.0)
    gateway_text_parser.add_argument("--enable-live", action="store_true")
    gateway_text_parser.set_defaults(func=gateway_text_smoke_command)

    gateway_image_parser = subparsers.add_parser(
        "gateway-image",
        help="Run guarded gateway text-to-image generation",
    )
    gateway_image_parser.add_argument("--prompt", required=True)
    gateway_image_parser.add_argument("--output", required=True)
    gateway_image_parser.add_argument("--report-output", default="")
    gateway_image_parser.add_argument("--size", default="1024x1024")
    gateway_image_parser.add_argument("--timeout", type=float, default=120.0)
    gateway_image_parser.add_argument("--enable-live", action="store_true")
    gateway_image_parser.set_defaults(func=gateway_image_command)

    gateway_video_parser = subparsers.add_parser(
        "gateway-video-probe",
        help="Submit one potentially billable video task and inspect only its response contract",
    )
    gateway_video_parser.add_argument("--project", default="sample_episode")
    gateway_video_parser.add_argument("--model", default="")
    gateway_video_parser.add_argument("--prompt", default="A slow camera pan across an empty train station.")
    gateway_video_parser.add_argument("--image", action="append", default=[])
    gateway_video_parser.add_argument("--duration", type=int, default=5)
    gateway_video_parser.add_argument("--ratio", default="9:16")
    gateway_video_parser.add_argument("--resolution", default="720p")
    gateway_video_parser.add_argument("--generate-audio", action="store_true")
    gateway_video_parser.add_argument("--output", default="")
    gateway_video_parser.add_argument("--timeout", type=float, default=60.0)
    gateway_video_parser.add_argument("--submit-timeout", type=float, default=300.0)
    gateway_video_parser.add_argument("--enable-live", action="store_true")
    gateway_video_parser.set_defaults(func=gateway_video_probe_command)

    gateway_video_generate_parser = subparsers.add_parser(
        "gateway-video-generate",
        aliases=["video-generate"],
        help="Generate, poll, and download one configured cloud video clip",
    )
    gateway_video_generate_parser.add_argument("--prompt", required=True)
    gateway_video_generate_parser.add_argument("--model", default="")
    gateway_video_generate_parser.add_argument("--output", required=True)
    gateway_video_generate_parser.add_argument("--report-output", default="")
    gateway_video_generate_parser.add_argument("--image", action="append", default=[])
    gateway_video_generate_parser.add_argument("--audio", default="")
    gateway_video_generate_parser.add_argument("--duration", type=int, default=5)
    gateway_video_generate_parser.add_argument("--ratio", default="9:16")
    gateway_video_generate_parser.add_argument("--resolution", default="")
    gateway_video_generate_parser.add_argument("--generate-audio", action="store_true")
    gateway_video_generate_parser.add_argument("--overwrite", action="store_true")
    gateway_video_generate_parser.add_argument("--timeout", type=float, default=60.0)
    gateway_video_generate_parser.add_argument("--submit-timeout", type=float, default=300.0)
    gateway_video_generate_parser.add_argument("--download-timeout", type=float, default=120.0)
    gateway_video_generate_parser.add_argument("--poll-interval", type=float, default=3.0)
    gateway_video_generate_parser.add_argument("--max-wait", type=float, default=900.0)
    gateway_video_generate_parser.add_argument("--enable-live", action="store_true")
    gateway_video_generate_parser.set_defaults(func=gateway_video_generate_command)

    gateway_video_batch_parser = subparsers.add_parser(
        "gateway-video-batch",
        aliases=["video-batch"],
        help="Generate configured cloud video clips into OpenMontage asset paths",
    )
    gateway_video_batch_parser.add_argument("--project", default="sample_episode")
    gateway_video_batch_parser.add_argument("--model", default="")
    gateway_video_batch_parser.add_argument("--handoff", default="")
    gateway_video_batch_parser.add_argument("--package", default="")
    gateway_video_batch_parser.add_argument("--output", default="")
    gateway_video_batch_parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Maximum clips to generate; use 0 for all clips",
    )
    gateway_video_batch_parser.add_argument("--resolution", default="")
    gateway_video_batch_parser.add_argument("--generate-audio", action="store_true")
    gateway_video_batch_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate clips that already exist",
    )
    gateway_video_batch_parser.add_argument("--timeout", type=float, default=60.0)
    gateway_video_batch_parser.add_argument("--submit-timeout", type=float, default=300.0)
    gateway_video_batch_parser.add_argument("--download-timeout", type=float, default=120.0)
    gateway_video_batch_parser.add_argument("--poll-interval", type=float, default=3.0)
    gateway_video_batch_parser.add_argument("--max-wait", type=float, default=900.0)
    gateway_video_batch_parser.add_argument("--enable-live", action="store_true")
    gateway_video_batch_parser.set_defaults(func=gateway_video_batch_command)

    quality_bakeoff_parser = subparsers.add_parser(
        "quality-bakeoff-candidates",
        help="Plan or generate the representative production model bakeoff candidates",
    )
    quality_bakeoff_parser.add_argument("--project", default="sample_episode")
    quality_bakeoff_parser.add_argument(
        "--candidate",
        type=int,
        default=1,
        help="Candidate number from 1 to 3",
    )
    quality_bakeoff_parser.add_argument(
        "--kind",
        choices=("all", "video", "still"),
        default="all",
    )
    quality_bakeoff_parser.add_argument(
        "--micro-shot",
        action="append",
        default=[],
        help="Limit video bakeoff generation to one planned representative ID",
    )
    quality_bakeoff_parser.add_argument("--overwrite", action="store_true")
    quality_bakeoff_parser.add_argument("--timeout", type=float, default=120.0)
    quality_bakeoff_parser.add_argument(
        "--submit-timeout",
        type=float,
        default=300.0,
    )
    quality_bakeoff_parser.add_argument(
        "--download-timeout",
        type=float,
        default=120.0,
    )
    quality_bakeoff_parser.add_argument(
        "--poll-interval",
        type=float,
        default=3.0,
    )
    quality_bakeoff_parser.add_argument("--max-wait", type=float, default=900.0)
    quality_bakeoff_parser.add_argument(
        "--enable-live",
        action="store_true",
        help="Explicitly allow the selected potentially billable bakeoff requests",
    )
    quality_bakeoff_parser.set_defaults(func=quality_bakeoff_candidates_command)

    quality_qc_parser = subparsers.add_parser(
        "quality-visual-qc",
        help="Analyze and optionally review one deterministic micro-video candidate",
    )
    quality_qc_parser.add_argument("--project", default="sample_episode")
    quality_qc_parser.add_argument("--micro-shot", required=True)
    quality_qc_parser.add_argument(
        "--model",
        choices=("doubao-seedance-2-0",),
        required=True,
    )
    quality_qc_parser.add_argument("--candidate", type=int, default=1)
    quality_qc_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-run automatic evidence collection while preserving older evidence",
    )
    quality_qc_parser.add_argument(
        "--review",
        default="",
        help="Optional exact-schema manual review JSON",
    )
    quality_qc_parser.set_defaults(func=quality_visual_qc_command)

    quality_finalize_parser = subparsers.add_parser(
        "quality-finalize-bakeoff",
        help="Validate reviewed candidates and select the production models",
    )
    quality_finalize_parser.add_argument("--project", default="sample_episode")
    quality_finalize_parser.add_argument(
        "--review",
        required=True,
        help="Exact-schema model bakeoff review JSON",
    )
    quality_finalize_parser.set_defaults(func=quality_finalize_bakeoff_command)

    quality_production_parser = subparsers.add_parser(
        "quality-production-candidates",
        help="Plan or generate all production micro-shot candidates after bakeoff",
    )
    quality_production_parser.add_argument(
        "--project",
        default="sample_episode",
    )
    quality_production_parser.add_argument(
        "--candidate",
        type=int,
        default=1,
        help="Candidate number from 1 to 3",
    )
    quality_production_parser.add_argument(
        "--kind",
        choices=("all", "video", "still"),
        default="all",
    )
    quality_production_parser.add_argument(
        "--micro-shot",
        action="append",
        default=[],
        help="Generate only this exact micro-shot ID; repeat for multiple shots",
    )
    quality_production_parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum jobs per selected route; use 0 for all jobs",
    )
    quality_production_parser.add_argument("--overwrite", action="store_true")
    quality_production_parser.add_argument("--timeout", type=float, default=120.0)
    quality_production_parser.add_argument(
        "--submit-timeout",
        type=float,
        default=300.0,
    )
    quality_production_parser.add_argument(
        "--download-timeout",
        type=float,
        default=120.0,
    )
    quality_production_parser.add_argument(
        "--poll-interval",
        type=float,
        default=3.0,
    )
    quality_production_parser.add_argument(
        "--max-wait",
        type=float,
        default=900.0,
    )
    quality_production_parser.add_argument(
        "--enable-live",
        action="store_true",
        help="Explicitly allow selected potentially billable production requests",
    )
    quality_production_parser.set_defaults(
        func=quality_production_candidates_command
    )

    quality_select_parser = subparsers.add_parser(
        "quality-select",
        help="Validate and publish the complete reviewed visual selection",
    )
    quality_select_parser.add_argument("--project", default="sample_episode")
    quality_select_parser.add_argument(
        "--selection",
        required=True,
        help="Reviewed visual selection JSON inside the project run directory",
    )
    quality_select_parser.set_defaults(func=quality_select_command)

    pet_sitcom_parser = subparsers.add_parser(
        "pet-sitcom",
        help="Plan, generate, review, and compose the fixed original AI pet sitcom",
    )
    pet_sitcom_parser.add_argument(
        "--stage",
        choices=PET_STAGE_ORDER,
        default="plan",
    )
    pet_sitcom_parser.add_argument("--output-dir", default="")
    pet_sitcom_parser.add_argument("--anchor", choices=_PET_ANCHORS, action="append", default=[])
    pet_sitcom_parser.add_argument("--shot", choices=_PET_SHOTS, action="append", default=[])
    pet_sitcom_parser.add_argument("--candidate", choices=(1, 2, 3, 4, 5), type=int, default=1)
    pet_sitcom_parser.add_argument("--retry-reason", choices=tuple(sorted(_PET_RETRY_REASONS)), default="")
    pet_sitcom_parser.add_argument("--music-source", default="")
    pet_sitcom_parser.add_argument("--enable-live", action="store_true")
    pet_sitcom_parser.add_argument("--timeout", type=float, default=120.0)
    pet_sitcom_parser.add_argument("--submit-timeout", type=float, default=300.0)
    pet_sitcom_parser.add_argument("--download-timeout", type=float, default=120.0)
    pet_sitcom_parser.add_argument("--poll-interval", type=float, default=3.0)
    pet_sitcom_parser.add_argument("--max-wait", type=float, default=900.0)
    pet_sitcom_parser.set_defaults(func=pet_sitcom_command)
    add_pet_replica_parser(subparsers)
    add_factory_parser(subparsers)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
