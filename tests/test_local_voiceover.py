import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import factory.local_voiceover as local_voiceover
from factory.local_voiceover import (
    build_mix_voiceover_audio_command,
    build_mux_voiced_preview_command,
    build_say_command,
    build_voiceover_cues,
    render_voiceover_preview,
    write_voiceover_script,
)
from factory.doubao_tts import DoubaoTTSAPIError
from factory.novel_planner import plan_episode


def _accept_nonempty_media(path: Path, required_stream: str) -> bool:
    return path.is_file() and path.stat().st_size > 0


def test_build_voiceover_cues_assigns_speakers_and_monotonic_starts():
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "voiceover_sample", target_shots=2)

    cues = build_voiceover_cues(episode)

    assert cues
    assert cues[0].speaker_name == "旁白"
    assert cues[0].voice == "Reed (中文（中国大陆）)"
    assert any(cue.speaker_name == "苏眠" for cue in cues)
    assert [cue.start_seconds for cue in cues] == sorted(cue.start_seconds for cue in cues)
    assert max(cue.start_seconds for cue in cues) < sum(shot.duration_seconds for shot in episode.shots)


def test_write_voiceover_script_records_human_readable_cues(tmp_path: Path):
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "voiceover_script", target_shots=1)
    cues = build_voiceover_cues(episode)

    path = write_voiceover_script(cues, tmp_path / "voiceover_script.txt")

    text = path.read_text(encoding="utf-8")
    assert "00.40s" in text
    assert "旁白：" in text


def test_build_say_command_targets_aiff_clip():
    cmd = build_say_command(
        text="旁白：雨停了。",
        voice="Tingting",
        output_path="/tmp/clip.aiff",
        rate=185,
        say_bin="say",
    )

    assert cmd == ["say", "-v", "Tingting", "-r", "185", "-o", "/tmp/clip.aiff", "旁白：雨停了。"]


def test_schedule_voiceover_cues_uses_measured_durations_without_overlap():
    episode = plan_episode(
        "苏眠站在街灯下。她低声说，别急。",
        "measured_timing",
        target_shots=1,
    )
    cues = build_voiceover_cues(episode)

    assert hasattr(local_voiceover, "schedule_voiceover_cues")
    scheduled, timings = local_voiceover.schedule_voiceover_cues(
        episode,
        cues,
        [3.0, 2.0],
    )

    assert scheduled[1].start_seconds >= timings[0]["end_seconds"] + 0.25
    assert timings[0]["overlaps_previous"] is False
    assert timings[1]["overlaps_previous"] is False
    assert timings[-1]["end_seconds"] <= episode.shots[0].duration_seconds


def test_build_mix_voiceover_audio_command_delays_each_clip():
    cmd = build_mix_voiceover_audio_command(
        clip_paths=[Path("/tmp/clip1.aiff"), Path("/tmp/clip2.aiff")],
        starts_seconds=[0.4, 2.25],
        duration_seconds=8.0,
        output_path="/tmp/voiceover.m4a",
    )

    joined = " ".join(str(part) for part in cmd)
    assert cmd[0] == "ffmpeg"
    assert "/tmp/clip1.aiff" in cmd
    assert "adelay=400|400" in joined
    assert "adelay=2250|2250" in joined
    assert "amix=inputs=2" in joined
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in joined
    assert cmd[cmd.index("-ar") + 1] == "48000"
    assert cmd[cmd.index("-ac") + 1] == "2"
    assert cmd[-1] == "/tmp/voiceover.m4a"


def test_build_mux_voiced_preview_command_replaces_silent_audio():
    cmd = build_mux_voiced_preview_command(
        source_video_path="/tmp/card_preview.mp4",
        voiceover_audio_path="/tmp/voiceover.m4a",
        output_path="/tmp/card_preview_voiced.mp4",
    )

    assert cmd[:4] == ["ffmpeg", "-y", "-i", "/tmp/card_preview.mp4"]
    assert "-map" in cmd
    assert "0:s?" in cmd
    assert cmd[-1] == "/tmp/card_preview_voiced.mp4"


def test_render_voiceover_uses_doubao_when_configured(tmp_path: Path):
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "doubao_voiceover", target_shots=2)
    source_video = tmp_path / "card_preview.mp4"
    source_video.write_bytes(b"video")

    class FakeDoubaoClient:
        def __init__(self):
            self.texts = []

        def synthesize(self, text, output_path, **kwargs):
            self.texts.append(text)
            output = Path(output_path)
            metadata = output.with_suffix(output.suffix + ".json")
            output.write_bytes(b"ID3")
            metadata.write_text('{"data": {"sentences": []}}\n', encoding="utf-8")
            return SimpleNamespace(output_path=output, metadata_path=metadata)

    def fake_command_runner(command, **kwargs):
        output = Path(command[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rendered")
        return SimpleNamespace(returncode=0)

    fake_client = FakeDoubaoClient()
    config = {
        "workspace": str(tmp_path),
        "sources": {"openMontage": str(tmp_path / "OpenMontage")},
    }
    result = render_voiceover_preview(
        episode,
        source_video_path=source_video,
        output_path=tmp_path / "card_preview_voiced.mp4",
        work_dir=tmp_path / "voiceover",
        config=config,
        process_env={
            "TTS_PROVIDER": "auto",
            "DOUBAO_SPEECH_API_KEY": "secret",
            "DOUBAO_SPEECH_VOICE_TYPE": "voice",
        },
        doubao_client=fake_client,
        command_runner=fake_command_runner,
        media_validator=_accept_nonempty_media,
    )

    cues = build_voiceover_cues(episode)
    assert result["voiceover_provider"] == "doubao"
    assert result["doubao_clip_count"] == len(cues)
    assert result["local_clip_count"] == 0
    assert fake_client.texts == [cue.text for cue in cues]
    assert Path(result["voiceover_provider_report"]).exists()


def test_render_voiceover_passes_distinct_role_voice_ids(tmp_path: Path):
    episode = plan_episode(
        "林澈推开门。苏眠低声说，别急。",
        "doubao_role_voices",
        target_shots=2,
    )
    source_video = tmp_path / "card_preview.mp4"
    source_video.write_bytes(b"video")

    class FakeDoubaoClient:
        def __init__(self):
            self.voice_ids = []

        def synthesize(self, text, output_path, **kwargs):
            self.voice_ids.append(kwargs.get("voice_id"))
            output = Path(output_path)
            metadata = output.with_suffix(output.suffix + ".json")
            output.write_bytes(b"ID3")
            metadata.write_text("{}\n", encoding="utf-8")
            return SimpleNamespace(output_path=output, metadata_path=metadata)

    def fake_command_runner(command, **kwargs):
        output = Path(command[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rendered")
        return SimpleNamespace(returncode=0)

    fake_client = FakeDoubaoClient()
    result = render_voiceover_preview(
        episode,
        source_video_path=source_video,
        output_path=tmp_path / "voiced.mp4",
        work_dir=tmp_path / "voiceover",
        config={
            "workspace": str(tmp_path),
            "sources": {"openMontage": str(tmp_path / "OpenMontage")},
        },
        process_env={
            "TTS_PROVIDER": "doubao",
            "DOUBAO_SPEECH_API_KEY": "secret",
            "DOUBAO_SPEECH_VOICE_TYPE": "default-voice",
            "DOUBAO_SPEECH_VOICE_MAP": json.dumps(
                {
                    "narrator": "narrator-voice",
                    "character_1": "lin-voice",
                    "character_2": "su-voice",
                }
            ),
        },
        doubao_client=fake_client,
        command_runner=fake_command_runner,
        media_validator=_accept_nonempty_media,
    )

    cues = build_voiceover_cues(episode)
    expected = {
        "旁白": "narrator-voice",
        "林澈": "lin-voice",
        "苏眠": "su-voice",
    }
    assert fake_client.voice_ids == [expected[cue.speaker_name] for cue in cues]
    report = json.loads(
        Path(result["voiceover_provider_report"]).read_text(encoding="utf-8")
    )
    assert report["role_voice_distinct"] is True
    assert report["warnings"] == []


def test_render_voiceover_falls_back_per_cue_and_redacts_errors(tmp_path: Path):
    episode = plan_episode("林澈推开门。", "doubao_fallback", target_shots=1)
    source_video = tmp_path / "card_preview.mp4"
    source_video.write_bytes(b"video")

    class FailingDoubaoClient:
        def synthesize(self, text, output_path, **kwargs):
            raise RuntimeError("provider rejected secret")

    def fake_command_runner(command, **kwargs):
        if command[0] == "say":
            output = Path(command[command.index("-o") + 1])
        else:
            output = Path(command[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rendered")
        return SimpleNamespace(returncode=0)

    config = {
        "workspace": str(tmp_path),
        "sources": {"openMontage": str(tmp_path / "OpenMontage")},
    }
    result = render_voiceover_preview(
        episode,
        source_video_path=source_video,
        output_path=tmp_path / "card_preview_voiced.mp4",
        work_dir=tmp_path / "voiceover",
        config=config,
        process_env={
            "TTS_PROVIDER": "auto",
            "DOUBAO_SPEECH_API_KEY": "secret",
            "DOUBAO_SPEECH_VOICE_TYPE": "voice",
        },
        doubao_client=FailingDoubaoClient(),
        command_runner=fake_command_runner,
        media_validator=_accept_nonempty_media,
    )

    report = Path(result["voiceover_provider_report"]).read_text(encoding="utf-8")
    assert result["voiceover_provider"] == "local"
    assert result["doubao_clip_count"] == 0
    assert result["local_clip_count"] == len(build_voiceover_cues(episode))
    assert "secret" not in report
    assert "[redacted]" in report


def test_render_voiceover_reuses_matching_completed_cue_state(tmp_path: Path):
    episode = plan_episode("林澈推开门。", "doubao_cache", target_shots=1)
    source_video = tmp_path / "card_preview.mp4"
    source_video.write_bytes(b"video")
    work = tmp_path / "voiceover"

    class CountingDoubaoClient:
        def __init__(self):
            self.calls = 0

        def synthesize(self, text, output_path, **kwargs):
            self.calls += 1
            output = Path(output_path)
            metadata = output.with_suffix(output.suffix + ".json")
            output.write_bytes(b"ID3")
            metadata.write_text("{}\n", encoding="utf-8")
            return SimpleNamespace(
                output_path=output,
                metadata_path=metadata,
                task_id=f"task-{self.calls}",
            )

    def fake_command_runner(command, **kwargs):
        output = Path(command[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rendered")
        return SimpleNamespace(returncode=0)

    client = CountingDoubaoClient()
    kwargs = {
        "episode": episode,
        "source_video_path": source_video,
        "output_path": tmp_path / "voiced.mp4",
        "work_dir": work,
        "config": {
            "workspace": str(tmp_path),
            "sources": {"openMontage": str(tmp_path / "OpenMontage")},
        },
        "process_env": {
            "TTS_PROVIDER": "doubao",
            "DOUBAO_SPEECH_API_KEY": "secret",
            "DOUBAO_SPEECH_VOICE_TYPE": "voice",
        },
        "doubao_client": client,
        "command_runner": fake_command_runner,
        "media_validator": _accept_nonempty_media,
    }

    first = render_voiceover_preview(**kwargs)
    calls_after_first = client.calls
    second = render_voiceover_preview(**kwargs)

    assert calls_after_first == len(build_voiceover_cues(episode))
    assert client.calls == calls_after_first
    assert second["reused_clip_count"] == calls_after_first
    assert first["voiceover_provider"] == "doubao"


def test_render_voiceover_trims_legacy_cached_doubao_clips_without_resubmitting(
    tmp_path: Path,
):
    episode = plan_episode("林澈推开门。", "doubao_trim_cache", target_shots=1)
    source_video = tmp_path / "card_preview.mp4"
    source_video.write_bytes(b"video")
    work = tmp_path / "voiceover"

    class CountingDoubaoClient:
        def __init__(self):
            self.calls = 0

        def synthesize(self, text, output_path, **kwargs):
            self.calls += 1
            output = Path(output_path)
            metadata = output.with_suffix(output.suffix + ".json")
            output.write_bytes(b"ID3")
            metadata.write_text("{}\n", encoding="utf-8")
            return SimpleNamespace(
                output_path=output,
                metadata_path=metadata,
                task_id=f"task-{self.calls}",
            )

    commands = []

    def fake_command_runner(command, **kwargs):
        commands.append(command)
        output = Path(command[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rendered")
        return SimpleNamespace(returncode=0)

    client = CountingDoubaoClient()
    kwargs = {
        "episode": episode,
        "source_video_path": source_video,
        "output_path": tmp_path / "voiced.mp4",
        "work_dir": work,
        "config": {
            "workspace": str(tmp_path),
            "sources": {"openMontage": str(tmp_path / "OpenMontage")},
        },
        "process_env": {
            "TTS_PROVIDER": "doubao",
            "DOUBAO_SPEECH_API_KEY": "secret",
            "DOUBAO_SPEECH_VOICE_TYPE": "voice",
        },
        "doubao_client": client,
        "command_runner": fake_command_runner,
        "media_validator": _accept_nonempty_media,
    }

    render_voiceover_preview(**kwargs)
    calls_after_first = client.calls
    for state_path in (work / "clips").glob("*.tts.json"):
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.pop("postprocess_profile", None)
        state_path.write_text(json.dumps(state), encoding="utf-8")

    commands.clear()
    second = render_voiceover_preview(**kwargs)

    trim_commands = [
        command
        for command in commands
        if "-af" in command
        and "silenceremove" in command[command.index("-af") + 1]
    ]
    states = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (work / "clips").glob("*.tts.json")
    ]
    assert client.calls == calls_after_first
    assert len(trim_commands) == len(build_voiceover_cues(episode))
    assert {state["postprocess_profile"] for state in states} == {
        "trim-boundaries-v1"
    }
    assert second["reused_clip_count"] == calls_after_first


def test_render_voiceover_resumes_submitted_cue_state(tmp_path: Path):
    episode = plan_episode("林澈推开门。", "doubao_resume", target_shots=1)
    source_video = tmp_path / "card_preview.mp4"
    source_video.write_bytes(b"video")

    class ResumableDoubaoClient:
        def __init__(self):
            self.submits = 0
            self.completions = 0

        def submit(self, text, **kwargs):
            self.submits += 1
            return SimpleNamespace(task_id="task-1", request_id=kwargs["request_id"])

        def complete_task(self, task, output_path, **kwargs):
            self.completions += 1
            output = Path(output_path)
            metadata = Path(kwargs["metadata_path"])
            output.write_bytes(b"ID3")
            metadata.write_text("{}\n", encoding="utf-8")
            return SimpleNamespace(
                output_path=output,
                metadata_path=metadata,
                task_id=task.task_id,
            )

    def fake_command_runner(command, **kwargs):
        output = Path(command[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rendered")
        return SimpleNamespace(returncode=0)

    client = ResumableDoubaoClient()
    common = {
        "episode": episode,
        "source_video_path": source_video,
        "output_path": tmp_path / "voiced.mp4",
        "work_dir": tmp_path / "voiceover",
        "config": {
            "workspace": str(tmp_path),
            "sources": {"openMontage": str(tmp_path / "OpenMontage")},
        },
        "process_env": {
            "TTS_PROVIDER": "doubao",
            "DOUBAO_SPEECH_API_KEY": "secret",
            "DOUBAO_SPEECH_VOICE_TYPE": "voice",
        },
        "doubao_client": client,
        "command_runner": fake_command_runner,
        "media_validator": _accept_nonempty_media,
    }

    render_voiceover_preview(**common)
    state_paths = sorted((tmp_path / "voiceover" / "clips").glob("*.tts.json"))
    state = json.loads(state_paths[0].read_text(encoding="utf-8"))
    Path(state["output_path"]).unlink()
    state["status"] = "submitted"
    state_paths[0].write_text(json.dumps(state), encoding="utf-8")

    render_voiceover_preview(**common)

    assert client.submits == len(build_voiceover_cues(episode))
    assert client.completions == len(build_voiceover_cues(episode)) + 1


def test_render_voiceover_reads_provider_from_factory_env(tmp_path: Path):
    episode = plan_episode("林澈推开门。", "factory_env_provider", target_shots=1)
    source_video = tmp_path / "card_preview.mp4"
    source_video.write_bytes(b"video")
    (tmp_path / ".env").write_text("TTS_PROVIDER=local\n", encoding="utf-8")
    openmontage = tmp_path / "OpenMontage"
    openmontage.mkdir()
    (openmontage / ".env").write_text(
        "DOUBAO_SPEECH_API_KEY=secret\n"
        "DOUBAO_SPEECH_VOICE_TYPE=voice\n",
        encoding="utf-8",
    )

    class NeverDoubaoClient:
        def synthesize(self, text, output_path, **kwargs):
            raise AssertionError("Doubao should not be selected")

    commands = []

    def fake_command_runner(command, **kwargs):
        commands.append(command)
        if command[0] == "say":
            output = Path(command[command.index("-o") + 1])
        else:
            output = Path(command[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rendered")
        return SimpleNamespace(returncode=0)

    result = render_voiceover_preview(
        episode,
        source_video_path=source_video,
        output_path=tmp_path / "card_preview_voiced.mp4",
        work_dir=tmp_path / "voiceover",
        config={
            "workspace": str(tmp_path),
            "sources": {"openMontage": str(openmontage)},
        },
        process_env={},
        doubao_client=NeverDoubaoClient(),
        command_runner=fake_command_runner,
        media_validator=_accept_nonempty_media,
    )

    assert result["voiceover_provider"] == "local"
    report = json.loads(
        Path(result["voiceover_provider_report"]).read_text(encoding="utf-8")
    )
    assert report["configuration_source"] == "factory.env"
    assert report["provider_selection_source"] == "factory.env"
    assert report["doubao_configuration_source"] == "openmontage.env"
    say_commands = [command for command in commands if command[0] == "say"]
    assert [command[-1] for command in say_commands] == [
        cue.text for cue in build_voiceover_cues(episode)
    ]
    assert all(command[command.index("-r") + 1] == "165" for command in say_commands)
    assert report["timing_overlap_count"] == 0


def test_render_voiceover_mux_failure_preserves_previous_video(tmp_path: Path):
    episode = plan_episode("林澈推开门。", "atomic_voiceover", target_shots=1)
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"video")
    output = tmp_path / "voiced.mp4"
    output.write_bytes(b"last-good")

    def fake_command_runner(command, **kwargs):
        if command[0] == "say":
            target = Path(command[command.index("-o") + 1])
        else:
            target = Path(command[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"partial" if target.suffix == ".mp4" else b"rendered")
        if target.suffix == ".mp4":
            raise subprocess.CalledProcessError(1, command, stderr="mux failed")
        return SimpleNamespace(returncode=0)

    with pytest.raises(subprocess.CalledProcessError, match="ffmpeg"):
        render_voiceover_preview(
            episode,
            source_video_path=source_video,
            output_path=output,
            work_dir=tmp_path / "voiceover",
            config={
                "workspace": str(tmp_path),
                "sources": {"openMontage": str(tmp_path / "OpenMontage")},
            },
            process_env={"TTS_PROVIDER": "local"},
            command_runner=fake_command_runner,
            media_validator=_accept_nonempty_media,
        )

    assert output.read_bytes() == b"last-good"


def test_definitive_doubao_rejection_is_retryable_not_ambiguous(tmp_path: Path):
    episode = plan_episode("林澈推开门。", "tts_rejected", target_shots=1)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")

    class RejectedClient:
        def __init__(self):
            self.submits = 0

        def submit(self, text, **kwargs):
            self.submits += 1
            raise DoubaoTTSAPIError("Invalid X-Api-Key")

        def complete_task(self, *args, **kwargs):
            raise AssertionError("Rejected task must not be completed")

    def fake_runner(command, **kwargs):
        target = (
            Path(command[command.index("-o") + 1])
            if command[0] == "say"
            else Path(command[-1])
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"rendered")

    client = RejectedClient()
    common = {
        "episode": episode,
        "source_video_path": source,
        "output_path": tmp_path / "voiced.mp4",
        "work_dir": tmp_path / "voiceover",
        "config": {
            "workspace": str(tmp_path),
            "sources": {"openMontage": str(tmp_path / "OpenMontage")},
        },
        "process_env": {
            "TTS_PROVIDER": "doubao",
            "DOUBAO_SPEECH_API_KEY": "secret",
            "DOUBAO_SPEECH_VOICE_TYPE": "voice",
        },
        "doubao_client": client,
        "command_runner": fake_runner,
        "media_validator": _accept_nonempty_media,
    }

    render_voiceover_preview(**common)
    first_calls = client.submits
    states = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "voiceover/clips").glob("*.tts.json")
    ]
    render_voiceover_preview(**common)

    assert states
    assert {state["status"] for state in states} == {"failed"}
    assert client.submits == first_calls * 2


def test_ambiguous_doubao_submit_is_not_retried_automatically(tmp_path: Path):
    episode = plan_episode("林澈推开门。", "tts_ambiguous", target_shots=1)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")

    class TimedOutClient:
        def __init__(self):
            self.submits = 0

        def submit(self, text, **kwargs):
            self.submits += 1
            raise TimeoutError("submit timed out")

        def complete_task(self, *args, **kwargs):
            raise AssertionError("Ambiguous task cannot be completed without an ID")

    def fake_runner(command, **kwargs):
        target = (
            Path(command[command.index("-o") + 1])
            if command[0] == "say"
            else Path(command[-1])
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"rendered")

    client = TimedOutClient()
    common = {
        "episode": episode,
        "source_video_path": source,
        "output_path": tmp_path / "voiced.mp4",
        "work_dir": tmp_path / "voiceover",
        "config": {
            "workspace": str(tmp_path),
            "sources": {"openMontage": str(tmp_path / "OpenMontage")},
        },
        "process_env": {
            "TTS_PROVIDER": "doubao",
            "DOUBAO_SPEECH_API_KEY": "secret",
            "DOUBAO_SPEECH_VOICE_TYPE": "voice",
        },
        "doubao_client": client,
        "command_runner": fake_runner,
        "media_validator": _accept_nonempty_media,
    }

    render_voiceover_preview(**common)
    first_calls = client.submits
    render_voiceover_preview(**common)

    assert first_calls > 0
    assert client.submits == first_calls
