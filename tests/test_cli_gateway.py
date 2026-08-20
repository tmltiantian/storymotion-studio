import json
import os
import subprocess
import sys
import wave
from pathlib import Path


PYTHON = sys.executable


def _config(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    runs = tmp_path / "runs"
    workspace.mkdir()
    config = tmp_path / "factory.config.json"
    config.write_text(
        json.dumps(
            {
                "workspace": str(workspace),
                "sources": {},
                "runsDir": str(runs),
                "outputDir": str(tmp_path / "output"),
            }
        ),
        encoding="utf-8",
    )
    return config


def _gateway_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "LLM_PROVIDER": "gateway",
            "IMAGE_PROVIDER": "gateway",
            "VIDEO_PROVIDER": "gateway",
            "GATEWAY_BASE_URL": "https://gateway.example.invalid",
            "ENABLE_GATEWAY_VIDEO": "1",
            "TTS_PROVIDER": "local",
            "GATEWAY_API_KEY": "FICTIONAL_TEST_SECRET_SENTINEL_DO_NOT_USE",
        }
    )
    env.pop("DASHSCOPE_API_KEY", None)
    return env


def _minimax_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "VIDEO_PROVIDER": "minimax",
            "MINIMAX_API_KEY": "FICTIONAL_TEST_SECRET_SENTINEL_DO_NOT_USE",
            "MINIMAX_API_BASE": "https://api.minimaxi.com",
            "MINIMAX_VIDEO_MODEL": "MiniMax-H3",
            "ENABLE_MINIMAX_VIDEO": "1",
        }
    )
    return env


def test_cli_provider_report_writes_sanitized_profile(tmp_path):
    config = _config(tmp_path)

    result = subprocess.run(
        [PYTHON, "factory_cli.py", "--config", str(config), "provider-report"],
        check=True,
        capture_output=True,
        text=True,
        env=_gateway_env(),
    )

    payload = json.loads(result.stdout)
    report_path = Path(payload["provider_report"])
    report_text = report_path.read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert report["capabilities"]["text"]["provider"] == "gateway"
    assert report["capabilities"]["image"]["provider"] == "gateway"
    assert "FICTIONAL_TEST_SECRET_SENTINEL_DO_NOT_USE" not in report_text


def test_cli_gateway_text_smoke_is_no_cost_without_enable_flag(tmp_path):
    config = _config(tmp_path)

    result = subprocess.run(
        [PYTHON, "factory_cli.py", "--config", str(config), "gateway-text-smoke"],
        check=True,
        capture_output=True,
        text=True,
        env=_gateway_env(),
    )

    payload = json.loads(result.stdout)
    report = json.loads(Path(payload["gateway_text_smoke"]).read_text(encoding="utf-8"))
    assert report["executed"] is False
    assert report["success"] is False
    assert report["blocked_reasons"] == ["Live gateway text smoke is disabled."]


def test_cli_gateway_image_is_no_cost_without_enable_flag(tmp_path):
    config = _config(tmp_path)
    output = tmp_path / "generated.png"

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "--config",
            str(config),
            "gateway-image",
            "--prompt",
            "cinematic station",
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_gateway_env(),
    )

    payload = json.loads(result.stdout)
    report = json.loads(Path(payload["gateway_image_report"]).read_text(encoding="utf-8"))
    assert report["executed"] is False
    assert report["blocked_reasons"] == ["Live gateway image generation is disabled."]
    assert output.exists() is False


def test_cli_gateway_video_probe_is_no_cost_without_enable_flag(tmp_path):
    config = _config(tmp_path)

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "--config",
            str(config),
            "gateway-video-probe",
            "--project",
            "sample",
            "--model",
            "doubao-seedance-2-0",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_gateway_env(),
    )

    payload = json.loads(result.stdout)
    report = json.loads(Path(payload["gateway_video_probe"]).read_text(encoding="utf-8"))
    assert report["executed"] is False
    assert report["model"] == "doubao-seedance-2-0"
    assert report["blocked_reasons"] == ["Live gateway video probe is disabled."]


def test_cli_gateway_video_probe_does_not_bypass_disabled_provider_gate(tmp_path):
    config = _config(tmp_path)
    env = _gateway_env()
    env["ENABLE_GATEWAY_VIDEO"] = "0"

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "--config",
            str(config),
            "gateway-video-probe",
            "--project",
            "sample",
            "--timeout",
            "0.1",
            "--enable-live",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    payload = json.loads(result.stdout)
    report = json.loads(Path(payload["gateway_video_probe"]).read_text(encoding="utf-8"))
    assert report["executed"] is False
    assert report["blocked_reasons"] == [
        "Gateway video is disabled; set ENABLE_GATEWAY_VIDEO=1."
    ]


def test_cli_gateway_video_generate_is_no_cost_without_enable_flag(tmp_path):
    config = _config(tmp_path)
    output = tmp_path / "shot.mp4"
    audio = tmp_path / "drive.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\0\0" * 24000)

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "--config",
            str(config),
            "gateway-video-generate",
            "--prompt",
            "animate the station scene",
            "--output",
            str(output),
            "--model",
            "doubao-seedance-2-0",
            "--audio",
            str(audio),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_gateway_env(),
    )

    payload = json.loads(result.stdout)
    report = json.loads(Path(payload["gateway_video_report"]).read_text(encoding="utf-8"))
    assert report["executed"] is False
    assert report["model"] == "doubao-seedance-2-0"
    assert report["reference_audio_provided"] is True
    assert report["blocked_reasons"] == ["Live gateway video generation is disabled."]
    assert output.exists() is False
    assert "FICTIONAL_TEST_SECRET_SENTINEL_DO_NOT_USE" not in json.dumps(report)


def test_cli_gateway_video_generate_returns_error_for_invalid_dry_run(tmp_path):
    config = _config(tmp_path)

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "--config",
            str(config),
            "gateway-video-generate",
            "--prompt",
            "animate the station scene",
            "--output",
            str(tmp_path / "shot.mp4"),
            "--duration",
            "16",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=_gateway_env(),
    )

    payload = json.loads(result.stdout)
    report = json.loads(Path(payload["gateway_video_report"]).read_text(encoding="utf-8"))
    assert result.returncode == 1
    assert report["plan_ready"] is False
    assert "at most 15 seconds" in report["error"]


def test_cli_video_generate_uses_minimax_h3_defaults_without_network(tmp_path):
    config = _config(tmp_path)
    output = tmp_path / "h3.mp4"

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "--config",
            str(config),
            "video-generate",
            "--prompt",
            "a cat raises one paw naturally",
            "--output",
            str(output),
            "--duration",
            "4",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_minimax_env(),
    )

    payload = json.loads(result.stdout)
    report = json.loads(Path(payload["gateway_video_report"]).read_text(encoding="utf-8"))
    assert report["provider"] == "minimax"
    assert report["model"] == "MiniMax-H3"
    assert report["jobs"][0]["resolution"] == "768P"
    assert report["executed"] is False
    assert report["plan_ready"] is True
    assert output.exists() is False
    assert "minimax-do-not-leak" not in json.dumps(report)


def test_cli_video_generate_accepts_explicit_confirmed_project_scope_without_network(
    tmp_path,
):
    config = _config(tmp_path)
    output = tmp_path / "h3.mp4"

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "--config",
            str(config),
            "video-generate",
            "--prompt",
            "a cat raises one paw naturally",
            "--output",
            str(output),
            "--duration",
            "4",
            "--project-dir",
            str(tmp_path / "approved-project"),
            "--shot-id",
            "H3-A",
            "--confirm-paid",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=_minimax_env(),
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    report = json.loads(Path(payload["gateway_video_report"]).read_text("utf-8"))
    assert report["executed"] is False
    assert report["blocked_reasons"] == ["Live gateway video generation is disabled."]
    assert output.exists() is False


def test_cli_video_generate_reports_unsupported_provider_without_crashing(tmp_path):
    config = _config(tmp_path)
    env = os.environ.copy()
    env["VIDEO_PROVIDER"] = "local"

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "--config",
            str(config),
            "video-generate",
            "--prompt",
            "a cat moves",
            "--output",
            str(tmp_path / "clip.mp4"),
            "--enable-live",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    payload = json.loads(result.stdout)
    report = json.loads(Path(payload["gateway_video_report"]).read_text("utf-8"))
    assert result.returncode == 1
    assert report["executed"] is False
    assert report["blocked_reasons"] == [
        "A supported cloud video provider is not configured."
    ]


def test_cli_video_batch_reports_unsupported_provider_before_reading_inputs(tmp_path):
    config = _config(tmp_path)
    env = os.environ.copy()
    env["VIDEO_PROVIDER"] = "local"

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "--config",
            str(config),
            "video-batch",
            "--project",
            "missing-project",
            "--enable-live",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    payload = json.loads(result.stdout)
    report = json.loads(Path(payload["gateway_video_batch"]).read_text("utf-8"))
    assert result.returncode == 1
    assert report["executed"] is False
    assert report["blocked_reasons"] == [
        "A supported cloud video provider is not configured."
    ]


def test_cli_gateway_video_batch_plans_openmontage_clips_without_network(tmp_path):
    config = _config(tmp_path)
    config_data = json.loads(config.read_text(encoding="utf-8"))
    run_dir = Path(config_data["runsDir"]) / "sample"
    role = run_dir / "roles/character.png"
    role.parent.mkdir(parents=True)
    role.write_bytes(b"\x89PNG\r\n\x1a\nrole")
    (run_dir / "video_handoff.json").write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.video-handoff.v1",
                "project_id": "sample",
                "characters": [
                    {
                        "id": "char_1",
                        "reference_image_path": str(role),
                        "reference_image_exists": True,
                    }
                ],
                "shots": [
                    {
                        "id": "frame_001",
                        "character_ids": ["char_1"],
                        "video_prompt": "character enters a station",
                        "duration_seconds": 5,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    clip = run_dir / "clips/shot_001.mp4"
    (run_dir / "openmontage_package.json").write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.openmontage.v1",
                "project_id": "sample",
                "character_assets": {
                    "production_ready": True,
                    "characters": [
                        {
                            "character_id": "char_1",
                            "reference_image_path": str(role),
                            "production_ready": True,
                        }
                    ],
                },
                "target": {"aspect_ratio": "9:16"},
                "timeline": [
                    {
                        "shot_id": "shot_001",
                        "index": 1,
                        "expected_assets": {"video_clip": str(clip)},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "--config",
            str(config),
            "gateway-video-batch",
            "--project",
            "sample",
            "--model",
            "doubao-seedance-2-0",
            "--overwrite",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_gateway_env(),
    )

    payload = json.loads(result.stdout)
    report = json.loads(Path(payload["gateway_video_batch"]).read_text(encoding="utf-8"))
    assert report["plan_ready"] is True
    assert report["planned_count"] == 1
    assert report["executed"] is False
    assert report["model"] == "doubao-seedance-2-0"
    assert report["overwrite"] is True
    assert clip.exists() is False
    assert "do-not-leak" not in json.dumps(report)


def test_cli_exposes_local_refresh_preview_command():
    result = subprocess.run(
        [PYTHON, "factory_cli.py", "refresh-preview", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--project" in result.stdout
    assert "Rebuild the voiced OpenMontage preview" in result.stdout
