from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import factory_cli
from factory.pipeline_contracts import ProjectMode, StageName
from factory.pipeline_contracts import StageState
from factory.pipeline_runner import PipelineRunResult


def test_top_level_cli_exposes_factory_without_legacy_composite_commands() -> None:
    parser = factory_cli.build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )

    assert "factory" in subparsers.choices
    obsolete_commands = {
        "plan",
        "run-project",
        "enqueue",
        "worker",
        "probe",
        "lumenx-health",
        "env-report",
        "lumenx-bootstrap",
        "lumenx-execute-handoff",
        "lumenx-generate-live",
        "lumenx-live-run",
        "lumenx-mock-live-run",
        "readiness",
        "workflow-status",
        "real-generation-preflight",
        "real-generation-start-gate",
        "operator-handoff",
        "dashboard",
    }
    assert obsolete_commands.isdisjoint(subparsers.choices)
    assert {"provider-report", "gateway-video-batch", "pet-replica"}.issubset(
        subparsers.choices
    )


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "factory.json"
    path.write_text(
        json.dumps(
            {
                "workspace": str(tmp_path),
                "runsDir": str(tmp_path / "runs"),
                "outputDir": str(tmp_path / "output"),
            }
        ),
        encoding="utf-8",
    )
    return path


def _run(monkeypatch, capsys, *arguments: str):
    monkeypatch.setattr(sys, "argv", ["factory_cli.py", *arguments])
    code = factory_cli.main()
    return code, json.loads(capsys.readouterr().out)


def test_factory_create_writes_original_project_without_live_calls(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config = _config(tmp_path)

    code, payload = _run(
        monkeypatch,
        capsys,
        "--config",
        str(config),
        "factory",
        "create",
        "--mode",
        "original",
        "--project",
        "cat_episode",
        "--title",
        "窗边的声音",
        "--idea",
        "两只猫调查窗帘后的声音",
        "--duration",
        "60",
    )

    assert code == 0
    assert payload["mode"] == ProjectMode.ORIGINAL.value
    assert payload["next_stage"] == StageName.CONCEPT.value
    assert payload["live_enabled"] is False
    assert (tmp_path / "runs" / "cat_episode" / "project.json").is_file()
    source = tmp_path / "runs" / "cat_episode" / "source" / "idea.txt"
    assert source.read_text(encoding="utf-8") == "两只猫调查窗帘后的声音\n"
    spec = json.loads(
        (tmp_path / "runs" / "cat_episode" / "project.json").read_text(
            encoding="utf-8"
        )
    )
    assert spec["input"]["path"] == str(source)


def test_factory_create_requires_mode_appropriate_input(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config = _config(tmp_path)

    code, payload = _run(
        monkeypatch,
        capsys,
        "--config",
        str(config),
        "factory",
        "create",
        "--mode",
        "novel",
        "--project",
        "novel_episode",
        "--title",
        "Novel",
    )

    assert code == 1
    assert "--input" in payload["error"]


def test_factory_run_passes_through_and_live_only_when_explicit(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config = _config(tmp_path)
    calls = []
    monkeypatch.setattr(
        "factory.pipeline_cli.run_pipeline",
        lambda project_dir, **kwargs: calls.append((project_dir, kwargs))
        or PipelineRunResult(
            True,
            None,
            (StageName.CONCEPT,),
            next_stage=StageName.ASSETS,
        ),
    )

    code, payload = _run(
        monkeypatch,
        capsys,
        "--config",
        str(config),
        "factory",
        "run",
        "cat_episode",
        "--through",
        "storyboard",
    )

    assert code == 0
    assert calls[0][1]["through"] is StageName.STORYBOARD
    assert calls[0][1]["enable_live"] is False
    assert payload["completed_stages"] == ["concept"]
    assert payload["run_state"] == "paused"
    assert payload["next_stage"] == "assets"
    assert payload["stopped_at"] is None


def test_factory_run_distinguishes_failed_from_blocked(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        "factory.pipeline_cli.run_pipeline",
        lambda *_args, **_kwargs: PipelineRunResult(
            False,
            StageName.SCRIPT,
            (StageName.CONCEPT,),
            next_stage=StageName.SCRIPT,
            stopped_state=StageState.FAILED,
        ),
    )

    code, payload = _run(
        monkeypatch,
        capsys,
        "--config",
        str(config),
        "factory",
        "run",
        "cat_episode",
    )

    assert code == 1
    assert payload["run_state"] == "failed"
    assert payload["stopped_at"] == "script"


def test_factory_status_uses_single_project_location(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        "factory.pipeline_cli.pipeline_status",
        lambda path: {"success": True, "project_dir": str(path), "next_stage": "audio"},
    )

    code, payload = _run(
        monkeypatch,
        capsys,
        "--config",
        str(config),
        "factory",
        "status",
        "cat_episode",
    )

    assert code == 0
    assert payload["project_dir"] == str(tmp_path / "runs" / "cat_episode")
    assert payload["next_stage"] == "audio"


def test_factory_migrate_uses_read_only_legacy_source(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config = _config(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    calls = []
    monkeypatch.setattr(
        "factory.pipeline_cli.migrate_existing_project",
        lambda legacy_root, project_dir, **kwargs: calls.append(
            (legacy_root, project_dir, kwargs)
        )
        or {"success": True, "project_id": kwargs["project_id"]},
    )

    code, payload = _run(
        monkeypatch,
        capsys,
        "--config",
        str(config),
        "factory",
        "migrate",
        "--mode",
        "replica",
        "--project",
        "old_replica",
        "--title",
        "旧复刻项目",
        "--legacy-root",
        str(legacy),
    )

    assert code == 0
    assert payload["project_id"] == "old_replica"
    assert calls[0][1] == tmp_path / "runs" / "old_replica"
    assert calls[0][2]["mode"] is ProjectMode.REPLICA


def test_factory_approve_passes_note_and_evidence_to_store(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config = _config(tmp_path)
    evidence = tmp_path / "review.json"
    evidence.write_text("{}", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        "factory.pipeline_cli.approve_stage",
        lambda project_dir, stage, **kwargs: calls.append(
            (project_dir, stage, kwargs)
        )
        or type(
            "Package", (), {"next_stage": StageName.DELIVER}
        )(),
    )

    code, payload = _run(
        monkeypatch,
        capsys,
        "--config",
        str(config),
        "factory",
        "approve",
        "cat_episode",
        "--stage",
        "eval",
        "--note",
        "逐镜检查通过",
        "--evidence",
        str(evidence),
    )

    assert code == 0
    assert calls[0][1] is StageName.EVAL
    assert calls[0][2]["note"] == "逐镜检查通过"
    assert calls[0][2]["evidence"] == (evidence,)
    assert payload["next_stage"] == "deliver"
