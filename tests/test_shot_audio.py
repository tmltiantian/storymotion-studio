from pathlib import Path

from factory.dialogue_assets import DialogueAudioAsset, DialogueAudioManifest
from factory.file_io import sha256_file
from factory.novel_planner import plan_episode
from factory.schema import Character, DialogueLine, Episode, Shot
from factory.shot_audio import write_shot_audio_assets


def test_write_shot_audio_assets_extracts_only_on_screen_dialogue_shots(
    tmp_path: Path,
) -> None:
    episode = plan_episode(
        "林澈说，快走。苏眠看向门外。",
        "shot_audio",
        target_shots=2,
    )
    source = tmp_path / "voiceover.m4a"
    source.write_bytes(b"audio")
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b"wav")

    assets = write_shot_audio_assets(
        episode,
        source,
        tmp_path / "shots",
        command_runner=fake_run,
    )

    talking = [
        shot
        for shot in episode.shots
        if any(line.speaker_id != "narrator" for line in shot.dialogue)
    ]
    assert set(assets) == {shot.id for shot in talking}
    assert len(calls) == len(talking)
    assert all("-ss" in command and "-t" in command for command in calls)
    assert all(path.is_file() for path in assets.values())


def test_write_shot_audio_assets_uses_validated_dialogue_asset_as_legacy_alias(
    tmp_path: Path,
) -> None:
    episode = Episode(
        project_id="shot_audio_alias",
        title="Shot audio alias",
        language="en",
        style="motion comic",
        target_aspect_ratio="9:16",
        target_resolution="1080x1920",
        characters=[Character("wukong", "Wukong", "lead", "alert", "armor", "low")],
        shots=[
            Shot(
                "s01",
                1,
                "Gate",
                "Wukong speaks.",
                "A gate.",
                "static",
                4.0,
                "tense",
                dialogue=[DialogueLine("wukong", "Open the gate.")],
                character_ids=["wukong"],
            )
        ],
    )
    asset_path = tmp_path / "s01.dialogue_01.wav"
    asset_path.write_bytes(b"final dialogue audio")
    asset = DialogueAudioAsset(
        dialogue_id="s01.dialogue_01",
        speaker_id="wukong",
        path=str(asset_path),
        sha256=sha256_file(asset_path),
        duration_seconds=0.25,
        voice_id="wukong-voice",
    )
    manifest = DialogueAudioManifest(
        assets=(asset,),
        path=str(tmp_path / "dialogue_audio_manifest.json"),
        voiceover_audio="",
        voiceover_sha256="",
    )

    aliases = write_shot_audio_assets(
        episode,
        tmp_path / "missing.m4a",
        tmp_path / "unused",
        dialogue_manifest=manifest,
    )

    assert aliases == {"s01": asset_path}
