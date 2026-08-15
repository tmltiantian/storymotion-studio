from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .pipeline_contracts import ProjectMode, ProjectSpec, StageName, StageState
from .pipeline_migration import migrate_existing_project
from .pipeline_review import ApprovalPreset
from .pipeline_runner import pipeline_status, resume_pipeline, run_pipeline
from .pipeline_store import approve_stage, create_project, request_stage_changes


def add_factory_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "factory",
        help="Create, run, resume, review, and publish any video project",
    )
    commands = parser.add_subparsers(dest="factory_command", required=True)

    create = commands.add_parser("create", help="Create a unified project spec")
    create.add_argument(
        "--mode", choices=tuple(mode.value for mode in ProjectMode), required=True
    )
    create.add_argument("--project", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--idea", default="")
    create.add_argument("--input", default="")
    create.add_argument("--output-dir", default="")
    create.add_argument("--duration", type=float, default=60.0)
    create.add_argument("--shots", type=int, default=8)
    create.add_argument("--ratio", default="9:16")
    create.add_argument("--resolution", default="1080x1920")
    create.add_argument("--fps", type=int, default=30)
    create.add_argument("--character-assets", default="")
    create.add_argument(
        "--approval-preset",
        choices=tuple(preset.value for preset in ApprovalPreset),
        default=ApprovalPreset.STANDARD.value,
    )
    create.set_defaults(factory_func=_create_command)

    migrate = commands.add_parser(
        "migrate", help="Register an existing project without moving its files"
    )
    migrate.add_argument(
        "--mode", choices=tuple(mode.value for mode in ProjectMode), required=True
    )
    migrate.add_argument("--project", required=True)
    migrate.add_argument("--title", required=True)
    migrate.add_argument("--legacy-root", required=True)
    migrate.set_defaults(factory_func=_migrate_command)

    for name, handler in (
        ("run", _run_command),
        ("resume", _resume_command),
    ):
        command = commands.add_parser(name, help=f"{name.title()} a unified project")
        command.add_argument("project")
        command.add_argument(
            "--through", choices=tuple(stage.value for stage in StageName)
        )
        command.add_argument("--enable-live", action="store_true")
        command.set_defaults(factory_func=handler)

    status = commands.add_parser("status", help="Show unified project status")
    status.add_argument("project")
    status.set_defaults(factory_func=_status_command)

    approve = commands.add_parser(
        "approve", help="Approve a blocked stage with review evidence"
    )
    approve.add_argument("project")
    approve.add_argument(
        "--stage", choices=tuple(stage.value for stage in StageName), required=True
    )
    approve.add_argument("--revision", type=int, required=True)
    approve.add_argument("--note", required=True)
    approve.add_argument("--evidence", action="append", required=True)
    approve.set_defaults(factory_func=_approve_command)

    request_changes = commands.add_parser(
        "request-changes", help="Request changes to a reviewed stage revision"
    )
    request_changes.add_argument("project")
    request_changes.add_argument(
        "--stage", choices=tuple(stage.value for stage in StageName), required=True
    )
    request_changes.add_argument("--revision", type=int, required=True)
    request_changes.add_argument("--reason", required=True)
    request_changes.set_defaults(factory_func=_request_changes_command)

    review = commands.add_parser("review", help="Run through the EVAL stage")
    review.add_argument("project")
    review.add_argument("--enable-live", action="store_true")
    review.set_defaults(factory_func=_review_command)

    publish = commands.add_parser("publish", help="Run the final delivery gate")
    publish.add_argument("project")
    publish.add_argument("--enable-live", action="store_true")
    publish.set_defaults(factory_func=_publish_command)

    parser.set_defaults(func=factory_command)
    return parser


def _load_config(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Factory config must contain a JSON object")
    return value


def _runs_dir(args: argparse.Namespace) -> Path:
    config = _load_config(args.config)
    return Path(config["runsDir"]).expanduser().resolve()


def _project_dir(args: argparse.Namespace) -> Path:
    return _runs_dir(args) / args.project


def _create_input(
    args: argparse.Namespace, mode: ProjectMode, project_dir: Path
) -> dict[str, Any]:
    if mode is ProjectMode.ORIGINAL:
        if not args.idea.strip():
            raise ValueError("original mode requires --idea")
        source_dir = project_dir / "source"
        if source_dir.is_symlink():
            raise ValueError("project source directory cannot be a symlink")
        source_dir.mkdir(parents=True, exist_ok=True)
        idea_path = source_dir / "idea.txt"
        idea_path.write_text(args.idea.strip() + "\n", encoding="utf-8")
        return {
            "kind": "idea",
            "text": args.idea.strip(),
            "path": str(idea_path),
        }
    if not args.input.strip():
        raise ValueError(f"{mode.value} mode requires --input")
    kind = "novel" if mode is ProjectMode.NOVEL else "reference"
    return {"kind": kind, "path": str(Path(args.input).expanduser())}


def _create_command(args: argparse.Namespace) -> dict[str, Any]:
    mode = ProjectMode(args.mode)
    project_dir = _project_dir(args)
    config = _load_config(args.config)
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else Path(config["outputDir"]).expanduser().resolve() / args.project
    )
    spec = ProjectSpec(
        project_id=args.project,
        title=args.title,
        mode=mode,
        input=_create_input(args, mode, project_dir),
        output_dir=output_dir,
        target={
            "duration_seconds": args.duration,
            "shots": args.shots,
            "ratio": args.ratio,
            "resolution": args.resolution,
            "fps": args.fps,
        },
        policies={
            "enable_live": False,
            "audio_first": True,
            "approval_preset": args.approval_preset,
        },
        mode_options={
            "character_assets": args.character_assets,
            "factory_config": str(Path(args.config).expanduser().resolve()),
        },
    )
    package = create_project(project_dir, spec)
    return {
        "success": True,
        "project_id": spec.project_id,
        "mode": spec.mode.value,
        "project_dir": str(project_dir),
        "next_stage": package.next_stage.value,
        "live_enabled": False,
    }


def _migrate_command(args: argparse.Namespace) -> dict[str, Any]:
    return migrate_existing_project(
        args.legacy_root,
        _project_dir(args),
        project_id=args.project,
        title=args.title,
        mode=ProjectMode(args.mode),
        factory_config=args.config,
    )


def _run_result(result) -> dict[str, Any]:
    next_stage = result.next_stage.value if result.next_stage else "complete"
    if result.success:
        run_state = "complete" if result.next_stage is None else "paused"
    else:
        run_state = "failed" if result.stopped_state is StageState.FAILED else "blocked"
    return {
        "success": result.success,
        "run_state": run_state,
        "stopped_at": result.stopped_at.value if result.stopped_at else None,
        "next_stage": next_stage,
        "completed_stages": [stage.value for stage in result.completed_stages],
    }


def _run_command(args: argparse.Namespace) -> dict[str, Any]:
    return _run_result(
        run_pipeline(
            _project_dir(args),
            through=StageName(args.through) if args.through else None,
            enable_live=args.enable_live,
        )
    )


def _resume_command(args: argparse.Namespace) -> dict[str, Any]:
    return _run_result(
        resume_pipeline(
            _project_dir(args),
            through=StageName(args.through) if args.through else None,
            enable_live=args.enable_live,
        )
    )


def _status_command(args: argparse.Namespace) -> dict[str, Any]:
    return pipeline_status(_project_dir(args))


def _approve_command(args: argparse.Namespace) -> dict[str, Any]:
    package = approve_stage(
        _project_dir(args),
        StageName(args.stage),
        revision=args.revision,
        note=args.note,
        evidence=tuple(Path(path).expanduser() for path in args.evidence),
    )
    return {
        "success": True,
        "approved_stage": args.stage,
        "next_stage": package.next_stage.value if package.next_stage else "complete",
    }


def _request_changes_command(args: argparse.Namespace) -> dict[str, Any]:
    package = request_stage_changes(
        _project_dir(args),
        StageName(args.stage),
        revision=args.revision,
        reason=args.reason,
    )
    return {
        "success": True,
        "review_state": "changes_requested",
        "requested_stage": args.stage,
        "next_stage": package.next_stage.value if package.next_stage else "complete",
    }


def _review_command(args: argparse.Namespace) -> dict[str, Any]:
    return _run_result(
        resume_pipeline(
            _project_dir(args),
            through=StageName.EVAL,
            enable_live=args.enable_live,
        )
    )


def _publish_command(args: argparse.Namespace) -> dict[str, Any]:
    status = pipeline_status(_project_dir(args))
    if status["stages"][StageName.EVAL.value] != "passed":
        raise ValueError("publish requires the EVAL stage to pass")
    return _run_result(
        resume_pipeline(
            _project_dir(args),
            through=StageName.DELIVER,
            enable_live=args.enable_live,
        )
    )


def factory_command(args: argparse.Namespace) -> int:
    try:
        payload = args.factory_func(args)
        code = 0 if payload.get("success") else 1
    except (ValueError, OSError, RuntimeError) as exc:
        payload = {"success": False, "error": str(exc)}
        code = 1
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return code
