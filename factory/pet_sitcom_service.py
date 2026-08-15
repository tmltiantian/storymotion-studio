from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import pet_sitcom_audio_first
from . import pet_sitcom_audio_probe
from . import pet_sitcom_generation
from . import pet_sitcom_review
from . import pet_sitcom_sound


class PublicSurface:
    """Allowlisted, live view over a specialist implementation module."""

    def __init__(
        self,
        module: Any,
        names: tuple[str, ...],
        **extra: Any,
    ) -> None:
        object.__setattr__(self, "_module", module)
        object.__setattr__(
            self,
            "_names",
            {name.removeprefix("_"): name for name in names},
        )
        object.__setattr__(self, "_extra", extra)

    def __getattr__(self, name: str) -> Any:
        if name in self._extra:
            return self._extra[name]
        implementation_name = self._names.get(name)
        if implementation_name is None:
            raise AttributeError(name)
        return getattr(self._module, implementation_name)

    def __setattr__(self, name: str, value: Any) -> None:
        implementation_name = self._names.get(name)
        if implementation_name is None:
            raise AttributeError(name)
        setattr(self._module, implementation_name, value)


def _surface(module: Any, names: tuple[str, ...], **extra: Any) -> PublicSurface:
    return PublicSurface(module, names, **extra)


@dataclass(frozen=True)
class PetSitcomServices:
    generation: PublicSurface
    review: PublicSurface
    sound: PublicSurface
    audio_first: PublicSurface
    audio_probe: PublicSurface


PET_SITCOM_SERVICES = PetSitcomServices(
    generation=_surface(
        pet_sitcom_generation,
        (
            "ANCHOR_REVIEW_SCHEMA",
            "ANCHOR_STATE_SCHEMA",
            "IMAGE_MODEL",
            "IMAGE_PROVIDER",
            "IMAGE_SIZE",
            "PET_CONTINUITY_SCHEMA",
            "PET_LOCAL_RECUT_SCHEMA",
            "PET_RETRY_SUFFIXES",
            "PET_SELECTION_SCHEMA",
            "PET_SHOT_GENERATION_SCHEMA",
            "VIDEO_MODEL",
            "VIDEO_PROVIDER",
            "_ANCHOR_REVIEW_FIELDS",
            "_PET_CONTINUITY_FIELDS",
            "_PET_LOCAL_RECUT_FIELDS",
            "_PET_PROVENANCE_FIELDS",
            "_anchor_hashes",
            "_anchor_jobs",
            "_anchor_state_path",
            "_hash_payload",
            "_pet_candidate_path",
            "_pet_candidate_state_path",
            "_pet_continuity_frame_path",
            "_pet_continuity_state_path",
            "_pet_gateway_report_path",
            "_pet_shot_prompt",
            "_validate_candidate_for_selection",
            "build_pet_drive_audio",
        ),
    ),
    review=_surface(
        pet_sitcom_review,
        (
            "FINAL_EVIDENCE_SCHEMA",
            "OWNER_NATIVE_AUDIO_REVIEW_SCHEMA",
            "OWNER_REVIEW_METHOD",
            "SHOT_EVIDENCE_SCHEMA",
            "SHOT_REVIEW_SCHEMA",
            "SOURCE_EVIDENCE_SCHEMA",
            "_AUTOMATION_LIMITATIONS",
            "_FINAL_MANIFEST_FIELDS",
            "_MOUTH_SHOTS",
            "_OWNER_RECORD_FIELDS",
            "_OWNER_TOP_FIELDS",
            "_PAW_SHOTS",
            "_PROP_SHOTS",
            "_QC_TOP_FIELDS",
            "_SELECTION_ENTRY_FIELDS",
            "_SELECTION_TOP_FIELDS",
            "_SHOT_EVIDENCE_FIELDS",
            "_SHOT_REVIEW_TOP_FIELDS",
            "_SOURCE_MANIFEST_FIELDS",
            "_continuity_edges",
            "_evidence_root",
            "_final_manifest_path",
            "_iso",
            "_selected_source_chain",
            "_source_manifest_path",
            "_validate_continuity_item",
            "_validate_final_sequence",
            "_validate_incremental_continuity",
            "_validate_incremental_props",
            "_validate_mouth_timing_record",
            "_validate_optional_incremental_sequence",
            "_validate_qc_document",
            "_validate_qc_record",
            "_validate_sequence_group",
            "_validate_shot_review_record",
            "_validate_source_sequence",
        ),
    ),
    sound=_surface(
        pet_sitcom_sound,
        (
            "CHANNELS",
            "FINAL_DURATION_SECONDS",
            "MINIMUM_MUSIC_SAMPLE_RATE",
            "MUSIC_APPROVAL_SCHEMA",
            "SAMPLE_RATE",
            "SOUND_DESIGN_SCHEMA",
            "_APPROVAL_FIELDS",
            "_DURATION_TOLERANCE_SECONDS",
            "_MANIFEST_APPROVAL_FIELDS",
            "_SOURCE_FIELDS",
            "_STEM_FIELDS",
            "_STEM_NAMES",
            "_TOP_LEVEL_FIELDS",
            "_binding_base",
            "_binding_sha256",
            "_json_hash",
            "_sound_config",
            "_stem_durations",
            "_stem_path",
            "_stems_content_root",
            "_valid_iso_timestamp",
            "music_approval_path",
        ),
    ),
    audio_first=_surface(
        pet_sitcom_audio_first,
        (
            "AUDIO_FIRST_SCHEMA",
            "DIALOGUE_TAIL_SECONDS",
            "DRIVE_AUDIO_STATE_SCHEMA",
            "MINIMUM_DURATION_SECONDS",
            "PET_VOICES",
            "PLAN_SCHEMA_VERSION",
            "_asset_from_record",
            "_drive_signature",
            "_plan_hash",
            "_shot_start_times",
            "_speech_output",
        ),
    ),
    audio_probe=_surface(
        pet_sitcom_audio_probe,
        (
            "PROBE_FRAME_TIMESTAMPS",
            "PROBE_MODEL",
            "PROBE_REVIEW_GATES",
            "PROBE_REVIEW_SCHEMA",
            "PROBE_SCHEMA",
            "PROBE_SOURCE_SHOT_ID",
            "_CAPABILITIES",
            "_OUTCOME_FIELDS",
            "_hash_text",
            "_probe_prompt",
            "_validate_inconclusive_task",
            "_validate_review_timing",
            "build_pet_drive_audio",
        ),
        module_name=pet_sitcom_audio_probe.__name__,
    ),
)
