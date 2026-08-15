import re
from pathlib import Path

from factory.pet_sitcom_service import PET_SITCOM_SERVICES


def test_factory_cli_does_not_reach_into_pet_specialist_private_symbols() -> None:
    source = Path("factory_cli.py").read_text(encoding="utf-8")

    assert not re.search(r"pet_sitcom_[a-z_]+_module\._[A-Za-z0-9_]", source)


def test_pet_sitcom_service_exposes_stable_public_inspection_groups() -> None:
    assert callable(PET_SITCOM_SERVICES.generation.anchor_jobs)
    assert callable(PET_SITCOM_SERVICES.review.validate_qc_document)
    assert callable(PET_SITCOM_SERVICES.sound.sound_config)
    assert callable(PET_SITCOM_SERVICES.audio_first.plan_hash)
    assert callable(PET_SITCOM_SERVICES.audio_probe.probe_prompt)
