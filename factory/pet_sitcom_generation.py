from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from . import gateway_video_batch as _gateway_video_batch
from .gateway_image import GatewayImageClient, is_valid_image_file
from .gateway_video import GatewayVideoClient, is_valid_mp4_file
from .gateway_video_batch import render_gateway_video_single
from .media_validation import probe_media
from .pet_sitcom import IMAGE_MODEL, VIDEO_MODEL, PetSitcomPlan
from .pet_sitcom_audio_first import (
    build_pet_drive_audio,
    load_pet_speech_assets,
)


class PetSitcomGenerationError(RuntimeError):
    pass


IMAGE_PROVIDER = "gateway"
VIDEO_PROVIDER = "gateway"
IMAGE_SIZE = "1440x2560"
MOUTH_TEST_DURATION = 6
MOUTH_TEST_RATIO = "9:16"
MOUTH_TEST_RESOLUTION = "1080p"
ANCHOR_STATE_SCHEMA = "motion-comic-factory.pet-sitcom-anchor.v1"
ANCHOR_REVIEW_SCHEMA = "motion-comic-factory.pet-sitcom-anchor-review.v1"
MOUTH_TEST_SCHEMA = "motion-comic-factory.pet-sitcom-mouth-test.v1"
MOUTH_REVIEW_SCHEMA = "motion-comic-factory.pet-sitcom-mouth-review.v1"
MOUTH_TERMINAL_SCHEMA = "motion-comic-factory.pet-sitcom-mouth-terminal.v1"
PET_SHOT_GENERATION_SCHEMA = "motion-comic-factory.pet-sitcom-shots.v1"
PET_LOCAL_RECUT_SCHEMA = "motion-comic-factory.pet-sitcom-local-recut.v1"
PET_SELECTION_SCHEMA = "motion-comic-factory.pet-sitcom-selection.v1"
PET_CONTINUITY_SCHEMA = "motion-comic-factory.pet-sitcom-continuity-frame.v1"
_PET_PROVENANCE_BINDING_FIELDS = (
    "schema_version",
    "shot_id",
    "candidate_number",
    "provider",
    "model",
    "base_prompt_sha256",
    "prompt_sha256",
    "retry_reason",
    "retry_suffix",
    "reference_paths",
    "reference_sha256",
    "dependency_video_sha256",
    "source_tts_sha256",
    "reference_audio_path",
    "reference_audio_sha256",
    "generation_duration_seconds",
    "generate_audio",
)
_PET_PROVENANCE_FIELDS = frozenset(
    (*_PET_PROVENANCE_BINDING_FIELDS, "gateway_report_path", "video_sha256", "provider_success")
)
_PET_LOCAL_RECUT_FIELDS = frozenset(
    (
        *_PET_PROVENANCE_FIELDS,
        "source_candidates",
        "recipe",
        "recipe_sha256",
    )
)
_PET_LOCAL_RECUT_SOURCE_FIELDS = frozenset(
    {
        "candidate_number",
        "video_path",
        "video_sha256",
        "start_seconds",
        "end_seconds",
        "video_filter",
    }
)
_PET_SHOT_ONE_REACTION_RECUT_RECIPE = {
    "schema_version": PET_LOCAL_RECUT_SCHEMA,
    "shot_id": "shot_01",
    "candidate_number": 6,
    "segments": [
        {
            "candidate_number": 4,
            "start_seconds": 0.0,
            "end_seconds": 3.4,
            "video_filter": "",
        },
        {
            "candidate_number": 5,
            "start_seconds": 3.4,
            "end_seconds": 6.0,
            "video_filter": "scale=1404:2496,crop=1080:1920:162:0",
        },
    ],
    "transition": "motivated_hard_cut_from_empty_bag_wide_to_two_cat_reaction_closeup",
    "output_width": 1080,
    "output_height": 1920,
    "output_fps": 24,
    "output_duration_seconds": 6.0,
    "audio_streams": 0,
}
_PET_SHOT_THREE_DIALOGUE_RECUT_RECIPE = {
    "schema_version": PET_LOCAL_RECUT_SCHEMA,
    "shot_id": "shot_03",
    "candidate_number": 4,
    "segments": [
        {
            "source_shot_id": "shot_02",
            "candidate_number": 2,
            "start_seconds": 0.0,
            "end_seconds": 0.55,
            "video_filter": "scale=2160:3840,crop=1080:1920:1080:1360",
        },
        {
            "source_shot_id": "shot_03",
            "candidate_number": 2,
            "start_seconds": 0.75,
            "end_seconds": 2.05,
            "video_filter": "",
        },
        {
            "source_shot_id": "shot_02",
            "candidate_number": 2,
            "start_seconds": 0.6,
            "end_seconds": 3.2,
            "video_filter": "scale=2160:3840,crop=1080:1920:1080:1360",
        },
        {
            "source_shot_id": "shot_03",
            "candidate_number": 2,
            "start_seconds": 4.45,
            "end_seconds": 5.35,
            "video_filter": "",
        },
        {
            "source_shot_id": "shot_02",
            "candidate_number": 2,
            "start_seconds": 2.35,
            "end_seconds": 4.0,
            "video_filter": "scale=2700:4800,crop=1080:1920:1500:1760",
        },
    ],
    "transition": "dialogue_shot_reverse_shot_with_offscreen_second_phrase",
    "output_width": 1080,
    "output_height": 1920,
    "output_fps": 24,
    "output_duration_seconds": 7.0,
    "audio_streams": 1,
}
_PET_SHOT_FOUR_DIALOGUE_RECUT_RECIPE = {
    "schema_version": PET_LOCAL_RECUT_SCHEMA,
    "shot_id": "shot_04",
    "candidate_number": 3,
    "segments": [
        {
            "source_shot_id": "shot_03",
            "candidate_number": 4,
            "start_seconds": 5.39,
            "end_seconds": 6.04,
            "video_filter": "",
        },
        {
            "source_shot_id": "shot_04",
            "candidate_number": 2,
            "start_seconds": 1.585,
            "end_seconds": 4.175,
            "video_filter": "",
        },
        {
            "source_shot_id": "shot_03",
            "candidate_number": 4,
            "start_seconds": 6.04,
            "end_seconds": 7.0,
            "video_filter": "",
        },
    ],
    "transition": "closeup_reply_retimed_to_exact_drive_audio",
    "output_width": 1080,
    "output_height": 1920,
    "output_fps": 24,
    "output_duration_seconds": 4.2,
    "audio_streams": 1,
}
_PET_SHOT_FIVE_DIALOGUE_RECUT_RECIPE = {
    "schema_version": PET_LOCAL_RECUT_SCHEMA,
    "shot_id": "shot_05",
    "candidate_number": 3,
    "segments": [
        {
            "source_shot_id": "shot_05",
            "candidate_number": 2,
            "start_seconds": 0.4,
            "end_seconds": 0.95,
            "video_filter": "",
        },
        {
            "source_shot_id": "shot_05",
            "candidate_number": 2,
            "start_seconds": 3.5,
            "end_seconds": 7.596,
            "video_filter": (
                "crop=540:960:540:480,scale=1080:1920"
            ),
        },
        {
            "source_shot_id": "shot_05",
            "candidate_number": 2,
            "start_seconds": 4.6,
            "end_seconds": 7.254,
            "video_filter": (
                "crop=540:960:0:480,scale=1080:1920"
            ),
        },
    ],
    "transition": "establishing_to_speaker_close_to_reaction_close",
    "output_width": 1080,
    "output_height": 1920,
    "output_fps": 24,
    "output_duration_seconds": 7.3,
    "audio_streams": 1,
}
_PET_SHOT_SIX_SECURITY_RECUT_RECIPE = {
    "schema_version": PET_LOCAL_RECUT_SCHEMA,
    "shot_id": "shot_06",
    "candidate_number": 4,
    "segments": [
        {
            "source_shot_id": "shot_06",
            "candidate_number": 3,
            "start_seconds": 0.0,
            "end_seconds": 6.1,
            "video_filter": "noise=c0s=12:c0f=t+u",
        },
    ],
    "transition": "locked_security_replay_with_luma_sensor_grain",
    "output_width": 1080,
    "output_height": 1920,
    "output_fps": 24,
    "output_duration_seconds": 6.1,
    "audio_streams": 0,
}
_PET_SHOT_SEVEN_TAIL_RECUT_RECIPE = {
    "schema_version": PET_LOCAL_RECUT_SCHEMA,
    "shot_id": "shot_07",
    "candidate_number": 3,
    "segments": [
        {
            "source_shot_id": "shot_07",
            "candidate_number": 1,
            "start_seconds": 0.0,
            "end_seconds": 4.8,
            "video_filter": "noise=c0s=12:c0f=t+u",
        },
    ],
    "transition": "attached_tail_tip_replay_with_luma_sensor_grain",
    "output_width": 1080,
    "output_height": 1920,
    "output_fps": 24,
    "output_duration_seconds": 4.8,
    "audio_streams": 0,
}
_PET_SHOT_EIGHT_EVIDENCE_RECUT_RECIPE = {
    "schema_version": PET_LOCAL_RECUT_SCHEMA,
    "shot_id": "shot_08",
    "candidate_number": 6,
    "segments": [
        {
            "source_shot_id": "shot_08",
            "candidate_number": 5,
            "start_seconds": 1.1,
            "end_seconds": 1.69,
            "video_filter": "",
        },
        {
            "source_shot_id": "shot_08",
            "candidate_number": 5,
            "start_seconds": 1.78,
            "end_seconds": 7.25,
            "video_filter": "setpts=0.789762340036563*PTS",
        },
        {
            "source_shot_id": "shot_08",
            "candidate_number": 1,
            "start_seconds": 3.0,
            "end_seconds": 5.09,
            "video_filter": "crop=400:711:680:300,scale=1080:1920",
        },
    ],
    "transition": "clean_performance_retimed_with_tracked_mouth_corner_evidence",
    "mouth_window_seconds": [0.59, 4.91],
    "fleck_visible_until_seconds": 4.91,
    "fleck_count": 2,
    "fleck_patch": {
        "width": 18,
        "height": 12,
        "primary_rgba": [92, 62, 37, 184],
        "secondary_rgba": [118, 82, 48, 168],
    },
    "tracking_keyframes": [
        {"time_seconds": 0.0, "x": 270, "y": 935},
        {"time_seconds": 0.59, "x": 275, "y": 935},
        {"time_seconds": 4.91, "x": 259, "y": 925},
    ],
    "output_width": 1080,
    "output_height": 1920,
    "output_fps": 24,
    "output_duration_seconds": 7.0,
    "audio_streams": 1,
}
_PET_SHOT_NINE_EVIDENCE_RECUT_RECIPE = {
    "schema_version": PET_LOCAL_RECUT_SCHEMA,
    "shot_id": "shot_09",
    "candidate_number": 6,
    "segments": [
        {
            "source_shot_id": "shot_09",
            "candidate_number": 2,
            "start_seconds": 0.0,
            "end_seconds": 2.9,
            "video_filter": "setpts=0.879310344827586*PTS",
        },
        {
            "source_shot_id": "shot_09",
            "candidate_number": 2,
            "start_seconds": 2.9,
            "end_seconds": 4.45,
            "video_filter": "setpts=1.04193548387097*PTS",
        },
        {
            "source_shot_id": "shot_09",
            "candidate_number": 2,
            "start_seconds": 4.625,
            "end_seconds": 5.96,
            "video_filter": "crop=500:889:580:300,scale=1080:1920",
        },
    ],
    "transition": "mirror_push_retimed_to_dialogue_then_doubao_reaction_closeup",
    "mouth_window_seconds": [2.55, 4.165],
    "removal_segment_indexes": [0, 1],
    "removal_mask": {
        "width": 1080,
        "height": 1920,
        "center_x": 386,
        "center_y": 706,
        "radius_x": 30,
        "radius_y": 50,
    },
    "fleck_visible_until_seconds": 4.165,
    "fleck_count": 2,
    "fleck_patch": {
        "width": 18,
        "height": 12,
        "primary_rgba": [92, 62, 37, 184],
        "secondary_rgba": [118, 82, 48, 168],
    },
    "tracking_keyframes": [
        {"time_seconds": 0.0, "x": 402, "y": 686},
        {"time_seconds": 2.55, "x": 403, "y": 687},
        {"time_seconds": 4.165, "x": 403, "y": 688},
    ],
    "output_width": 1080,
    "output_height": 1920,
    "output_fps": 24,
    "output_duration_seconds": 5.5,
    "audio_streams": 1,
}
_PET_SHOT_TEN_EVIDENCE_RECUT_RECIPE = {
    "schema_version": PET_LOCAL_RECUT_SCHEMA,
    "shot_id": "shot_10",
    "candidate_number": 6,
    "segments": [
        {
            "source_shot_id": "shot_10",
            "candidate_number": 3,
            "start_seconds": 0.0,
            "end_seconds": 0.416667,
            "video_filter": "setpts=1.68*PTS,framerate=fps=24",
        },
        {
            "source_shot_id": "shot_10",
            "candidate_number": 3,
            "start_seconds": 0.416667,
            "end_seconds": 2.833333,
            "video_filter": (
                "setpts=1.09620703448276*PTS,framerate=fps=24"
            ),
        },
        {
            "source_shot_id": "shot_10",
            "candidate_number": 3,
            "start_seconds": 2.833333,
            "end_seconds": 3.416667,
            "video_filter": (
                "setpts=1.28714228571429*PTS,framerate=fps=24"
            ),
        },
    ],
    "transition": "clean_mirror_performance_retimed_to_dialogue_and_blink",
    "mouth_window_seconds": [0.7, 3.349167],
    "blink_window_seconds": [3.55, 4.1],
    "fleck_visible_until_seconds": 4.1,
    "fleck_count": 2,
    "fleck_patch": {
        "width": 18,
        "height": 12,
        "primary_rgba": [92, 62, 37, 184],
        "secondary_rgba": [118, 82, 48, 168],
    },
    "tracking_keyframes": [
        {"time_seconds": 0.0, "x": 360, "y": 994},
        {"time_seconds": 0.7, "x": 330, "y": 947},
        {"time_seconds": 3.349167, "x": 302, "y": 920},
        {"time_seconds": 4.1, "x": 302, "y": 920},
    ],
    "output_width": 1080,
    "output_height": 1920,
    "output_fps": 24,
    "output_duration_seconds": 4.1,
    "audio_streams": 1,
}
_PET_CONTINUITY_FIELDS = frozenset(
    {
        "schema_version",
        "source_video_path",
        "source_video_sha256",
        "source_video_duration_seconds",
        "edit_duration_seconds",
        "timestamp_seconds",
        "extracted_at",
        "frame_sha256",
    }
)
PET_RETRY_SUFFIXES = {
    "identity": "Keep both exact reference cats unchanged in markings, eyes, face, and body proportions.",
    "paw_anatomy": "Keep all visible paws anatomically feline, grounded, separate, and free of fusion or extra toes.",
    "mouth_anatomy": "Keep the designated speaker's mouth feline and restrained, with no human lips or oversized teeth.",
    "wrong_speaker": "Only the designated cat may speak; the other cat remains vocally silent with reaction-only motion.",
    "continuity": (
        "Match the approved action and required prop state exactly; an empty container "
        "must show a completely empty interior with no intact food pieces. Match the "
        "previous ending frame's cat positions, tails, props, light direction, and room "
        "geometry at the start when a previous frame exists."
    ),
}
_PET_SHOT_RETRY_DETAILS = {
    ("shot_02", "continuity"): (
        "Replace the static hold with one continuous silent reaction exchange. "
        "Match the previous close reaction pose at the first frame. During the "
        "first second Naitang slow-blinks once, then turns his eyes and chin toward "
        "Doubao without sliding his body. Doubao answers with one ear swivel, a "
        "brief side-eye toward Naitang, and a small grounded weight shift between "
        "his front paws. They return their eyes toward camera at slightly different "
        "times while their chests keep visibly breathing through the final frame. "
        "Both mouths stay naturally closed. No identical pose may last 0.25 seconds, "
        "and no movement may snap, loop, or move the locked camera."
    ),
    ("shot_03", "continuity"): (
        "Synchronize Naitang to the supplied canonical dialogue without shifting it: "
        "keep his mouth naturally closed before the first syllable, then begin "
        "restrained mouth motion at 0.55 seconds with no added lead-in. Match visible "
        "jaw openings to the spoken phrases and finish by 5.14 seconds, after which "
        "Naitang keeps his mouth closed. Doubao keeps his mouth fully closed for the "
        "entire clip and reacts only with a blink, ear swivel, and small grounded head "
        "turn. Preserve continuous natural body motion through breathing and weight "
        "settling; Naitang raises one forepaw gradually during the second sentence and "
        "sets it down without snapping. No identical pose may last 0.25 seconds. Keep "
        "the camera locked and preserve the previous room geometry and cat positions."
    ),
    ("shot_04", "continuity"): (
        "Preserve the previous ending as one continuous close reaction shot of "
        "Doubao. Do not cut to a wide kitchen or back to a reference frame. Keep the "
        "bag completely offscreen and preserve the same sofa, floor, warm light, and "
        "Doubao markings. Naitang remains offscreen. Doubao keeps his mouth closed "
        "before the supplied line, then begin restrained mouth motion at 0.65 seconds "
        "and finish by 1.61 seconds. Keep his mouth fully closed after 1.61 seconds. "
        "After speaking, Doubao shifts his eye focus toward offscreen Naitang, then "
        "glances down-left toward the offscreen bag with one small ear swivel and "
        "continuous breathing. No identical pose may last 0.25 seconds. Keep the "
        "camera locked and do not add text, packaging, or another cut."
    ),
    ("shot_05", "continuity"): (
        "Use one continuous locked two-cat medium shot on the same living-room "
        "axis as the previous Doubao close-up. Widen only at this shot boundary "
        "to reveal Naitang screen-left and Doubao screen-right beside the same "
        "sofa and wood floor; do not replace the room with an empty kitchen "
        "doorway and do not cut again. Doubao keeps his mouth closed before "
        "0.55 seconds, speaks continuously with restrained feline jaw motion "
        "from 0.55 through 4.65 seconds, and keeps his mouth fully closed after "
        "4.65 seconds. Naitang never mouths the dialogue: he avoids eye contact "
        "with a small ear turn, keeps his mouth closed through 4.65 seconds, "
        "then performs exactly one quick natural lip lick between 5.10 and "
        "5.40 seconds and closes his mouth again. Both cats maintain continuous "
        "breathing, independent eye focus, ear motion, and one small grounded "
        "weight shift; no pose may remain perceptually identical for 0.25 "
        "seconds. Preserve approved markings, warm frame-left daylight, feline "
        "paws, and realistic body scale. No camera movement, extra animal, "
        "package, text, subtitle, watermark, or generated speech."
    ),
    ("shot_06", "continuity"): (
        "Cut at the shot boundary to one fixed floor-level security-camera "
        "view aimed across the same kitchen wood floor toward the cabinet "
        "toe-kick. Do not repeat the doorway portrait composition. Show the "
        "same beige freeze-dried treat pouch with a silver inner lining from "
        "the earlier evidence shot, already torn open and completely empty: "
        "no pellets, intact treats, crumbs, or readable packaging text. No cat, "
        "person, paw, or tail may enter during this shot; the orange tail is "
        "reserved for the next replay shot. Between 0.40 and 1.60 seconds the "
        "empty pouch skids about fifteen centimeters, rotates slightly, and "
        "crinkles to a stop beside the cabinet base. Its loose top flap keeps "
        "fluttering irregularly in a gentle airflow through the end while a "
        "soft window-light shadow drifts slowly across the floor. Preserve "
        "subtle changing security-sensor grain so no frame is perceptually "
        "identical for 0.25 seconds. Keep the camera mechanically locked, room "
        "geometry and frame-left light unchanged, and generate no speech, "
        "vocalization, subtitle, timestamp, logo, watermark, or audio track."
    ),
    ("shot_06", "wrong_speaker"): (
        "Keep Doubao visible and speaking with restrained feline mouth motion. Naitang "
        "stays silent with a closed mouth, avoids eye contact, and makes only one brief "
        "natural lip lick after Doubao finishes."
    ),
    ("shot_07", "continuity"): (
        "Continue the exact same fixed floor-level security-camera frame as shot_06, "
        "aimed across the same kitchen floor toward the same cabinet toe-kick. The "
        "opened empty pouch remains beside the cabinet in the identical position and "
        "shape. Render no full cat, head, torso, leg, or paw. Exactly one orange striped "
        "furry tail enters from frame right between 0.80 and 1.10 seconds, travels in a "
        "single continuous right-to-left arc close to the floor, and exits frame left "
        "by 3.80 seconds. The tail remains visibly connected to one cat whose entire "
        "body remains offscreen; the tail root stays clipped by the right or lower frame "
        "edge until the final tip leaves at frame left. Never show both tail ends inside "
        "the image. The tail must not detach, coil into a circle, reverse, loop, teleport, "
        "or become a second animal. The tail does not touch, drag, or move the pouch. "
        "Keep no second tail and zero food, pellets, crumbs, text, or audio. "
        "Preserve a mechanically locked camera and subtle changing sensor grain so "
        "motion remains continuous without a frozen hold."
    ),
    ("shot_08", "continuity"): (
        "Use one continuous locked medium-close shot on the same kitchen axis. Keep "
        "Naitang screen-left and Doubao screen-right with their approved identities "
        "and grounded paws. Show exactly three to five tiny irregular beige flakes "
        "attached to Naitang's right mouth corner and adjacent whisker roots. Each "
        "flake is smaller than the width of one whisker root, never a cube or pellet, "
        "and all flakes remain attached in identical positions from first frame to "
        "last despite jaw motion. Render zero cubes, pellets, treats, or crumbs on the "
        "floor, paws, chest, or under the chin. Naitang keeps his mouth fully closed "
        "before 0.59 seconds, uses restrained syllable-level jaw motion from 0.59 "
        "through 4.91 seconds, and keeps his mouth fully closed after 4.91 seconds. "
        "Doubao's mouth stays closed for the entire shot while he reacts only with "
        "one blink, one ear swivel, and a small eye-focus shift. Preserve continuous "
        "breathing and subtle independent motion so no identical pose may last 0.25 "
        "seconds. Do not add another cut, camera movement, generated text, extra food, "
        "speech, or vocalization."
    ),
    ("shot_09", "continuity"): (
        "Use one continuous locked two-cat shot on the same kitchen axis. Place one "
        "small round mirror mounted on a low stable stand beside Doubao and in front "
        "of Naitang. From 0.00-0.40 seconds both mouths remain fully closed while "
        "Doubao prepares his grounded left forepaw. From 0.40-2.20 seconds Doubao's "
        "grounded left forepaw contacts and pushes the mirror and stand about fifteen "
        "centimeters toward Naitang; it never floats, teleports, flips, or moves before "
        "paw contact. From 2.20-2.55 seconds the mirror settles completely while "
        "Doubao plants his paw. From 2.55-4.17 seconds only Doubao speaks with restrained "
        "syllable-level feline jaw motion. Naitang's mouth remains closed while his eyes "
        "hold on Doubao. After 4.17 seconds both mouths remain fully closed; Naitang "
        "lowers his eyes and chin toward the mirror, then makes one small embarrassed "
        "ear turn. Keep exactly two tiny matte-brown flecks fixed in Naitang's right "
        "mouth-corner fur in every Naitang frame, with no ring, beard, cluster, cube, "
        "pellet, or raised chunk. Preserve continuous breathing, eye focus, and ear "
        "motion so there is no perceptually identical pose longer than 0.25 seconds. "
        "Keep the mirror and stand visible and stationary after settling, and add no "
        "extra object, reflection animal, text, speech, or camera movement."
    ),
    ("shot_10", "continuity"): (
        "Retain the successful locked front three-quarter composition, both exact cat "
        "identities and positions, and the same stationary round mirror and low metal "
        "stand. Correct only the evidence scale and final reaction timing. Replace both "
        "round brown disks on Naitang's muzzle with exactly two tiny flat irregular "
        "matte-brown flecks, each five to eight image pixels wide at 1080p and smaller "
        "than one quarter of Naitang's visible pupil diameter. Keep the two flecks "
        "separate and fixed between the same right mouth-corner whisker roots; they have "
        "no circular outline, raised volume, hanging edge, cube, pellet, beard, ring, or "
        "cluster. Naitang keeps his mouth fully closed through 0.75 seconds, speaks only "
        "from 0.75 through 3.35 seconds, and keeps his mouth fully closed after 3.35 "
        "seconds. Doubao keeps his mouth fully closed for the entire clip and completes "
        "one clearly visible open-to-closed-to-open slow blink between 3.55 and 3.95 "
        "seconds, inside the 4.10-second final edit window. Keep continuous breathing "
        "and restrained independent ear motion with no identical pose longer than 0.25 "
        "seconds. The mirror remains completely stationary and no food appears elsewhere."
    ),
    ("shot_14", "continuity"): (
        "After Naitang finishes the exact line once, keep his mouth fully closed while "
        "both cats maintain continuous natural micro-motion through the final frame: "
        "subtle breathing, one slow blink, and one restrained ear twitch. Keep the "
        "mirror and the same mouth-corner crumbs visible and fixed in place. Keep the "
        "camera locked. Never freeze or repeat identical frames for 0.50 seconds."
    ),
}
_PET_SHOT_THIRD_RETRY_DETAILS = {
    ("shot_01", "continuity"): (
        "Keep the opened empty treat bag in exactly one fixed shape and position on "
        "the floor; the treat bag stays completely motionless and never inflates, "
        "collapses, slides, or changes its printed surface. Keep the camera locked. "
        "Both cats maintain continuous natural micro-motion through breathing, "
        "staggered blinks, and small independent ear twitches. During the owner's "
        "question, they complete one natural head turn from the bag toward camera at "
        "slightly different times, then keep subtle breathing through the final frame. "
        "Never freeze or repeat identical frames for 0.50 seconds."
    ),
    ("shot_03", "continuity"): (
        "Follow these supplied-audio windows exactly. 0.55-2.05 seconds: Naitang "
        "speaks with restrained syllable-level jaw openings. 2.05-2.63 seconds: "
        "Naitang closes his mouth for the audible pause. 2.63-4.09 seconds: Naitang "
        "speaks continuously and gradually begins the forepaw gesture. 4.09-4.45 "
        "seconds: Naitang closes his mouth for the second pause while the forepaw "
        "continues moving. 4.45-5.14 seconds: Naitang speaks and settles the forepaw. "
        "After 5.14 seconds his mouth remains fully closed. No speaking window may "
        "contain a closed-mouth freeze. During every pause and after dialogue, keep "
        "both cats alive through breathing, eye focus, and independent ear motion. "
        "Doubao's mouth remains closed in every window. Never duplicate or hold an "
        "identical frame for 0.35 seconds."
    ),
    ("shot_06", "continuity"): (
        "Retain the successful empty floor-level cabinet composition from the "
        "previous attempt, but keep the pouch opening folded down toward the "
        "floor so its silver interior is either visibly bare or fully collapsed. "
        "Render zero pellets, kibble, cubes, treats, crumbs, or powder anywhere "
        "inside the pouch or on the floor. From 0.20-2.00 seconds the empty pouch "
        "skids and rotates gently; from 2.00-4.80 seconds it continues a slower "
        "irregular drift while the loose folded edge flexes. From 4.80-6.10 "
        "seconds the pouch stays beside the cabinet but its top edge keeps "
        "fluttering and the soft window shadow keeps moving, so it never becomes "
        "a still photograph before the edit. Keep the camera locked and keep all "
        "cats, tails, paws, people, text, and audio completely absent."
    ),
    ("shot_06", "wrong_speaker"): (
        "Reframe before dialogue so Doubao's face and mouth stay clearly visible on the "
        "right half of the frame while Naitang remains visible farther back on the left. "
        "Do not show Doubao only from behind or let a foreground tail block Doubao's face. "
        "Doubao stays in place through the full spoken line. Naitang keeps a closed mouth "
        "throughout the line, then may make one brief natural lip lick only after it ends."
    ),
    ("shot_07", "continuity"): (
        "Retain the successful shot_06 low cabinet framing and empty pouch position. "
        "Naitang walks just below the lower frame edge from right to left, fully outside "
        "the image. His orange striped tail root stays continuously clipped by the lower "
        "frame edge, and that hidden-body attachment point travels steadily from "
        "lower-right toward lower-left. Never show both ends of the tail inside the image "
        "and never render the whole tail as a detached object. The visible tail arches "
        "upward from that hidden attachment point with feline thickness, taper, fur, "
        "inertia, and continuous curvature; it does not crawl, slither, coil, or lead "
        "with both ends. Do not show either full cat or let the tail touch the pouch. "
        "The pouch remains completely stationary and the clean floor stays free of "
        "every pellet and crumb. Maintain subtle changing luminance sensor noise after "
        "the tail exits; never freeze or repeat identical frames for 0.35 seconds."
    ),
    ("shot_08", "continuity"): (
        "Reframe to a tight chest-up close-up with Naitang dominant on the left and "
        "Doubao's reacting face still visible farther back on the right; crop the floor "
        "completely out of frame. Replace every food chunk with exactly two to four "
        "flat light-brown dust specks the size of grains of sand. They have no "
        "three-dimensional cube, pellet, dangling piece, or treat shape and remain "
        "fixed to the right whisker roots in every frame. From 0.00-0.59 seconds "
        "Naitang keeps his mouth closed while breathing. From 0.59-4.91 seconds he "
        "speaks with restrained phrase-level jaw motion, adding one slow blink, one "
        "ear swivel, a small chin lift, and a gradual eye turn toward Doubao. After "
        "4.91 seconds his mouth stays fully closed while breathing and settling his "
        "chin. Doubao performs one side-eye and independent ear turn without opening "
        "his mouth. Never hold an identical pose for 0.25 seconds, never move the "
        "camera, and render no food anywhere except the fixed dust specks."
    ),
    ("shot_09", "continuity"): (
        "retain the successful upright round mirror on its low metal stand, the same "
        "two-cat composition, and the physically grounded paw push from the previous "
        "attempt; do not redesign, flatten, remove, or teleport the mirror. Remove the "
        "single dangling brown chunk from Naitang's muzzle. Replace it with exactly two "
        "flat matte-brown flecks, each smaller than one quarter of Naitang's visible "
        "pupil diameter, fixed in the same right mouth-corner whisker-root fur. They "
        "have no raised volume, hanging edge, pellet, cube, beard, or cluster. Doubao "
        "keeps his mouth closed through 2.55 seconds, speaks from 2.55 through 4.17 "
        "seconds with restrained syllable-level motion, and keeps his mouth fully "
        "closed after 4.17 seconds. Naitang's mouth remains closed for the entire shot. "
        "The mirror remains stationary after 2.20 seconds. Naitang lowers his eyes and "
        "chin after Doubao finishes while both cats continue breathing and moving their "
        "ears subtly, with no identical pose longer than 0.25 seconds."
    ),
    ("shot_10", "continuity"): (
        "Reframe immediately to a front three-quarter close-up of Naitang's face, with "
        "his right mouth corner and whisker roots large, sharp, and unobstructed for the "
        "entire clip. The mouth corner cannot turn away from camera. Three to five tiny "
        "light-brown crumbs contrast clearly against his white muzzle and remain attached "
        "through every jaw movement. Keep Doubao silent and farther back."
    ),
    ("shot_14", "continuity"): (
        "Keep the same two light-brown fragments attached beneath Naitang's mouth in "
        "exactly the same visible positions from the first frame through the final frame. "
        "They must never fade, fall, move, or disappear after dialogue. Preserve the "
        "successful locked composition and continuous natural micro-motion from the "
        "previous retry, including breathing, a slow blink, and a restrained ear twitch."
    ),
}
_PET_SHOT_FOURTH_RETRY_DETAILS = {
    ("shot_01", "continuity"): (
        "Stage one continuous six-second reaction while the opened empty treat bag "
        "remains completely motionless in one fixed shape and position. From "
        "0.00-1.20 seconds, both cats sniff toward the bag while Naitang gradually "
        "raises his chin and their ears swivel independently. From 1.20-3.00 seconds, "
        "Doubao makes one short grounded half-step and settles his weight while Naitang "
        "shifts naturally through his shoulders without sliding his paws. From "
        "3.00-4.60 seconds, both cats turn from the bag toward the camera at slightly "
        "different moments. Through the final frame, preserve visible breathing, "
        "staggered blinks, and restrained independent ear motion. No cat may hold an "
        "identical pose longer than 0.25 seconds; interpolate every action continuously "
        "and never snap between poses. Keep the camera locked and do not morph the cats, "
        "floor, furniture, or bag."
    ),
    ("shot_07", "continuity"): (
        "Use one unbroken tail pass only. At 0.00-0.80 seconds show the unchanged low "
        "security view and empty pouch. From 0.80-1.10 seconds one attached orange tail "
        "base enters at frame right. From 1.10-3.50 seconds the same tail advances "
        "steadily left in a low natural S-curve without stopping or reversing. By "
        "3.80 seconds the tip exits frame left. Through 4.80 seconds hold the same "
        "locked empty view with changing sensor grain, not duplicated frames. No cat "
        "body, paw, head, second tail, food, pouch motion, camera motion, text, or audio."
    ),
    ("shot_08", "continuity"): (
        "In one continuous locked shot, preserve the successful continuous acting and "
        "dialogue timing from the previous attempt, but remove every round disk, pellet, "
        "cube, or raised food piece. Keep the forehead, brow, eyelids, cheeks, nose "
        "bridge, nose leather, chin, chest, paws, and floor completely clean. Show "
        "exactly two tiny matte-brown flecks only in the fur immediately beside "
        "Naitang's right mouth corner. Keep each fleck smaller than one quarter of his "
        "visible pupil diameter. They are flat irregular discolorations between hairs, "
        "not separate objects pasted onto the face, and remain fixed to the same hairs "
        "through every jaw movement. Naitang keeps his mouth closed before 0.59 seconds, "
        "speaks from 0.59 through 4.91 seconds with restrained syllable-level jaw motion, "
        "and keeps his mouth fully closed after 4.91 seconds. Doubao remains silent with "
        "a closed mouth while both cats maintain continuous breathing, eye focus, and "
        "independent ear motion."
    ),
    ("shot_10", "continuity"): (
        "Retain the successful front close-up and visible muzzle evidence. Naitang says "
        "the exact line exactly once between 0.40 and 1.70 seconds. From 1.70 seconds "
        "through the end, his mouth remains fully closed during a silent embarrassed "
        "hold; do not repeat, paraphrase, meow, or add any vocalization. All muzzle crumbs "
        "stay attached in the same positions through the entire clip."
    ),
}
_PET_SHOT_EIGHT_CLEAN_PERFORMANCE_PROMPT = (
    "Reference 1 is Naitang's immutable orange-and-white character sheet. "
    "Reference 2 is Doubao's immutable black-and-white character sheet. "
    "Reference 3 defines the kitchen geometry and warm frame-left daylight. "
    "Reference 4 defines only the preceding room axis and light direction; do not "
    "copy its low security-camera framing. Generate a clean performance layer for "
    "deterministic tracked evidence compositing. Use one tight chest-up two-cat "
    "composition with Naitang dominant on frame left and Doubao reacting farther "
    "back on frame right; crop the floor completely out of frame. Keep both cats' "
    "faces, fur, whiskers, and bodies completely clean and unmarked, with no "
    "accessory or object attached anywhere. Naitang keeps his mouth closed from "
    "0.00 through 0.59 seconds, then speaks from 0.59 through 4.91 seconds with "
    "restrained syllable-level feline jaw motion and says exactly "
    "\"橘色尾巴那么多，不能因为颜色就怀疑一只无辜的小猫。\" His mouth remains "
    "fully closed from 4.91 seconds through the final frame. Give Naitang continuous "
    "breathing, one slow blink, one small chin lift, and one restrained ear swivel. "
    "Doubao remains silent with his mouth fully closed while making one side-eye, "
    "one independent ear turn, and continuous breathing. Keep no perceptually "
    "identical pose longer than 0.25 seconds. Use a mechanically locked camera, "
    "continuous natural motion, realistic feline anatomy, stable identities, and "
    "no cut, zoom, optical-flow look, duplicated body part, extra animal, person, "
    "text, subtitle, logo, watermark, added speech, or vocalization."
)
_PET_SHOT_TEN_CLEAN_PERFORMANCE_PROMPT = (
    "Reference 1 is Naitang's immutable orange-and-white character sheet. "
    "Reference 2 is Doubao's immutable black-and-white character sheet. "
    "Reference 3 defines the kitchen geometry and warm frame-left daylight. "
    "Reference 4 defines only the preceding kitchen axis, Doubao's frame-right "
    "eyeline, and the continued existence of the mirror; do not copy its tight "
    "Doubao reaction crop. Generate one clean performance layer for the final "
    "comedy button; cut exactly once at this shot boundary to a mechanically "
    "locked front three-quarter medium close-up of Naitang on frame left, with "
    "Doubao visible farther back on frame right. The small round mirror on its "
    "low metal stand remains visible and completely stationary in the lower "
    "middle of frame. Keep Naitang's right mouth-corner whisker-root fur facing "
    "camera, sharp, and unobstructed. Show exactly two tiny flat matte-brown "
    "flecks fixed to that same white mouth-corner fur. Each fleck is smaller "
    "than one quarter of Naitang's visible pupil diameter, has no raised volume "
    "or hanging edge, and never becomes a cube, pellet, beard, ring, or cluster. "
    "Naitang keeps his mouth fully closed from 0.00 through 0.75 seconds while "
    "he glances down at the mirror and then toward camera. He speaks from 0.75 "
    "through 3.35 seconds with restrained syllable-level feline jaw motion and "
    "says exactly \"证据也可能是后来粘上去的。\" His mouth remains fully closed "
    "from 3.35 seconds through 5.00 seconds while he performs one embarrassed "
    "ear turn and continuous breathing. Doubao remains silent with his mouth "
    "fully closed for the entire shot and performs one slow blink after Naitang "
    "finishes. The owner laugh will be added only in post-production; generate "
    "no owner voice, laugh, second line, repeated line, meow, music, or extra "
    "vocalization. Keep both cats alive through breathing, eye focus, and "
    "independent ear motion with no perceptually identical pose longer than "
    "0.25 seconds. Keep a mechanically locked camera. Preserve realistic feline "
    "anatomy, grounded paws, approved "
    "markings, stable room geometry, and the warm frame-left light. Add no "
    "extra animal, person, food elsewhere, text, subtitle, logo, watermark, "
    "camera movement, digital zoom, optical-flow motion, morph, or unexplained cut."
)
_PET_SHOT_TEN_CLEAN_TRACKING_PLATE_PROMPT = (
    "Reference 1 is Naitang's immutable orange-and-white character sheet. "
    "Reference 2 is Doubao's immutable black-and-white character sheet. "
    "Reference 3 defines the kitchen geometry and warm frame-left daylight. "
    "Reference 4 defines only the preceding kitchen axis, Doubao's frame-right "
    "eyeline, and the continued existence of the mirror; do not copy its tight "
    "Doubao reaction crop. Generate a clean performance plate for deterministic "
    "tracked evidence compositing. Cut exactly once at this shot boundary to a "
    "mechanically locked front three-quarter medium close-up of Naitang on frame "
    "left, with Doubao visible farther back on frame right. The small round mirror "
    "on its low metal stand remains visible and completely stationary in the lower "
    "middle of frame. The mirror glass remains completely clean and reflects only "
    "the physically corresponding room. Naitang's entire face and muzzle remain "
    "completely clean. Show zero crumbs, flecks, pellets, cubes, spots, stains, or "
    "food anywhere in the image; the two evidence flecks will be added later in "
    "post-production. Keep Naitang's right mouth-corner whisker-root fur facing "
    "camera, sharp, unobstructed, and visually trackable. Naitang keeps his mouth "
    "fully closed from 0.00 through 0.75 seconds while he glances down at the "
    "mirror and then toward camera. He speaks from 0.75 through 3.35 seconds with "
    "restrained syllable-level feline jaw motion and says exactly "
    "\"证据也可能是后来粘上去的。\" His mouth remains fully closed from 3.35 seconds "
    "through 5.00 seconds while he performs one embarrassed ear turn and continuous "
    "breathing. Doubao remains silent with his mouth fully closed for the entire "
    "shot and performs one complete open-to-closed-to-open slow blink between 3.55 "
    "and 3.95 seconds, inside the 4.10-second final edit window. The owner laugh "
    "will be added only in post-production; generate no owner voice, laugh, second "
    "line, repeated line, meow, music, or extra vocalization. Keep both cats alive "
    "through breathing, eye focus, and independent ear motion with no perceptually "
    "identical pose longer than 0.25 seconds. Keep a mechanically locked camera, "
    "realistic feline anatomy, grounded paws, approved markings, stable room "
    "geometry, and the warm frame-left light. Add no extra animal, person, text, "
    "subtitle, logo, watermark, camera movement, digital zoom, optical-flow motion, "
    "morph, or unexplained cut."
)
_PET_SHOT_FIFTH_RETRY_DETAILS = {
    ("shot_01", "continuity"): (
        "Keep the successful fixed empty bag and continuous sniff-to-reaction motion, "
        "but never settle into a static camera-facing portrait. Complete the first "
        "staggered turn by 2.40 seconds. From 2.40-4.20 seconds, Naitang shifts weight "
        "gradually between his front paws while his shoulders and chest follow naturally; "
        "at the same time Doubao turns briefly toward Naitang and then back toward camera. "
        "From 4.20-6.00 seconds, Doubao takes one slow grounded half-step and settles it "
        "while Naitang makes one small chin turn, with their blinks and ear swivels at "
        "different times. Continuous breathing and independent motion remains visible "
        "through the last frame. Never repeat or hold an identical pose for 0.25 seconds, "
        "never snap between poses, and never move or morph the bag, floor, furniture, or "
        "locked camera."
    ),
    ("shot_10", "continuity"): (
        "Use a tight front three-quarter close-up in which Naitang's right mouth corner "
        "faces the camera continuously and never turns into a side profile. Show exactly "
        "three separate light-brown crumbs attached to the white fur at that mouth corner, "
        "large and sharp enough to read clearly in a phone-sized frame; keep their positions "
        "fixed and show no food on the floor. Naitang says the exact line once, creating "
        "exactly one audible region and one restrained feline mouth-motion cycle between "
        "0.35 and 1.65 seconds. There is no second mouth-motion cycle, repeated line, "
        "paraphrase, meow, or other vocalization. From 1.70 seconds to the end, Naitang "
        "holds the same visible crumbs with a fully closed mouth while Doubao remains silent "
        "and closed-mouthed in the background."
    ),
}

_ANCHOR_REVIEW_FIELDS = (
    "naitang_consistent_across_panels",
    "doubao_consistent_across_panels",
    "cats_remain_clearly_distinct",
    "scenes_are_empty_and_clean",
    "scenes_share_home_design",
)
_MOUTH_REVIEW_FIELDS = (
    "correct_naitang_identity",
    "photorealistic_feline_face",
    "no_human_mouth_deformation",
    "correct_speaker",
    "exact_intelligible_line",
    "visible_mouth_and_jaw_movement",
    "subjective_start_pause_end_alignment",
    "no_extra_animal_text_or_watermark",
)
_RETRY_REASONS = {
    "mouth_anatomy",
    "wrong_speaker",
    "lip_timing",
    "identity",
    "extra_content",
}
MOUTH_RETRY_SUFFIXES = {
    "mouth_anatomy": (
        "Correct only feline mouth anatomy: keep a small natural cat mouth, restrained jaw "
        "opening, and no human lips, teeth, or facial deformation."
    ),
    "wrong_speaker": (
        "Correct speaker assignment: only Naitang produces the spoken audio and mouth movement; "
        "keep every other subject vocally silent and reaction-only."
    ),
    "lip_timing": (
        "Correct lip timing: keep the mouth closed during both 0.20-second pauses and align each "
        "subtle jaw movement only to the audible Chinese syllables."
    ),
    "identity": (
        "Correct identity: preserve Naitang exactly as reference 1 with the same orange-and-white "
        "markings, round face, amber eyes, white left mouth-corner fur, and body proportions."
    ),
    "extra_content": (
        "Remove all extra content: show only Naitang in the referenced living room with no other "
        "animal, person, voice, text, subtitle, logo, watermark, or overlay."
    ),
}
_MOUTH_PROVENANCE_FIELDS = (
    "schema_version",
    "candidate_number",
    "provider",
    "model",
    "duration",
    "ratio",
    "resolution",
    "generate_audio",
    "base_prompt_sha256",
    "prompt_sha256",
    "retry_reason",
    "retry_suffix",
    "reference_paths",
    "reference_sha256",
    "gateway_report_path",
    "video_sha256",
    "provider_success",
)


def generate_pet_sitcom_anchors(
    plan: PetSitcomPlan,
    *,
    image_client: GatewayImageClient,
    allow_network: bool = False,
    anchor_names: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Generate the two immutable character sheets and two empty scene anchors."""
    _validate_plan(plan)
    jobs = _requested_anchor_jobs(plan, anchor_names)
    report: dict[str, Any] = {
        "schema_version": ANCHOR_STATE_SCHEMA,
        "planned_count": len(jobs),
        "executed": False,
        "success": False,
        "completed_count": 0,
        "reused_count": 0,
        "allow_network": allow_network,
        "anchors": [],
        "errors": [],
    }
    if not allow_network:
        report["blocked_reasons"] = ["Live gateway image generation is disabled."]
        report["anchors"] = [_planned_anchor_report(job) for job in jobs]
        _write_report(_anchor_report_path(plan), report, image_client)
        return report

    _require_client_model(image_client, IMAGE_MODEL, "image")
    for job in jobs:
        output = job["output"]
        signature = _hash_payload(
            {
                "provider": IMAGE_PROVIDER,
                "model": IMAGE_MODEL,
                "prompt": job["prompt"],
                "size": IMAGE_SIZE,
                "candidate_number": 1,
            }
        )
        if _anchor_is_reusable(output, signature):
            report["reused_count"] += 1
            report["completed_count"] += 1
            report["anchors"].append(_anchor_report(job, signature, "reused"))
            continue

        report["executed"] = True
        try:
            _generate_anchor(job, image_client, signature)
        except Exception as exc:  # Provider errors must be persisted without secrets.
            report["errors"].append(
                {"name": job["name"], "error": _sanitize_error(exc, image_client)}
            )
            break
        report["completed_count"] += 1
        report["anchors"].append(_anchor_report(job, signature, "generated"))

    report["success"] = not report["errors"] and report["completed_count"] == len(jobs)
    if report["success"] and _all_anchors_are_current(plan):
        _write_anchor_review_template(plan)
    _write_report(_anchor_report_path(plan), report, image_client)
    return report


def approve_pet_anchors(plan: PetSitcomPlan) -> dict[str, Any]:
    """Bind a completed human anchor review to the current PNG source hashes."""
    _validate_plan(plan)
    review_path = _anchor_review_path(plan)
    review = _read_json_object(review_path)
    if (
        review.get("schema_version") != ANCHOR_REVIEW_SCHEMA
        or review.get("completed") is not True
    ):
        raise PetSitcomGenerationError(
            "Anchor review must be completed before approval."
        )
    if any(review.get(field) is not True for field in _ANCHOR_REVIEW_FIELDS):
        raise PetSitcomGenerationError(
            "Anchor review requires every boolean check to be true."
        )
    current_hashes = _anchor_hashes(plan)
    if review.get("source_hashes") != current_hashes:
        raise PetSitcomGenerationError(
            "Anchor review source hashes no longer match current PNG files."
        )
    review["approved"] = True
    _write_atomic_json(review_path, review)
    return {
        "approved": True,
        "review_path": review_path,
        "source_hashes": current_hashes,
    }


def generate_pet_mouth_test(
    plan: PetSitcomPlan,
    *,
    video_client: GatewayVideoClient,
    allow_network: bool = False,
    candidate_number: int = 1,
    retry_reason: str = "",
) -> dict[str, Any]:
    """Render the gated six-second Naitang mouth test through the gateway helper."""
    raise PetSitcomGenerationError(
        "The legacy mouth-test API was replaced by the approved audio-drive probe."
    )
    _validate_plan(plan)
    _require_approved_anchors(plan)
    _validate_mouth_candidate(plan, candidate_number, retry_reason)
    _require_client_model(video_client, VIDEO_MODEL, "video")

    candidate = _mouth_candidate_path(plan, candidate_number)
    gateway_report = _mouth_gateway_report_path(candidate)
    report_path = _mouth_report_path(candidate)
    base_prompt = _mouth_test_prompt("")
    prompt = _mouth_test_prompt(retry_reason)
    provenance = _mouth_candidate_provenance(
        plan, candidate_number, retry_reason, base_prompt, prompt, gateway_report
    )
    report: dict[str, Any] = {
        "schema_version": MOUTH_TEST_SCHEMA,
        "candidate_number": candidate_number,
        "candidate_path": str(candidate),
        "report_path": str(report_path),
        "gateway_report_path": str(gateway_report),
        "state_path": str(_mouth_candidate_state_path(candidate)),
        "provider": VIDEO_PROVIDER,
        "model": VIDEO_MODEL,
        "duration": MOUTH_TEST_DURATION,
        "ratio": MOUTH_TEST_RATIO,
        "resolution": MOUTH_TEST_RESOLUTION,
        "generate_audio": True,
        "planned_count": 1,
        "executed": False,
        "success": False,
        "resumed_count": 0,
        "reused_count": 0,
        "allow_network": allow_network,
        "references": _reference_reports(
            [plan.characters[0].reference_path, plan.scenes[0].anchor_path]
        ),
        **{
            key: provenance[key]
            for key in (
                "retry_reason",
                "retry_suffix",
                "base_prompt_sha256",
                "prompt_sha256",
            )
        },
        "errors": [],
    }
    if not allow_network:
        report["blocked_reasons"] = ["Live gateway video generation is disabled."]
        _write_report(report_path, report, video_client)
        return _report_with_paths(report)

    try:
        if _mouth_candidate_is_reusable(candidate, provenance):
            report["reused_count"] = 1
            report["success"] = True
            report["mp4_sha256"] = _sha256_file(candidate)
            _write_mouth_review_template(plan, candidate_number)
        else:
            overwrite = _prepare_mouth_candidate_regeneration(
                plan,
                candidate_number,
                candidate,
                prompt,
                provenance,
                video_client,
            )
            gateway_result = render_gateway_video_single(
                prompt,
                candidate,
                video_client,
                gateway_report,
                images=[plan.characters[0].reference_path, plan.scenes[0].anchor_path],
                duration=MOUTH_TEST_DURATION,
                ratio=MOUTH_TEST_RATIO,
                resolution=MOUTH_TEST_RESOLUTION,
                generate_audio=True,
                allow_network=True,
                overwrite=overwrite,
                report_sanitizer=lambda value: _sanitize_report(
                    value, _client_secrets(video_client)
                ),
            )
            report["executed"] = bool(gateway_result.get("executed"))
            report["resumed_count"] = int(gateway_result.get("resumed_count") or 0)
            report["success"] = bool(gateway_result.get("success")) and candidate.is_file()
            if candidate.is_file():
                report["mp4_sha256"] = _sha256_file(candidate)
            if report["success"]:
                provenance["provider_success"] = True
                provenance["video_sha256"] = _sha256_file(candidate)
                _write_report(
                    _mouth_candidate_state_path(candidate),
                    provenance,
                    video_client,
                    output_dir=plan.output_dir,
                )
                gateway_payload = _read_json_object(gateway_report)
                gateway_payload.setdefault(
                    "schema_version", "motion-comic-factory.gateway-video.v2"
                )
                gateway_payload.setdefault("provider", VIDEO_PROVIDER)
                gateway_payload.setdefault("model", VIDEO_MODEL)
                gateway_payload["success"] = True
                gateway_payload["pet_sitcom_mouth_provenance"] = provenance
                _write_report(
                    gateway_report,
                    gateway_payload,
                    video_client,
                    output_dir=plan.output_dir,
                )
                _write_mouth_review_template(plan, candidate_number)
            if not report["success"]:
                report["errors"].append(
                    {"error": "Gateway mouth-test render did not complete."}
                )
    except Exception as exc:  # Preserve the gateway task state for a later resume.
        report["errors"].append({"error": _sanitize_error(exc, video_client)})
    finally:
        _sanitize_json_file(gateway_report, _client_secrets(video_client))
        _write_report(report_path, report, video_client)
    return _report_with_paths(report)


def approve_pet_mouth_test(plan: PetSitcomPlan) -> dict[str, Any]:
    """Validate and return the current passing mouth-test review."""
    return require_approved_mouth_test(plan)


def require_approved_mouth_test(plan: PetSitcomPlan) -> dict[str, Any]:
    """Fail closed until a passing review remains bound to its candidate MP4."""
    raise PetSitcomGenerationError(
        "The legacy mouth-test API was replaced by the approved audio-drive probe."
    )
    _validate_plan(plan)
    _raise_if_mouth_workflow_stopped(plan)
    first_review = _completed_mouth_review(plan, 1)
    second_review = _completed_mouth_review(plan, 2)
    if second_review:
        _validate_mouth_review(plan, second_review, 2)
        if second_review.get("passed") is False:
            _persist_terminal_mouth_failure(plan, second_review)
            raise PetSitcomGenerationError(
                "candidate 2 failed; the mouth-test workflow is stopped."
            )
        if not first_review:
            raise PetSitcomGenerationError(
                "candidate 2 approval requires a failed candidate-1 review."
            )
        _validate_mouth_review(plan, first_review, 1)
        if first_review.get("passed") is not False:
            raise PetSitcomGenerationError(
                "candidate 2 approval requires a failed candidate-1 review."
            )
        _require_current_mouth_provenance(plan, 1, "")
        candidate_two_state = _require_current_mouth_provenance(
            plan, 2, str(first_review["retry_reason"])
        )
        if candidate_two_state.get("retry_reason") != first_review.get("retry_reason"):
            raise PetSitcomGenerationError(
                "candidate 2 provenance retry reason no longer matches candidate 1 review."
            )
        review = second_review
        candidate_number = 2
    elif first_review:
        _validate_mouth_review(plan, first_review, 1)
        _require_current_mouth_provenance(plan, 1, "")
        review = first_review
        candidate_number = 1
    else:
        raise PetSitcomGenerationError(
            "A passing mouth test review is required before production shots."
        )
    if review.get("passed") is not True:
        raise PetSitcomGenerationError(
            "A passing mouth test review is required before production shots."
        )
    if any(review.get(field) is not True for field in _MOUTH_REVIEW_FIELDS):
        raise PetSitcomGenerationError(
            "A passing mouth test requires every quality check to be true."
        )
    result = dict(review)
    result["approved"] = True
    _write_atomic_json(_mouth_review_path(plan, candidate_number), result)
    return result


def generate_pet_sitcom_shots(
    plan: PetSitcomPlan,
    *,
    video_client: GatewayVideoClient,
    allow_network: bool = False,
    shot_id: str | None = None,
    candidate_number: int = 1,
    retry_reason: str = "",
) -> dict[str, Any]:
    """Generate audio-bound shots from their declared continuity dependencies."""
    from .pet_sitcom_audio_probe import require_approved_pet_audio_probe

    require_approved_pet_audio_probe(plan)
    _validate_plan(plan)
    _require_client_model(video_client, VIDEO_MODEL, "video")
    shots = _requested_pet_shots(plan, shot_id)
    _validate_pet_candidate_request(shots, candidate_number, retry_reason)
    report: dict[str, Any] = {
        "schema_version": PET_SHOT_GENERATION_SCHEMA,
        "planned_count": len(shots),
        "executed": False,
        "success": False,
        "completed_count": 0,
        "reused_count": 0,
        "allow_network": allow_network,
        "shots": [],
        "errors": [],
    }
    if not allow_network:
        report["blocked_reasons"] = ["Live gateway video generation is disabled."]
        _write_report(
            plan.generation_report_path, report, video_client, output_dir=plan.output_dir
        )
        return report

    selections = _read_pet_selections(plan)
    for shot in shots:
        try:
            references = _pet_shot_references(plan, shot, selections)
            drive_audio, source_tts_sha256 = _pet_shot_audio_bindings(plan, shot)
        except Exception as exc:
            report["errors"].append(
                {
                    "shot_id": shot.shot_id,
                    "error": _sanitize_error(exc, video_client),
                }
            )
            break
        candidate = _pet_candidate_path(shot, candidate_number)
        gateway_report = _pet_gateway_report_path(candidate)
        prompt = _pet_shot_prompt(shot, candidate_number, retry_reason)
        provenance = _pet_candidate_provenance(
            shot,
            candidate_number,
            prompt,
            retry_reason,
            references,
            selections,
            drive_audio,
            source_tts_sha256,
        )
        state = _read_json_object(_pet_candidate_state_path(candidate))
        if _candidate_is_reusable(state, candidate, provenance):
            report["reused_count"] += 1
            report["completed_count"] += 1
            report["shots"].append(
                _pet_generation_entry(shot, candidate_number, candidate, provenance, "reused")
            )
        else:
            report["executed"] = True
            try:
                gateway_result = render_gateway_video_single(
                    prompt,
                    candidate,
                    video_client,
                    gateway_report,
                    images=references,
                    audio=drive_audio,
                    duration=shot.generation_duration_seconds,
                    ratio="9:16",
                    resolution="1080p",
                    generate_audio=drive_audio is not None,
                    allow_network=allow_network,
                    overwrite=False,
                    replace_stale=True,
                    report_sanitizer=lambda value: sanitize_pet_sitcom_report(
                        value, _client_secrets(video_client)
                    ),
                )
            except Exception as exc:  # Keep provider recovery evidence, without secrets.
                report["errors"].append(
                    {"shot_id": shot.shot_id, "error": _sanitize_error(exc, video_client)}
                )
                break
            finally:
                _sanitize_json_file(
                    gateway_report,
                    _client_secrets(video_client),
                    output_dir=plan.output_dir,
                )
            if not gateway_result.get("success") or not is_valid_mp4_file(candidate):
                report["errors"].append(
                    {
                        "shot_id": shot.shot_id,
                        "error": "Gateway video generation did not produce a valid completed clip.",
                    }
                )
                break
            provenance["provider_success"] = True
            provenance["gateway_report_path"] = str(gateway_report.resolve())
            provenance["video_sha256"] = _sha256_file(candidate)
            _write_report(
                _pet_candidate_state_path(candidate),
                provenance,
                video_client,
                output_dir=plan.output_dir,
            )
            gateway_payload = _read_json_object(gateway_report)
            gateway_payload["pet_sitcom_provenance"] = provenance
            _write_report(
                gateway_report,
                gateway_payload,
                video_client,
                output_dir=plan.output_dir,
            )
            report["completed_count"] += 1
            report["shots"].append(
                _pet_generation_entry(shot, candidate_number, candidate, provenance, "generated")
            )
        if candidate_number == 1 and not _has_current_retry_selection(
            plan, shot, selections
        ):
            try:
                selection = select_pet_shot_candidate(plan, shot.shot_id, candidate_number)
                selections = _selection_shots(selection)
            except PetSitcomGenerationError as exc:
                report["errors"].append({"shot_id": shot.shot_id, "error": str(exc)})
                break

    report["success"] = (
        not report["errors"]
        and len(report["shots"]) == len(shots)
        and report["completed_count"] == len(shots)
    )
    _write_report(
        plan.generation_report_path, report, video_client, output_dir=plan.output_dir
    )
    return report


def build_pet_shot_one_reaction_recut(
    plan: PetSitcomPlan,
    *,
    command_runner: Any = subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, Any]:
    """Build the hash-bound shot 01 wide-to-close reaction recovery cut."""
    from .pet_sitcom_audio_probe import require_approved_pet_audio_probe

    require_approved_pet_audio_probe(plan)
    _validate_plan(plan)
    shot = next(
        (item for item in plan.shots if item.shot_id == "shot_01"),
        None,
    )
    if shot is None or shot.generation_duration_seconds != 6:
        raise PetSitcomGenerationError(
            "The shot 01 reaction recut requires the approved six-second shot."
        )
    selections = _read_pet_selections(plan)
    references = _pet_shot_references(plan, shot, selections)
    drive_audio, source_tts_sha256 = _pet_shot_audio_bindings(plan, shot)
    if drive_audio is not None:
        raise PetSitcomGenerationError(
            "The shot 01 reaction recut must remain video-only."
        )
    source_candidates = _validated_pet_recut_sources(
        plan,
        shot,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
    )
    candidate = _pet_candidate_path(shot, 6)
    report_path = _pet_gateway_report_path(candidate)
    expected = _pet_local_recut_provenance(
        shot,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
        source_candidates,
    )
    state = _read_json_object(_pet_candidate_state_path(candidate))
    report = _read_json_object(report_path)
    if _local_recut_is_reusable(state, report, candidate, expected):
        return {
            "schema_version": PET_LOCAL_RECUT_SCHEMA,
            "success": True,
            "executed": False,
            "status": "reused",
            "shot_id": shot.shot_id,
            "candidate_number": 6,
            "video_path": str(candidate.resolve()),
            "video_sha256": state["video_sha256"],
            "report_path": str(report_path.resolve()),
        }

    _ensure_output_path(candidate, output_dir=plan.output_dir)
    temporary = _temporary_path(candidate, ".mp4")
    try:
        command = _pet_shot_one_recut_command(
            source_candidates,
            temporary,
            ffmpeg_bin=ffmpeg_bin,
        )
        command_runner(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        if not is_valid_mp4_file(temporary):
            raise PetSitcomGenerationError(
                "The local shot 01 reaction recut did not produce a valid MP4."
            )
        _fsync_file(temporary)
        os.replace(temporary, candidate)
        _fsync_directory(candidate.parent)
    except PetSitcomGenerationError:
        raise
    except Exception as exc:
        raise PetSitcomGenerationError(
            "The local shot 01 reaction recut failed."
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)

    completed = dict(expected)
    completed["provider_success"] = True
    completed["video_sha256"] = _sha256_file(candidate)
    _write_atomic_json(
        _pet_candidate_state_path(candidate),
        completed,
        output_dir=plan.output_dir,
    )
    _write_atomic_json(
        report_path,
        {
            "schema_version": PET_LOCAL_RECUT_SCHEMA,
            "success": True,
            "executed": True,
            "shot_id": shot.shot_id,
            "candidate_number": 6,
            "pet_sitcom_provenance": completed,
        },
        output_dir=plan.output_dir,
    )
    return {
        "schema_version": PET_LOCAL_RECUT_SCHEMA,
        "success": True,
        "executed": True,
        "status": "generated",
        "shot_id": shot.shot_id,
        "candidate_number": 6,
        "video_path": str(candidate.resolve()),
        "video_sha256": completed["video_sha256"],
        "report_path": str(report_path.resolve()),
    }


def build_pet_shot_three_dialogue_recut(
    plan: PetSitcomPlan,
    *,
    command_runner: Any = subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, Any]:
    """Build the hash-bound shot 03 dialogue/reaction recovery cut."""
    from .pet_sitcom_audio_probe import require_approved_pet_audio_probe

    require_approved_pet_audio_probe(plan)
    _validate_plan(plan)
    shot = next(
        (item for item in plan.shots if item.shot_id == "shot_03"),
        None,
    )
    if shot is None or shot.generation_duration_seconds != 7:
        raise PetSitcomGenerationError(
            "The shot 03 dialogue recut requires the approved seven-second shot."
        )
    selections = _read_pet_selections(plan)
    references = _pet_shot_references(plan, shot, selections)
    drive_audio, source_tts_sha256 = _pet_shot_audio_bindings(plan, shot)
    if drive_audio is None:
        raise PetSitcomGenerationError(
            "The shot 03 dialogue recut requires immutable drive audio."
        )
    source_candidates = _validated_pet_recut_sources(
        plan,
        shot,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
    )
    candidate_number = 4
    candidate = _pet_candidate_path(shot, candidate_number)
    report_path = _pet_gateway_report_path(candidate)
    expected = _pet_local_recut_provenance(
        shot,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
        source_candidates,
    )
    state = _read_json_object(_pet_candidate_state_path(candidate))
    report = _read_json_object(report_path)
    if _local_recut_is_reusable(state, report, candidate, expected):
        return {
            "schema_version": PET_LOCAL_RECUT_SCHEMA,
            "success": True,
            "executed": False,
            "status": "reused",
            "shot_id": shot.shot_id,
            "candidate_number": candidate_number,
            "video_path": str(candidate.resolve()),
            "video_sha256": state["video_sha256"],
            "report_path": str(report_path.resolve()),
        }

    _ensure_output_path(candidate, output_dir=plan.output_dir)
    temporary = _temporary_path(candidate, ".mp4")
    try:
        command = _pet_shot_three_recut_command(
            source_candidates,
            drive_audio,
            temporary,
            ffmpeg_bin=ffmpeg_bin,
        )
        command_runner(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        if not is_valid_mp4_file(temporary):
            raise PetSitcomGenerationError(
                "The local shot 03 dialogue recut did not produce a valid MP4."
            )
        _fsync_file(temporary)
        os.replace(temporary, candidate)
        _fsync_directory(candidate.parent)
    except PetSitcomGenerationError:
        raise
    except Exception as exc:
        raise PetSitcomGenerationError(
            "The local shot 03 dialogue recut failed."
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)

    completed = dict(expected)
    completed["provider_success"] = True
    completed["video_sha256"] = _sha256_file(candidate)
    _write_atomic_json(
        _pet_candidate_state_path(candidate),
        completed,
        output_dir=plan.output_dir,
    )
    _write_atomic_json(
        report_path,
        {
            "schema_version": PET_LOCAL_RECUT_SCHEMA,
            "success": True,
            "executed": True,
            "shot_id": shot.shot_id,
            "candidate_number": candidate_number,
            "pet_sitcom_provenance": completed,
        },
        output_dir=plan.output_dir,
    )
    return {
        "schema_version": PET_LOCAL_RECUT_SCHEMA,
        "success": True,
        "executed": True,
        "status": "generated",
        "shot_id": shot.shot_id,
        "candidate_number": candidate_number,
        "video_path": str(candidate.resolve()),
        "video_sha256": completed["video_sha256"],
        "report_path": str(report_path.resolve()),
    }


def build_pet_shot_four_dialogue_recut(
    plan: PetSitcomPlan,
    *,
    command_runner: Any = subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, Any]:
    """Build the hash-bound shot 04 retimed close-up recovery cut."""
    from .pet_sitcom_audio_probe import require_approved_pet_audio_probe

    require_approved_pet_audio_probe(plan)
    _validate_plan(plan)
    shot = next(
        (item for item in plan.shots if item.shot_id == "shot_04"),
        None,
    )
    if shot is None or shot.generation_duration_seconds != 5:
        raise PetSitcomGenerationError(
            "The shot 04 dialogue recut requires the approved five-second source shot."
        )
    selections = _read_pet_selections(plan)
    references = _pet_shot_references(plan, shot, selections)
    drive_audio, source_tts_sha256 = _pet_shot_audio_bindings(plan, shot)
    if drive_audio is None:
        raise PetSitcomGenerationError(
            "The shot 04 dialogue recut requires immutable drive audio."
        )
    source_candidates = _validated_pet_recut_sources(
        plan,
        shot,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
    )
    candidate_number = 3
    candidate = _pet_candidate_path(shot, candidate_number)
    report_path = _pet_gateway_report_path(candidate)
    expected = _pet_local_recut_provenance(
        shot,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
        source_candidates,
    )
    state = _read_json_object(_pet_candidate_state_path(candidate))
    report = _read_json_object(report_path)
    if _local_recut_is_reusable(state, report, candidate, expected):
        return {
            "schema_version": PET_LOCAL_RECUT_SCHEMA,
            "success": True,
            "executed": False,
            "status": "reused",
            "shot_id": shot.shot_id,
            "candidate_number": candidate_number,
            "video_path": str(candidate.resolve()),
            "video_sha256": state["video_sha256"],
            "report_path": str(report_path.resolve()),
        }

    _ensure_output_path(candidate, output_dir=plan.output_dir)
    temporary = _temporary_path(candidate, ".mp4")
    try:
        command = _pet_shot_three_recut_command(
            source_candidates,
            drive_audio,
            temporary,
            ffmpeg_bin=ffmpeg_bin,
            output_duration_seconds=shot.duration_seconds,
        )
        command_runner(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        if not is_valid_mp4_file(temporary):
            raise PetSitcomGenerationError(
                "The local shot 04 dialogue recut did not produce a valid MP4."
            )
        _fsync_file(temporary)
        os.replace(temporary, candidate)
        _fsync_directory(candidate.parent)
    except PetSitcomGenerationError:
        raise
    except Exception as exc:
        raise PetSitcomGenerationError(
            "The local shot 04 dialogue recut failed."
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)

    completed = dict(expected)
    completed["provider_success"] = True
    completed["video_sha256"] = _sha256_file(candidate)
    _write_atomic_json(
        _pet_candidate_state_path(candidate),
        completed,
        output_dir=plan.output_dir,
    )
    _write_atomic_json(
        report_path,
        {
            "schema_version": PET_LOCAL_RECUT_SCHEMA,
            "success": True,
            "executed": True,
            "shot_id": shot.shot_id,
            "candidate_number": candidate_number,
            "pet_sitcom_provenance": completed,
        },
        output_dir=plan.output_dir,
    )
    return {
        "schema_version": PET_LOCAL_RECUT_SCHEMA,
        "success": True,
        "executed": True,
        "status": "generated",
        "shot_id": shot.shot_id,
        "candidate_number": candidate_number,
        "video_path": str(candidate.resolve()),
        "video_sha256": completed["video_sha256"],
        "report_path": str(report_path.resolve()),
    }


def build_pet_shot_five_dialogue_recut(
    plan: PetSitcomPlan,
    *,
    command_runner: Any = subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, Any]:
    """Build the hash-bound shot 05 speaker/reaction recovery cut."""
    from .pet_sitcom_audio_probe import require_approved_pet_audio_probe

    require_approved_pet_audio_probe(plan)
    _validate_plan(plan)
    shot = next(
        (item for item in plan.shots if item.shot_id == "shot_05"),
        None,
    )
    if shot is None or shot.generation_duration_seconds != 8:
        raise PetSitcomGenerationError(
            "The shot 05 dialogue recut requires the approved eight-second source shot."
        )
    selections = _read_pet_selections(plan)
    references = _pet_shot_references(plan, shot, selections)
    drive_audio, source_tts_sha256 = _pet_shot_audio_bindings(plan, shot)
    if drive_audio is None:
        raise PetSitcomGenerationError(
            "The shot 05 dialogue recut requires immutable drive audio."
        )
    source_candidates = _validated_pet_recut_sources(
        plan,
        shot,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
    )
    candidate_number = 3
    candidate = _pet_candidate_path(shot, candidate_number)
    report_path = _pet_gateway_report_path(candidate)
    expected = _pet_local_recut_provenance(
        shot,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
        source_candidates,
    )
    state = _read_json_object(_pet_candidate_state_path(candidate))
    report = _read_json_object(report_path)
    if _local_recut_is_reusable(state, report, candidate, expected):
        return {
            "schema_version": PET_LOCAL_RECUT_SCHEMA,
            "success": True,
            "executed": False,
            "status": "reused",
            "shot_id": shot.shot_id,
            "candidate_number": candidate_number,
            "video_path": str(candidate.resolve()),
            "video_sha256": state["video_sha256"],
            "report_path": str(report_path.resolve()),
        }

    _ensure_output_path(candidate, output_dir=plan.output_dir)
    temporary = _temporary_path(candidate, ".mp4")
    try:
        command = _pet_shot_three_recut_command(
            source_candidates,
            drive_audio,
            temporary,
            ffmpeg_bin=ffmpeg_bin,
            output_duration_seconds=shot.duration_seconds,
        )
        command_runner(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        if not is_valid_mp4_file(temporary):
            raise PetSitcomGenerationError(
                "The local shot 05 dialogue recut did not produce a valid MP4."
            )
        _fsync_file(temporary)
        os.replace(temporary, candidate)
        _fsync_directory(candidate.parent)
    except PetSitcomGenerationError:
        raise
    except Exception as exc:
        raise PetSitcomGenerationError(
            "The local shot 05 dialogue recut failed."
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)

    completed = dict(expected)
    completed["provider_success"] = True
    completed["video_sha256"] = _sha256_file(candidate)
    _write_atomic_json(
        _pet_candidate_state_path(candidate),
        completed,
        output_dir=plan.output_dir,
    )
    _write_atomic_json(
        report_path,
        {
            "schema_version": PET_LOCAL_RECUT_SCHEMA,
            "success": True,
            "executed": True,
            "shot_id": shot.shot_id,
            "candidate_number": candidate_number,
            "pet_sitcom_provenance": completed,
        },
        output_dir=plan.output_dir,
    )
    return {
        "schema_version": PET_LOCAL_RECUT_SCHEMA,
        "success": True,
        "executed": True,
        "status": "generated",
        "shot_id": shot.shot_id,
        "candidate_number": candidate_number,
        "video_path": str(candidate.resolve()),
        "video_sha256": completed["video_sha256"],
        "report_path": str(report_path.resolve()),
    }


def build_pet_shot_six_security_recut(
    plan: PetSitcomPlan,
    *,
    command_runner: Any = subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, Any]:
    """Build the hash-bound shot 06 silent security-camera recovery cut."""
    from .pet_sitcom_audio_probe import require_approved_pet_audio_probe

    require_approved_pet_audio_probe(plan)
    _validate_plan(plan)
    shot = next(
        (item for item in plan.shots if item.shot_id == "shot_06"),
        None,
    )
    if shot is None or shot.generation_duration_seconds != 7:
        raise PetSitcomGenerationError(
            "The shot 06 security recut requires the approved seven-second source shot."
        )
    selections = _read_pet_selections(plan)
    references = _pet_shot_references(plan, shot, selections)
    drive_audio, source_tts_sha256 = _pet_shot_audio_bindings(plan, shot)
    if drive_audio is not None or source_tts_sha256:
        raise PetSitcomGenerationError(
            "The shot 06 security recut must remain source-silent."
        )
    source_candidates = _validated_pet_recut_sources(
        plan,
        shot,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
    )
    candidate_number = 4
    candidate = _pet_candidate_path(shot, candidate_number)
    report_path = _pet_gateway_report_path(candidate)
    expected = _pet_local_recut_provenance(
        shot,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
        source_candidates,
    )
    state = _read_json_object(_pet_candidate_state_path(candidate))
    report = _read_json_object(report_path)
    if _local_recut_is_reusable(state, report, candidate, expected):
        return {
            "schema_version": PET_LOCAL_RECUT_SCHEMA,
            "success": True,
            "executed": False,
            "status": "reused",
            "shot_id": shot.shot_id,
            "candidate_number": candidate_number,
            "video_path": str(candidate.resolve()),
            "video_sha256": state["video_sha256"],
            "report_path": str(report_path.resolve()),
        }

    _ensure_output_path(candidate, output_dir=plan.output_dir)
    temporary = _temporary_path(candidate, ".mp4")
    try:
        command = _pet_silent_recut_command(
            source_candidates,
            temporary,
            ffmpeg_bin=ffmpeg_bin,
            output_duration_seconds=shot.duration_seconds,
        )
        command_runner(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        if not is_valid_mp4_file(temporary):
            raise PetSitcomGenerationError(
                "The local shot 06 security recut did not produce a valid MP4."
            )
        _fsync_file(temporary)
        os.replace(temporary, candidate)
        _fsync_directory(candidate.parent)
    except PetSitcomGenerationError:
        raise
    except Exception as exc:
        raise PetSitcomGenerationError(
            "The local shot 06 security recut failed."
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)

    completed = dict(expected)
    completed["provider_success"] = True
    completed["video_sha256"] = _sha256_file(candidate)
    _write_atomic_json(
        _pet_candidate_state_path(candidate),
        completed,
        output_dir=plan.output_dir,
    )
    _write_atomic_json(
        report_path,
        {
            "schema_version": PET_LOCAL_RECUT_SCHEMA,
            "success": True,
            "executed": True,
            "shot_id": shot.shot_id,
            "candidate_number": candidate_number,
            "pet_sitcom_provenance": completed,
        },
        output_dir=plan.output_dir,
    )
    return {
        "schema_version": PET_LOCAL_RECUT_SCHEMA,
        "success": True,
        "executed": True,
        "status": "generated",
        "shot_id": shot.shot_id,
        "candidate_number": candidate_number,
        "video_path": str(candidate.resolve()),
        "video_sha256": completed["video_sha256"],
        "report_path": str(report_path.resolve()),
    }


def build_pet_shot_seven_tail_recut(
    plan: PetSitcomPlan,
    *,
    command_runner: Any = subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, Any]:
    """Build the hash-bound shot 07 attached-tail replay recovery cut."""
    from .pet_sitcom_audio_probe import require_approved_pet_audio_probe

    require_approved_pet_audio_probe(plan)
    _validate_plan(plan)
    shot = next(
        (item for item in plan.shots if item.shot_id == "shot_07"),
        None,
    )
    if shot is None or shot.generation_duration_seconds != 5:
        raise PetSitcomGenerationError(
            "The shot 07 tail recut requires the approved five-second source shot."
        )
    selections = _read_pet_selections(plan)
    references = _pet_shot_references(plan, shot, selections)
    drive_audio, source_tts_sha256 = _pet_shot_audio_bindings(plan, shot)
    if drive_audio is not None or source_tts_sha256:
        raise PetSitcomGenerationError(
            "The shot 07 tail recut must remain source-silent."
        )
    source_candidates = _validated_pet_recut_sources(
        plan,
        shot,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
    )
    candidate_number = 3
    candidate = _pet_candidate_path(shot, candidate_number)
    report_path = _pet_gateway_report_path(candidate)
    expected = _pet_local_recut_provenance(
        shot,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
        source_candidates,
    )
    state = _read_json_object(_pet_candidate_state_path(candidate))
    report = _read_json_object(report_path)
    if _local_recut_is_reusable(state, report, candidate, expected):
        return {
            "schema_version": PET_LOCAL_RECUT_SCHEMA,
            "success": True,
            "executed": False,
            "status": "reused",
            "shot_id": shot.shot_id,
            "candidate_number": candidate_number,
            "video_path": str(candidate.resolve()),
            "video_sha256": state["video_sha256"],
            "report_path": str(report_path.resolve()),
        }

    _ensure_output_path(candidate, output_dir=plan.output_dir)
    temporary = _temporary_path(candidate, ".mp4")
    try:
        command = _pet_silent_recut_command(
            source_candidates,
            temporary,
            ffmpeg_bin=ffmpeg_bin,
            output_duration_seconds=shot.duration_seconds,
        )
        command_runner(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        if not is_valid_mp4_file(temporary):
            raise PetSitcomGenerationError(
                "The local shot 07 tail recut did not produce a valid MP4."
            )
        _fsync_file(temporary)
        os.replace(temporary, candidate)
        _fsync_directory(candidate.parent)
    except PetSitcomGenerationError:
        raise
    except Exception as exc:
        raise PetSitcomGenerationError(
            "The local shot 07 tail recut failed."
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)

    completed = dict(expected)
    completed["provider_success"] = True
    completed["video_sha256"] = _sha256_file(candidate)
    _write_atomic_json(
        _pet_candidate_state_path(candidate),
        completed,
        output_dir=plan.output_dir,
    )
    _write_atomic_json(
        report_path,
        {
            "schema_version": PET_LOCAL_RECUT_SCHEMA,
            "success": True,
            "executed": True,
            "shot_id": shot.shot_id,
            "candidate_number": candidate_number,
            "pet_sitcom_provenance": completed,
        },
        output_dir=plan.output_dir,
    )
    return {
        "schema_version": PET_LOCAL_RECUT_SCHEMA,
        "success": True,
        "executed": True,
        "status": "generated",
        "shot_id": shot.shot_id,
        "candidate_number": candidate_number,
        "video_path": str(candidate.resolve()),
        "video_sha256": completed["video_sha256"],
        "report_path": str(report_path.resolve()),
    }


def build_pet_shot_eight_evidence_recut(
    plan: PetSitcomPlan,
    *,
    command_runner: Any = subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, Any]:
    """Build the hash-bound shot 08 retime and tracked-evidence recovery cut."""
    from .pet_sitcom_audio_probe import require_approved_pet_audio_probe

    require_approved_pet_audio_probe(plan)
    _validate_plan(plan)
    shot = next(
        (item for item in plan.shots if item.shot_id == "shot_08"),
        None,
    )
    if shot is None or shot.generation_duration_seconds != 8:
        raise PetSitcomGenerationError(
            "The shot 08 evidence recut requires the approved eight-second source shot."
        )
    selections = _read_pet_selections(plan)
    references = _pet_shot_references(plan, shot, selections)
    drive_audio, source_tts_sha256 = _pet_shot_audio_bindings(plan, shot)
    if drive_audio is None:
        raise PetSitcomGenerationError(
            "The shot 08 evidence recut requires immutable drive audio."
        )
    source_candidates = _validated_pet_recut_sources(
        plan,
        shot,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
    )
    candidate_number = 6
    candidate = _pet_candidate_path(shot, candidate_number)
    report_path = _pet_gateway_report_path(candidate)
    expected = _pet_local_recut_provenance(
        shot,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
        source_candidates,
    )
    state = _read_json_object(_pet_candidate_state_path(candidate))
    report = _read_json_object(report_path)
    if _local_recut_is_reusable(state, report, candidate, expected):
        return {
            "schema_version": PET_LOCAL_RECUT_SCHEMA,
            "success": True,
            "executed": False,
            "status": "reused",
            "shot_id": shot.shot_id,
            "candidate_number": candidate_number,
            "video_path": str(candidate.resolve()),
            "video_sha256": state["video_sha256"],
            "report_path": str(report_path.resolve()),
        }

    _ensure_output_path(candidate, output_dir=plan.output_dir)
    temporary = _temporary_path(candidate, ".mp4")
    fleck_patch = _temporary_path(candidate, ".flecks.png")
    try:
        _write_pet_evidence_fleck_patch(
            fleck_patch,
            expected["recipe"]["fleck_patch"],
        )
        command = _pet_shot_eight_evidence_recut_command(
            source_candidates,
            drive_audio,
            fleck_patch,
            expected["recipe"],
            temporary,
            ffmpeg_bin=ffmpeg_bin,
        )
        command_runner(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        if not is_valid_mp4_file(temporary):
            raise PetSitcomGenerationError(
                "The local shot 08 evidence recut did not produce a valid MP4."
            )
        _fsync_file(temporary)
        os.replace(temporary, candidate)
        _fsync_directory(candidate.parent)
    except PetSitcomGenerationError:
        raise
    except Exception as exc:
        raise PetSitcomGenerationError(
            "The local shot 08 evidence recut failed."
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)
        fleck_patch.unlink(missing_ok=True)

    completed = dict(expected)
    completed["provider_success"] = True
    completed["video_sha256"] = _sha256_file(candidate)
    _write_atomic_json(
        _pet_candidate_state_path(candidate),
        completed,
        output_dir=plan.output_dir,
    )
    _write_atomic_json(
        report_path,
        {
            "schema_version": PET_LOCAL_RECUT_SCHEMA,
            "success": True,
            "executed": True,
            "shot_id": shot.shot_id,
            "candidate_number": candidate_number,
            "pet_sitcom_provenance": completed,
        },
        output_dir=plan.output_dir,
    )
    return {
        "schema_version": PET_LOCAL_RECUT_SCHEMA,
        "success": True,
        "executed": True,
        "status": "generated",
        "shot_id": shot.shot_id,
        "candidate_number": candidate_number,
        "video_path": str(candidate.resolve()),
        "video_sha256": completed["video_sha256"],
        "report_path": str(report_path.resolve()),
    }


def build_pet_shot_nine_evidence_recut(
    plan: PetSitcomPlan,
    *,
    command_runner: Any = subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, Any]:
    """Build the hash-bound shot 09 mirror, dialogue, and clue recovery cut."""
    from .pet_sitcom_audio_probe import require_approved_pet_audio_probe

    require_approved_pet_audio_probe(plan)
    _validate_plan(plan)
    shot = next(
        (item for item in plan.shots if item.shot_id == "shot_09"),
        None,
    )
    if shot is None or shot.generation_duration_seconds != 6:
        raise PetSitcomGenerationError(
            "The shot 09 evidence recut requires the approved six-second source shot."
        )
    selections = _read_pet_selections(plan)
    references = _pet_shot_references(plan, shot, selections)
    drive_audio, source_tts_sha256 = _pet_shot_audio_bindings(plan, shot)
    if drive_audio is None:
        raise PetSitcomGenerationError(
            "The shot 09 evidence recut requires immutable drive audio."
        )
    source_candidates = _validated_pet_recut_sources(
        plan,
        shot,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
    )
    candidate_number = 6
    candidate = _pet_candidate_path(shot, candidate_number)
    report_path = _pet_gateway_report_path(candidate)
    expected = _pet_local_recut_provenance(
        shot,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
        source_candidates,
    )
    state = _read_json_object(_pet_candidate_state_path(candidate))
    report = _read_json_object(report_path)
    if _local_recut_is_reusable(state, report, candidate, expected):
        return {
            "schema_version": PET_LOCAL_RECUT_SCHEMA,
            "success": True,
            "executed": False,
            "status": "reused",
            "shot_id": shot.shot_id,
            "candidate_number": candidate_number,
            "video_path": str(candidate.resolve()),
            "video_sha256": state["video_sha256"],
            "report_path": str(report_path.resolve()),
        }

    _ensure_output_path(candidate, output_dir=plan.output_dir)
    temporary = _temporary_path(candidate, ".mp4")
    removal_mask = _temporary_path(candidate, ".mask.png")
    fleck_patch = _temporary_path(candidate, ".flecks.png")
    try:
        _write_pet_removal_mask(
            removal_mask,
            expected["recipe"]["removal_mask"],
        )
        _write_pet_evidence_fleck_patch(
            fleck_patch,
            expected["recipe"]["fleck_patch"],
        )
        command = _pet_shot_nine_evidence_recut_command(
            source_candidates,
            drive_audio,
            removal_mask,
            fleck_patch,
            expected["recipe"],
            temporary,
            ffmpeg_bin=ffmpeg_bin,
        )
        command_runner(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        if not is_valid_mp4_file(temporary):
            raise PetSitcomGenerationError(
                "The local shot 09 evidence recut did not produce a valid MP4."
            )
        _fsync_file(temporary)
        os.replace(temporary, candidate)
        _fsync_directory(candidate.parent)
    except PetSitcomGenerationError:
        raise
    except Exception as exc:
        raise PetSitcomGenerationError(
            "The local shot 09 evidence recut failed."
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)
        removal_mask.unlink(missing_ok=True)
        fleck_patch.unlink(missing_ok=True)

    completed = dict(expected)
    completed["provider_success"] = True
    completed["video_sha256"] = _sha256_file(candidate)
    _write_atomic_json(
        _pet_candidate_state_path(candidate),
        completed,
        output_dir=plan.output_dir,
    )
    _write_atomic_json(
        report_path,
        {
            "schema_version": PET_LOCAL_RECUT_SCHEMA,
            "success": True,
            "executed": True,
            "shot_id": shot.shot_id,
            "candidate_number": candidate_number,
            "pet_sitcom_provenance": completed,
        },
        output_dir=plan.output_dir,
    )
    return {
        "schema_version": PET_LOCAL_RECUT_SCHEMA,
        "success": True,
        "executed": True,
        "status": "generated",
        "shot_id": shot.shot_id,
        "candidate_number": candidate_number,
        "video_path": str(candidate.resolve()),
        "video_sha256": completed["video_sha256"],
        "report_path": str(report_path.resolve()),
    }


def build_pet_shot_ten_evidence_recut(
    plan: PetSitcomPlan,
    *,
    command_runner: Any = subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, Any]:
    """Build the hash-bound shot 10 dialogue, blink, and evidence recovery cut."""
    from .pet_sitcom_audio_probe import require_approved_pet_audio_probe

    require_approved_pet_audio_probe(plan)
    _validate_plan(plan)
    shot = next(
        (item for item in plan.shots if item.shot_id == "shot_10"),
        None,
    )
    if shot is None or shot.generation_duration_seconds != 5:
        raise PetSitcomGenerationError(
            "The shot 10 evidence recut requires the approved five-second source shot."
        )
    selections = _read_pet_selections(plan)
    references = _pet_shot_references(plan, shot, selections)
    drive_audio, source_tts_sha256 = _pet_shot_audio_bindings(plan, shot)
    if drive_audio is None:
        raise PetSitcomGenerationError(
            "The shot 10 evidence recut requires immutable drive audio."
        )
    source_candidates = _validated_pet_recut_sources(
        plan,
        shot,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
    )
    candidate_number = 6
    candidate = _pet_candidate_path(shot, candidate_number)
    report_path = _pet_gateway_report_path(candidate)
    expected = _pet_local_recut_provenance(
        shot,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
        source_candidates,
    )
    state = _read_json_object(_pet_candidate_state_path(candidate))
    report = _read_json_object(report_path)
    if _local_recut_is_reusable(state, report, candidate, expected):
        return {
            "schema_version": PET_LOCAL_RECUT_SCHEMA,
            "success": True,
            "executed": False,
            "status": "reused",
            "shot_id": shot.shot_id,
            "candidate_number": candidate_number,
            "video_path": str(candidate.resolve()),
            "video_sha256": state["video_sha256"],
            "report_path": str(report_path.resolve()),
        }

    _ensure_output_path(candidate, output_dir=plan.output_dir)
    temporary = _temporary_path(candidate, ".mp4")
    fleck_patch = _temporary_path(candidate, ".flecks.png")
    try:
        _write_pet_evidence_fleck_patch(
            fleck_patch,
            expected["recipe"]["fleck_patch"],
        )
        command = _pet_shot_ten_evidence_recut_command(
            source_candidates,
            drive_audio,
            fleck_patch,
            expected["recipe"],
            temporary,
            ffmpeg_bin=ffmpeg_bin,
        )
        command_runner(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        if not is_valid_mp4_file(temporary):
            raise PetSitcomGenerationError(
                "The local shot 10 evidence recut did not produce a valid MP4."
            )
        _fsync_file(temporary)
        os.replace(temporary, candidate)
        _fsync_directory(candidate.parent)
    except PetSitcomGenerationError:
        raise
    except Exception as exc:
        raise PetSitcomGenerationError(
            "The local shot 10 evidence recut failed."
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)
        fleck_patch.unlink(missing_ok=True)

    completed = dict(expected)
    completed["provider_success"] = True
    completed["video_sha256"] = _sha256_file(candidate)
    _write_atomic_json(
        _pet_candidate_state_path(candidate),
        completed,
        output_dir=plan.output_dir,
    )
    _write_atomic_json(
        report_path,
        {
            "schema_version": PET_LOCAL_RECUT_SCHEMA,
            "success": True,
            "executed": True,
            "shot_id": shot.shot_id,
            "candidate_number": candidate_number,
            "pet_sitcom_provenance": completed,
        },
        output_dir=plan.output_dir,
    )
    return {
        "schema_version": PET_LOCAL_RECUT_SCHEMA,
        "success": True,
        "executed": True,
        "status": "generated",
        "shot_id": shot.shot_id,
        "candidate_number": candidate_number,
        "video_path": str(candidate.resolve()),
        "video_sha256": completed["video_sha256"],
        "report_path": str(report_path.resolve()),
    }


def select_pet_shot_candidate(
    plan: PetSitcomPlan, shot_id: str, candidate_number: int
) -> dict[str, Any]:
    """Select a proven candidate and invalidate only its downstream dependencies."""
    from .pet_sitcom_audio_probe import require_approved_pet_audio_probe

    require_approved_pet_audio_probe(plan)
    _validate_plan(plan)
    if candidate_number not in {1, 2, 3, 4, 5, 6}:
        raise PetSitcomGenerationError(
            "Pet shot candidate number must be 1, 2, 3, 4, 5, or 6."
        )
    shot = next((item for item in plan.shots if item.shot_id == shot_id), None)
    if shot is None:
        raise PetSitcomGenerationError(f"Unknown pet sitcom shot: {shot_id}")
    selection_document = _read_pet_selection_document(plan)
    selections = _selection_shots(selection_document)
    history = _selection_history(selection_document)
    references = _pet_shot_references(plan, shot, selections)
    drive_audio, source_tts_sha256 = _pet_shot_audio_bindings(plan, shot)
    candidate = _pet_candidate_path(shot, candidate_number)
    state = _read_json_object(_pet_candidate_state_path(candidate))
    _validate_candidate_for_selection(
        plan,
        shot,
        candidate_number,
        candidate,
        state,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
    )

    old = selections.get(shot.shot_id)
    entry: dict[str, Any] = {
        "candidate_number": candidate_number,
        "status": "selected",
        "video_path": str(candidate.resolve()),
        "video_sha256": _sha256_file(candidate),
        "prompt_sha256": state["prompt_sha256"],
        "reference_paths": state["reference_paths"],
        "reference_sha256": state["reference_sha256"],
        "dependency_video_sha256": state["dependency_video_sha256"],
        "source_tts_sha256": state["source_tts_sha256"],
        "reference_audio_sha256": state["reference_audio_sha256"],
        "selected_at": datetime.now(timezone.utc).isoformat(),
    }
    changed = bool(
        old
        and (
            old.get("candidate_number") != candidate_number
            or old.get("video_sha256") != entry["video_sha256"]
            or old.get("prompt_sha256") != entry["prompt_sha256"]
            or old.get("dependency_video_sha256")
            != entry["dependency_video_sha256"]
            or old.get("source_tts_sha256") != entry["source_tts_sha256"]
            or old.get("reference_audio_sha256")
            != entry["reference_audio_sha256"]
        )
    )
    continuity_frame = _pet_continuity_frame_path(plan, shot.shot_id)
    frame = extract_pet_continuity_frame(
        candidate,
        continuity_frame,
        edit_duration_seconds=shot.duration_seconds,
        output_dir=plan.output_dir,
    )
    continuity_state = _read_json_object(_pet_continuity_state_path(frame))
    entry["continuity_frame_path"] = str(frame.resolve())
    entry["continuity_sidecar_path"] = str(
        _pet_continuity_state_path(frame).resolve()
    )
    entry["continuity_frame_sha256"] = _sha256_file(frame)
    entry["continuity_timestamp_seconds"] = continuity_state["timestamp_seconds"]
    if changed and old:
        history.setdefault(shot.shot_id, []).append(deepcopy(old))
    selections[shot.shot_id] = entry
    if changed:
        for downstream_id in dependent_shot_ids(plan, shot.shot_id):
            stale = selections.get(downstream_id)
            if stale:
                stale["status"] = "stale_upstream"
                stale["stale_from_shot"] = shot.shot_id
    _write_pet_selections(plan, selections, history)
    return {
        "schema_version": PET_SELECTION_SCHEMA,
        "shots": selections,
        "history": history,
    }


def dependent_shot_ids(
    plan: PetSitcomPlan, changed_shot_id: str
) -> tuple[str, ...]:
    """Return declared transitive dependents in stable plan order."""
    if changed_shot_id not in {shot.shot_id for shot in plan.shots}:
        raise PetSitcomGenerationError(
            f"Unknown pet sitcom shot: {changed_shot_id}"
        )
    pending = [changed_shot_id]
    discovered: set[str] = set()
    while pending:
        current = pending.pop(0)
        for shot in plan.shots:
            if (
                current in shot.continuity_source_ids
                and shot.shot_id not in discovered
            ):
                discovered.add(shot.shot_id)
                pending.append(shot.shot_id)
    return tuple(
        shot.shot_id for shot in plan.shots if shot.shot_id in discovered
    )


def extract_pet_continuity_frame(
    source_video: str | Path,
    output_path: str | Path,
    *,
    edit_duration_seconds: float | None = None,
    command_runner: Any = subprocess.run,
    output_dir: Path | None = None,
) -> Path:
    """Extract the last usable edit frame and bind it to exact source timing."""
    source_input = Path(source_video).expanduser().absolute()
    output_input = Path(output_path).expanduser().absolute()
    if output_dir is not None:
        _require_pet_output_path(
            source_input, output_dir, "Continuity source"
        )
        _require_pet_output_path(
            output_input, output_dir, "Generated output"
        )
        _require_pet_output_path(
            _pet_continuity_state_path(output_input),
            output_dir,
            "Continuity sidecar",
        )
    source = source_input.resolve()
    output = output_input.resolve()
    if not is_valid_mp4_file(source):
        raise PetSitcomGenerationError(f"Continuity source is not a valid MP4: {source}")
    probe = probe_media(source, required_stream="video")
    source_duration = float(probe.duration_seconds)
    edit_duration = (
        source_duration
        if edit_duration_seconds is None
        else float(edit_duration_seconds)
    )
    if (
        not probe.valid
        or probe.video_stream_count != 1
        or not math.isfinite(source_duration)
        or not math.isfinite(edit_duration)
        or source_duration <= 0.08
        or edit_duration <= 0.08
    ):
        raise PetSitcomGenerationError(
            "Continuity extraction requires finite positive video and edit durations."
        )
    timestamp = min(edit_duration - 0.08, source_duration - 0.08)
    if _pet_continuity_matches(
        source,
        output,
        edit_duration_seconds=edit_duration,
        source_duration_seconds=source_duration,
        timestamp_seconds=timestamp,
    ):
        return output
    _ensure_output_path(output, output_dir=output_dir)
    temporary = _temporary_path(output, ".png")
    try:
        command_runner(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss",
                f"{timestamp:.3f}", "-i",
                str(source), "-frames:v", "1", "-vf",
                "scale=1080:1920:force_original_aspect_ratio=decrease,"
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black", str(temporary),
            ],
            check=True,
        )
        _require_png(temporary, "continuity frame")
        _fsync_file(temporary)
        os.replace(temporary, output)
        _fsync_directory(output.parent)
        _write_atomic_json(
            _pet_continuity_state_path(output),
            {
                "schema_version": PET_CONTINUITY_SCHEMA,
                "source_video_path": str(source),
                "source_video_sha256": _sha256_file(source),
                "source_video_duration_seconds": source_duration,
                "edit_duration_seconds": edit_duration,
                "timestamp_seconds": timestamp,
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "frame_sha256": _sha256_file(output),
            },
            output_dir=output_dir,
        )
    finally:
        temporary.unlink(missing_ok=True)
    return output


def sanitize_pet_sitcom_report(
    value: Any, known_secrets: tuple[str, ...] = ()
) -> Any:
    """Public gateway sanitizer kept consistent with the existing mouth-test gate."""
    return _sanitize_report(value, known_secrets)


def _requested_pet_shots(plan: PetSitcomPlan, shot_id: str | None) -> tuple[Any, ...]:
    if shot_id is None:
        return plan.shots
    for shot in plan.shots:
        if shot.shot_id == shot_id:
            return (shot,)
    raise PetSitcomGenerationError(f"Unknown pet sitcom shot: {shot_id}")


def _validate_pet_candidate_request(
    shots: tuple[Any, ...], candidate_number: int, retry_reason: str
) -> None:
    if candidate_number not in {1, 2, 3, 4, 5}:
        raise PetSitcomGenerationError(
            "Pet shot candidate 6 and later are not allowed."
        )
    if candidate_number == 1:
        if retry_reason:
            raise PetSitcomGenerationError("Candidate 1 must not include a retry reason.")
        return
    if len(shots) != 1 or retry_reason not in PET_RETRY_SUFFIXES:
        raise PetSitcomGenerationError(
            f"Candidate {candidate_number} requires exactly one shot and one allowed "
            "retry reason."
        )


def _pet_shot_references(
    plan: PetSitcomPlan, shot: Any, selections: Mapping[str, dict[str, Any]]
) -> list[Path]:
    scene = next((item for item in plan.scenes if item.slug == shot.scene_slug), None)
    if scene is None:
        raise PetSitcomGenerationError(f"Unknown scene for {shot.shot_id}: {shot.scene_slug}")
    references = [
        plan.characters[0].reference_path,
        plan.characters[1].reference_path,
        scene.anchor_path,
    ]
    shot_by_id = {item.shot_id: item for item in plan.shots}
    for source_id in shot.continuity_source_ids:
        source_shot = shot_by_id[source_id]
        selection = selections.get(source_id)
        if not _current_pet_selection(selection):
            raise PetSitcomGenerationError(
                f"{shot.shot_id} requires selected valid {source_id} first."
            )
        candidate_number = selection.get("candidate_number")
        if candidate_number not in {1, 2, 3, 4, 5, 6}:
            raise PetSitcomGenerationError(
                f"{shot.shot_id} has invalid selected candidate for {source_id}."
            )
        source_video = _pet_candidate_path(source_shot, candidate_number)
        continuity_frame = _pet_continuity_frame_path(plan, source_id)
        if selection.get("video_path") != str(source_video.resolve()):
            raise PetSitcomGenerationError(
                f"{shot.shot_id} has an unsafe selected path for {source_id}."
            )
        if selection.get("video_sha256") != _file_hash_or_empty(source_video):
            raise PetSitcomGenerationError(
                f"{shot.shot_id} requires the current selected {source_id} hash."
            )
        if (
            selection.get("continuity_frame_path")
            != str(continuity_frame.resolve())
            or selection.get("continuity_sidecar_path")
            != str(_pet_continuity_state_path(continuity_frame).resolve())
            or selection.get("continuity_frame_sha256")
            != _file_hash_or_empty(continuity_frame)
            or not _pet_continuity_matches(
                source_video,
                continuity_frame,
                edit_duration_seconds=source_shot.duration_seconds,
            )
        ):
            raise PetSitcomGenerationError(
                f"{shot.shot_id} continuity provenance does not match {source_id}."
            )
        if not (shot.shot_id == "shot_07" and source_id == "shot_05"):
            references.append(continuity_frame)
    if not all(_is_png_file(path) for path in references):
        raise PetSitcomGenerationError(f"{shot.shot_id} has invalid local PNG references.")
    return references


def _pet_shot_prompt(shot: Any, candidate_number: int, retry_reason: str) -> str:
    if (
        shot.shot_id == "shot_08"
        and candidate_number == 5
        and retry_reason == "continuity"
    ):
        return _PET_SHOT_EIGHT_CLEAN_PERFORMANCE_PROMPT
    if (
        shot.shot_id == "shot_10"
        and candidate_number == 1
        and retry_reason == ""
    ):
        return _PET_SHOT_TEN_CLEAN_PERFORMANCE_PROMPT
    if (
        shot.shot_id == "shot_10"
        and candidate_number == 3
        and retry_reason == "continuity"
    ):
        return _PET_SHOT_TEN_CLEAN_TRACKING_PLATE_PROMPT
    dependency_notes = " ".join(
        f"Reference {index} preserves the selected ending state from {source_id}."
        for index, source_id in enumerate(shot.continuity_source_ids, start=4)
    )
    if shot.shot_id == "shot_07":
        dependency_notes = (
            "Reference 4 is the exact low security-camera composition from shot_06. "
            "Continue Reference 4 without changing its camera axis, cabinet geometry, "
            "empty-pouch position, or light direction; do not return to the two-cat "
            "composition."
        )
    base_prompt = (
        f"{shot.base_prompt} {dependency_notes}".strip()
        if dependency_notes
        else shot.base_prompt
    )
    if shot.shot_id == "shot_10":
        base_prompt = _PET_SHOT_TEN_CLEAN_PERFORMANCE_PROMPT
    if candidate_number == 1:
        return base_prompt
    prompt = (
        f"{base_prompt} Retry correction: "
        f"{PET_RETRY_SUFFIXES[retry_reason]}"
    )
    detail = _PET_SHOT_RETRY_DETAILS.get(
        (str(shot.shot_id), retry_reason),
        "",
    )
    if detail:
        prompt = f"{prompt} Shot-specific correction: {detail}"
    if candidate_number == 3:
        third_detail = _PET_SHOT_THIRD_RETRY_DETAILS.get(
            (str(shot.shot_id), retry_reason),
            "",
        )
        if third_detail:
            prompt = f"{prompt} Third-candidate correction: {third_detail}"
    if candidate_number == 4:
        fourth_detail = _PET_SHOT_FOURTH_RETRY_DETAILS.get(
            (str(shot.shot_id), retry_reason),
            "",
        )
        if fourth_detail:
            prompt = f"{prompt} Fourth-candidate correction: {fourth_detail}"
    if candidate_number == 5:
        fifth_detail = _PET_SHOT_FIFTH_RETRY_DETAILS.get(
            (str(shot.shot_id), retry_reason),
            "",
        )
        if fifth_detail:
            prompt = f"{prompt} Fifth-candidate correction: {fifth_detail}"
    return prompt


def _pet_candidate_path(shot: Any, candidate_number: int) -> Path:
    return shot.candidate_dir / f"candidate_{candidate_number:03d}.mp4"


def _pet_gateway_report_path(candidate: Path) -> Path:
    return candidate.with_suffix(".report.json")


def _pet_candidate_state_path(candidate: Path) -> Path:
    return candidate.with_suffix(".provenance.json")


def _pet_candidate_continuity_frame_path(candidate: Path) -> Path:
    return candidate.with_suffix(".continuity.png")


def _pet_continuity_frame_path(plan: PetSitcomPlan, shot_id: str) -> Path:
    return plan.output_dir / "continuity" / f"{shot_id}_last.png"


def _pet_shot_audio_bindings(
    plan: PetSitcomPlan, shot: Any
) -> tuple[Path | None, str]:
    if shot.speaker not in {"naitang", "doubao"}:
        return None, ""
    drive_input = Path(
        build_pet_drive_audio(plan, shot.shot_id)
    ).expanduser().absolute()
    _require_pet_output_path(
        drive_input, plan.output_dir, "Pet reference audio path"
    )
    if drive_input.is_symlink():
        raise PetSitcomGenerationError(
            f"{shot.shot_id} drive WAV may not be a symlink."
        )
    drive_audio = drive_input.resolve()
    if (
        not drive_audio.is_file()
        or not _file_hash_or_empty(drive_audio)
    ):
        raise PetSitcomGenerationError(
            f"{shot.shot_id} requires a current drive WAV."
        )
    source_asset = next(
        (
            asset
            for asset in load_pet_speech_assets(plan)
            if asset.shot_id == shot.shot_id
        ),
        None,
    )
    if source_asset is None:
        raise PetSitcomGenerationError(
            f"{shot.shot_id} requires a current immutable TTS asset."
        )
    source_input = Path(source_asset.output_path).expanduser().absolute()
    _require_pet_output_path(
        source_input, plan.output_dir, "Pet TTS source path"
    )
    if source_input.is_symlink():
        raise PetSitcomGenerationError(
            f"{shot.shot_id} TTS source may not be a symlink."
        )
    source_path = source_input.resolve()
    source_hash = _file_hash_or_empty(source_path)
    if (
        not source_path.is_file()
        or source_asset.output_sha256 != source_hash
    ):
        raise PetSitcomGenerationError(
            f"{shot.shot_id} immutable TTS asset is stale."
        )
    return drive_audio, source_hash


def _pet_candidate_provenance(
    shot: Any,
    candidate_number: int,
    prompt: str,
    retry_reason: str,
    references: list[Path],
    selections: Mapping[str, dict[str, Any]],
    drive_audio: Path | None,
    source_tts_sha256: str,
) -> dict[str, Any]:
    paths = [str(path.resolve()) for path in references]
    return {
        "schema_version": PET_SHOT_GENERATION_SCHEMA,
        "shot_id": shot.shot_id,
        "candidate_number": candidate_number,
        "provider": VIDEO_PROVIDER,
        "model": VIDEO_MODEL,
        "base_prompt_sha256": _hash_payload({"prompt": shot.base_prompt}),
        "prompt_sha256": _hash_payload({"prompt": prompt}),
        "retry_reason": "" if candidate_number == 1 else retry_reason,
        "retry_suffix": "" if candidate_number == 1 else PET_RETRY_SUFFIXES[retry_reason],
        "reference_paths": paths,
        "reference_sha256": [_sha256_file(path) for path in references],
        "dependency_video_sha256": {
            source_id: str(selections[source_id]["video_sha256"])
            for source_id in shot.continuity_source_ids
        },
        "source_tts_sha256": source_tts_sha256,
        "reference_audio_path": (
            "" if drive_audio is None else str(drive_audio.resolve())
        ),
        "reference_audio_sha256": (
            "" if drive_audio is None else _sha256_file(drive_audio)
        ),
        "generation_duration_seconds": shot.generation_duration_seconds,
        "generate_audio": drive_audio is not None,
        "gateway_report_path": str(
            _pet_gateway_report_path(
                _pet_candidate_path(shot, candidate_number)
            ).resolve()
        ),
        "video_sha256": "",
        "provider_success": False,
    }


def _pet_local_recut_recipe(shot: Any) -> Mapping[str, Any] | None:
    return {
        "shot_01": _PET_SHOT_ONE_REACTION_RECUT_RECIPE,
        "shot_03": _PET_SHOT_THREE_DIALOGUE_RECUT_RECIPE,
        "shot_04": _PET_SHOT_FOUR_DIALOGUE_RECUT_RECIPE,
        "shot_05": _PET_SHOT_FIVE_DIALOGUE_RECUT_RECIPE,
        "shot_06": _PET_SHOT_SIX_SECURITY_RECUT_RECIPE,
        "shot_07": _PET_SHOT_SEVEN_TAIL_RECUT_RECIPE,
        "shot_08": _PET_SHOT_EIGHT_EVIDENCE_RECUT_RECIPE,
        "shot_09": _PET_SHOT_NINE_EVIDENCE_RECUT_RECIPE,
        "shot_10": _PET_SHOT_TEN_EVIDENCE_RECUT_RECIPE,
    }.get(str(shot.shot_id))


def _validated_pet_recut_sources(
    plan: PetSitcomPlan,
    shot: Any,
    references: list[Path],
    selections: Mapping[str, dict[str, Any]],
    drive_audio: Path | None,
    source_tts_sha256: str,
) -> list[dict[str, Any]]:
    recipe = _pet_local_recut_recipe(shot)
    shots = {item.shot_id: item for item in plan.shots}
    if recipe is None:
        raise PetSitcomGenerationError(
            f"No local recut recipe is defined for {shot.shot_id}."
        )
    result: list[dict[str, Any]] = []
    for segment in recipe["segments"]:
        source_shot_id = str(
            segment.get("source_shot_id", shot.shot_id)
        )
        source_shot = shots.get(source_shot_id)
        if source_shot is None:
            raise PetSitcomGenerationError(
                f"Local recut source shot {source_shot_id} is unknown."
            )
        candidate_number = int(segment["candidate_number"])
        candidate = _pet_candidate_path(source_shot, candidate_number)
        state = _read_json_object(_pet_candidate_state_path(candidate))
        if source_shot_id == shot.shot_id:
            source_references = references
            source_drive_audio = drive_audio
            source_tts = source_tts_sha256
        else:
            source_references = _pet_shot_references(
                plan,
                source_shot,
                selections,
            )
            source_drive_audio, source_tts = _pet_shot_audio_bindings(
                plan,
                source_shot,
            )
        _validate_candidate_for_selection(
            plan,
            source_shot,
            candidate_number,
            candidate,
            state,
            source_references,
            selections,
            source_drive_audio,
            source_tts,
        )
        result.append(
            {
                "candidate_number": candidate_number,
                "video_path": str(candidate.resolve()),
                "video_sha256": _sha256_file(candidate),
                "start_seconds": float(segment["start_seconds"]),
                "end_seconds": float(segment["end_seconds"]),
                "video_filter": str(segment["video_filter"]),
            }
        )
    return result


def _pet_local_recut_provenance(
    shot: Any,
    references: list[Path],
    selections: Mapping[str, dict[str, Any]],
    drive_audio: Path | None,
    source_tts_sha256: str,
    source_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    recipe_value = _pet_local_recut_recipe(shot)
    if recipe_value is None:
        raise PetSitcomGenerationError(
            f"No local recut recipe is defined for {shot.shot_id}."
        )
    recipe = deepcopy(recipe_value)
    recipe_sha256 = _hash_payload(recipe)
    candidate_number = int(recipe["candidate_number"])
    candidate = _pet_candidate_path(shot, candidate_number)
    retry_suffix = {
        "shot_01": (
            "Motivated hard cut from the successful empty-bag wide action "
            "to a moving two-cat close reaction."
        ),
        "shot_03": (
            "Motivated shot-reverse-shot keeps Naitang visible for the "
            "first and final phrases while Doubao carries the offscreen "
            "middle phrase with continuous reaction motion."
        ),
        "shot_04": (
            "Retimed close-up preserves Doubao's good speaking and gaze "
            "motion while binding the visible mouth window to immutable "
            "drive audio."
        ),
        "shot_05": (
            "Motivated establishing, Doubao close-up, and Naitang reaction "
            "coverage bind natural speaker and lip-lick motion to immutable "
            "drive audio."
        ),
        "shot_06": (
            "Preserve the successful empty low-angle pouch replay and add only "
            "subtle changing luminance sensor grain to its physically settled "
            "security-camera hold."
        ),
        "shot_07": (
            "Preserve the physically connected orange tail-tip clue and stationary "
            "empty pouch, adding only subtle changing luminance sensor grain to the "
            "locked security-camera replay."
        ),
        "shot_08": (
            "Retimed clean two-cat performance binds Naitang's visible mouth window "
            "to immutable drive audio and tracks two tiny flat evidence flecks at the "
            "same right-mouth-corner fur location."
        ),
        "shot_09": (
            "Preserve the successful grounded mirror push and two-cat performance, "
            "remove the generated dangling food chunk, bind Doubao's visible mouth "
            "window to immutable drive audio, then cut to his silent reaction close-up."
        ),
        "shot_10": (
            "Retimed clean-mirror two-cat performance binds Naitang's visible mouth "
            "window to immutable drive audio, moves Doubao's complete blink into the "
            "final edit window, and tracks two tiny flat evidence flecks on Naitang."
        ),
    }[str(shot.shot_id)]
    retry_reason = {
        "shot_01": "continuity",
        "shot_03": "continuity",
        "shot_04": "continuity",
        "shot_05": "mouth_anatomy",
        "shot_06": "continuity",
        "shot_07": "continuity",
        "shot_08": "continuity",
        "shot_09": "continuity",
        "shot_10": "continuity",
    }[str(shot.shot_id)]
    return {
        "schema_version": PET_LOCAL_RECUT_SCHEMA,
        "shot_id": shot.shot_id,
        "candidate_number": candidate_number,
        "provider": "local_ffmpeg_recut",
        "model": "ffmpeg",
        "base_prompt_sha256": _hash_payload(
            {"prompt": shot.base_prompt}
        ),
        "prompt_sha256": recipe_sha256,
        "retry_reason": retry_reason,
        "retry_suffix": retry_suffix,
        "reference_paths": [
            str(path.resolve()) for path in references
        ],
        "reference_sha256": [
            _sha256_file(path) for path in references
        ],
        "dependency_video_sha256": {
            source_id: str(selections[source_id]["video_sha256"])
            for source_id in shot.continuity_source_ids
        },
        "source_tts_sha256": source_tts_sha256,
        "reference_audio_path": (
            "" if drive_audio is None else str(drive_audio.resolve())
        ),
        "reference_audio_sha256": (
            "" if drive_audio is None else _sha256_file(drive_audio)
        ),
        "generation_duration_seconds": shot.generation_duration_seconds,
        "generate_audio": drive_audio is not None,
        "gateway_report_path": str(
            _pet_gateway_report_path(candidate).resolve()
        ),
        "video_sha256": "",
        "provider_success": False,
        "source_candidates": deepcopy(source_candidates),
        "recipe": recipe,
        "recipe_sha256": recipe_sha256,
    }


def _pet_shot_one_recut_command(
    source_candidates: list[dict[str, Any]],
    output: Path,
    *,
    ffmpeg_bin: str,
) -> list[str]:
    if (
        len(source_candidates) != 2
        or any(
            set(item) != _PET_LOCAL_RECUT_SOURCE_FIELDS
            for item in source_candidates
        )
    ):
        raise PetSitcomGenerationError(
            "The local reaction recut requires two exact source segments."
        )
    filters: list[str] = []
    for index, source in enumerate(source_candidates):
        start = f"{float(source['start_seconds']):g}"
        end = f"{float(source['end_seconds']):g}"
        chain = (
            f"[{index}:v]trim=start={start}:end={end},"
            "setpts=PTS-STARTPTS"
        )
        if source["video_filter"]:
            chain = f"{chain},{source['video_filter']}"
        filters.append(f"{chain}[v{index}]")
    filters.append(
        "[v0][v1]concat=n=2:v=1:a=0,format=yuv420p[v]"
    )
    return [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        source_candidates[0]["video_path"],
        "-i",
        source_candidates[1]["video_path"],
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[v]",
        "-an",
        "-r",
        "24",
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-t",
        "6",
        str(output),
    ]


def _pet_shot_three_recut_command(
    source_candidates: list[dict[str, Any]],
    drive_audio: Path,
    output: Path,
    *,
    ffmpeg_bin: str,
    output_duration_seconds: float = 7.0,
) -> list[str]:
    if (
        len(source_candidates) < 2
        or any(
            set(item) != _PET_LOCAL_RECUT_SOURCE_FIELDS
            for item in source_candidates
        )
    ):
        raise PetSitcomGenerationError(
            "The local dialogue recut requires exact source segments."
        )
    inputs: list[str] = []
    filters: list[str] = []
    for index, source in enumerate(source_candidates):
        inputs.extend(["-i", source["video_path"]])
        start = f"{float(source['start_seconds']):g}"
        end = f"{float(source['end_seconds']):g}"
        chain = (
            f"[{index}:v]trim=start={start}:end={end},"
            "setpts=PTS-STARTPTS"
        )
        if source["video_filter"]:
            chain = f"{chain},{source['video_filter']}"
        filters.append(f"{chain}[v{index}]")
    labels = "".join(
        f"[v{index}]" for index in range(len(source_candidates))
    )
    filters.append(
        f"{labels}concat=n={len(source_candidates)}:v=1:a=0,"
        "format=yuv420p[v]"
    )
    return [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        *inputs,
        "-i",
        str(drive_audio.resolve()),
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[v]",
        "-map",
        f"{len(source_candidates)}:a:0",
        "-r",
        "24",
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-t",
        f"{float(output_duration_seconds):g}",
        str(output),
    ]


def _pet_track_expression(
    keyframes: list[Mapping[str, Any]],
    coordinate: str,
) -> str:
    if coordinate not in {"x", "y"} or len(keyframes) < 2:
        raise PetSitcomGenerationError(
            "Tracked evidence requires at least two x/y keyframes."
        )
    normalized: list[tuple[float, float]] = []
    for keyframe in keyframes:
        timestamp = float(keyframe["time_seconds"])
        value = float(keyframe[coordinate])
        if (
            not math.isfinite(timestamp)
            or not math.isfinite(value)
            or (normalized and timestamp <= normalized[-1][0])
        ):
            raise PetSitcomGenerationError(
                "Tracked evidence keyframes must be finite and strictly increasing."
            )
        normalized.append((timestamp, value))
    expression = f"{normalized[-1][1]:g}"
    for index in range(len(normalized) - 2, -1, -1):
        start_time, start_value = normalized[index]
        end_time, end_value = normalized[index + 1]
        duration = end_time - start_time
        slope = (end_value - start_value) / duration
        line = (
            f"{start_value:g}+({slope:g})*(t-{start_time:g})"
        )
        expression = f"if(lt(t,{end_time:g}),{line},{expression})"
    return expression


def _write_pet_removal_mask(
    destination: Path,
    style: Mapping[str, Any],
) -> None:
    from PIL import Image, ImageDraw

    width = int(style["width"])
    height = int(style["height"])
    center_x = int(style["center_x"])
    center_y = int(style["center_y"])
    radius_x = int(style["radius_x"])
    radius_y = int(style["radius_y"])
    if (
        width != 1080
        or height != 1920
        or radius_x <= 0
        or radius_y <= 0
        or center_x - radius_x < 0
        or center_x + radius_x >= width
        or center_y - radius_y < 0
        or center_y + radius_y >= height
    ):
        raise PetSitcomGenerationError(
            "Evidence-removal mask dimensions do not match the reviewed recipe."
        )
    image = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        (
            center_x - radius_x,
            center_y - radius_y,
            center_x + radius_x,
            center_y + radius_y,
        ),
        fill=255,
    )
    image.save(destination, format="PNG")


def _write_pet_evidence_fleck_patch(
    destination: Path,
    style: Mapping[str, Any],
) -> None:
    from PIL import Image, ImageDraw

    width = int(style["width"])
    height = int(style["height"])
    if width != 18 or height != 12:
        raise PetSitcomGenerationError(
            "Tracked evidence patch dimensions do not match the reviewed recipe."
        )
    primary = tuple(int(value) for value in style["primary_rgba"])
    secondary = tuple(int(value) for value in style["secondary_rgba"])
    if len(primary) != 4 or len(secondary) != 4:
        raise PetSitcomGenerationError(
            "Tracked evidence patch colors must be RGBA."
        )
    scale = 4
    image = Image.new(
        "RGBA",
        (width * scale, height * scale),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(image)
    draw.polygon(
        [
            (1 * scale, 3 * scale),
            (3 * scale, 1 * scale),
            (7 * scale, 2 * scale),
            (8 * scale, 5 * scale),
            (6 * scale, 7 * scale),
            (2 * scale, 6 * scale),
        ],
        fill=primary,
    )
    draw.polygon(
        [
            (11 * scale, 6 * scale),
            (13 * scale, 4 * scale),
            (17 * scale, 5 * scale),
            (16 * scale, 9 * scale),
            (12 * scale, 10 * scale),
        ],
        fill=secondary,
    )
    image.resize(
        (width, height),
        resample=Image.Resampling.LANCZOS,
    ).save(destination, format="PNG")


def _pet_shot_eight_evidence_recut_command(
    source_candidates: list[dict[str, Any]],
    drive_audio: Path,
    fleck_patch: Path,
    recipe: Mapping[str, Any],
    output: Path,
    *,
    ffmpeg_bin: str,
) -> list[str]:
    if (
        len(source_candidates) != 3
        or any(
            set(item) != _PET_LOCAL_RECUT_SOURCE_FIELDS
            for item in source_candidates
        )
        or int(recipe.get("fleck_count", 0)) != 2
    ):
        raise PetSitcomGenerationError(
            "The shot 08 evidence recut requires three exact source segments "
            "and two tracked flecks."
        )
    keyframes = recipe.get("tracking_keyframes")
    if not isinstance(keyframes, list):
        raise PetSitcomGenerationError(
            "The shot 08 evidence recut requires tracking keyframes."
        )
    inputs: list[str] = []
    filters: list[str] = []
    for index, source in enumerate(source_candidates):
        inputs.extend(["-i", source["video_path"]])
        start = f"{float(source['start_seconds']):g}"
        end = f"{float(source['end_seconds']):g}"
        chain = (
            f"[{index}:v]trim=start={start}:end={end},"
            "setpts=PTS-STARTPTS"
        )
        if source["video_filter"]:
            chain = f"{chain},{source['video_filter']}"
        filters.append(f"{chain}[v{index}]")
    labels = "".join(
        f"[v{index}]" for index in range(len(source_candidates))
    )
    filters.append(
        f"{labels}concat=n={len(source_candidates)}:v=1:a=0[base]"
    )
    patch_index = len(source_candidates)
    audio_index = patch_index + 1
    x_expression = _pet_track_expression(keyframes, "x")
    y_expression = _pet_track_expression(keyframes, "y")
    visible_until = float(recipe["fleck_visible_until_seconds"])
    filters.append(
        f"[base][{patch_index}:v]overlay=x='{x_expression}':"
        f"y='{y_expression}':eval=frame:shortest=1:"
        f"enable='lt(t,{visible_until:g})',"
        "format=yuv420p[v]"
    )
    duration = f"{float(recipe['output_duration_seconds']):g}"
    return [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        *inputs,
        "-loop",
        "1",
        "-i",
        str(fleck_patch.resolve()),
        "-i",
        str(drive_audio.resolve()),
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[v]",
        "-map",
        f"{audio_index}:a:0",
        "-r",
        "24",
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-t",
        duration,
        str(output),
    ]


def _pet_shot_nine_evidence_recut_command(
    source_candidates: list[dict[str, Any]],
    drive_audio: Path,
    removal_mask: Path,
    fleck_patch: Path,
    recipe: Mapping[str, Any],
    output: Path,
    *,
    ffmpeg_bin: str,
) -> list[str]:
    removal_indexes = recipe.get("removal_segment_indexes")
    if (
        len(source_candidates) != 3
        or any(
            set(item) != _PET_LOCAL_RECUT_SOURCE_FIELDS
            for item in source_candidates
        )
        or removal_indexes != [0, 1]
        or int(recipe.get("fleck_count", 0)) != 2
    ):
        raise PetSitcomGenerationError(
            "The shot 09 evidence recut requires three exact source segments, "
            "two removal segments, and two tracked flecks."
        )
    keyframes = recipe.get("tracking_keyframes")
    if not isinstance(keyframes, list):
        raise PetSitcomGenerationError(
            "The shot 09 evidence recut requires tracking keyframes."
        )
    escaped_mask = (
        str(removal_mask.resolve())
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
    )
    inputs: list[str] = []
    filters: list[str] = []
    for index, source in enumerate(source_candidates):
        inputs.extend(["-i", source["video_path"]])
        start = f"{float(source['start_seconds']):g}"
        end = f"{float(source['end_seconds']):g}"
        chain = (
            f"[{index}:v]trim=start={start}:end={end},"
            "setpts=PTS-STARTPTS"
        )
        if index in removal_indexes:
            chain = f"{chain},removelogo=f='{escaped_mask}'"
        if source["video_filter"]:
            chain = f"{chain},{source['video_filter']}"
        filters.append(f"{chain}[v{index}]")
    labels = "".join(
        f"[v{index}]" for index in range(len(source_candidates))
    )
    filters.append(
        f"{labels}concat=n={len(source_candidates)}:v=1:a=0[base]"
    )
    patch_index = len(source_candidates)
    audio_index = patch_index + 1
    x_expression = _pet_track_expression(keyframes, "x")
    y_expression = _pet_track_expression(keyframes, "y")
    visible_until = float(recipe["fleck_visible_until_seconds"])
    filters.append(
        f"[base][{patch_index}:v]overlay=x='{x_expression}':"
        f"y='{y_expression}':eval=frame:shortest=1:"
        f"enable='lt(t,{visible_until:g})',"
        "format=yuv420p[v]"
    )
    duration = f"{float(recipe['output_duration_seconds']):g}"
    return [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        *inputs,
        "-loop",
        "1",
        "-i",
        str(fleck_patch.resolve()),
        "-i",
        str(drive_audio.resolve()),
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[v]",
        "-map",
        f"{audio_index}:a:0",
        "-r",
        "24",
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-t",
        duration,
        str(output),
    ]


def _pet_shot_ten_evidence_recut_command(
    source_candidates: list[dict[str, Any]],
    drive_audio: Path,
    fleck_patch: Path,
    recipe: Mapping[str, Any],
    output: Path,
    *,
    ffmpeg_bin: str,
) -> list[str]:
    if (
        recipe.get("mouth_window_seconds") != [0.7, 3.349167]
        or recipe.get("blink_window_seconds") != [3.55, 4.1]
        or float(recipe.get("output_duration_seconds", 0.0)) != 4.1
    ):
        raise PetSitcomGenerationError(
            "The shot 10 evidence recut requires the reviewed dialogue and blink windows."
        )
    return _pet_shot_eight_evidence_recut_command(
        source_candidates,
        drive_audio,
        fleck_patch,
        recipe,
        output,
        ffmpeg_bin=ffmpeg_bin,
    )


def _pet_silent_recut_command(
    source_candidates: list[dict[str, Any]],
    output: Path,
    *,
    ffmpeg_bin: str,
    output_duration_seconds: float,
) -> list[str]:
    if (
        not source_candidates
        or any(
            set(item) != _PET_LOCAL_RECUT_SOURCE_FIELDS
            for item in source_candidates
        )
    ):
        raise PetSitcomGenerationError(
            "The local silent recut requires exact source segments."
        )
    inputs: list[str] = []
    filters: list[str] = []
    for index, source in enumerate(source_candidates):
        inputs.extend(["-i", source["video_path"]])
        start = f"{float(source['start_seconds']):g}"
        end = f"{float(source['end_seconds']):g}"
        chain = (
            f"[{index}:v]trim=start={start}:end={end},"
            "setpts=PTS-STARTPTS"
        )
        if source["video_filter"]:
            chain = f"{chain},{source['video_filter']}"
        filters.append(f"{chain}[v{index}]")
    if len(source_candidates) == 1:
        filters.append("[v0]format=yuv420p[v]")
    else:
        labels = "".join(
            f"[v{index}]" for index in range(len(source_candidates))
        )
        filters.append(
            f"{labels}concat=n={len(source_candidates)}:v=1:a=0,"
            "format=yuv420p[v]"
        )
    return [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        *inputs,
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[v]",
        "-an",
        "-r",
        "24",
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-t",
        f"{float(output_duration_seconds):g}",
        str(output),
    ]


def _local_recut_is_reusable(
    state: Mapping[str, Any],
    report: Mapping[str, Any],
    candidate: Path,
    expected: Mapping[str, Any],
) -> bool:
    binding_fields = _PET_LOCAL_RECUT_FIELDS - {
        "provider_success",
        "video_sha256",
    }
    return bool(
        set(state) == _PET_LOCAL_RECUT_FIELDS
        and state.get("provider_success") is True
        and state.get("video_sha256") == _file_hash_or_empty(candidate)
        and is_valid_mp4_file(candidate)
        and report.get("success") is True
        and report.get("pet_sitcom_provenance") == state
        and all(
            state.get(key) == expected.get(key)
            for key in binding_fields
        )
    )


def _candidate_is_reusable(
    state: Mapping[str, Any], candidate: Path, expected: Mapping[str, Any]
) -> bool:
    report = _read_json_object(_pet_gateway_report_path(candidate))
    return bool(
        set(state) == _PET_PROVENANCE_FIELDS
        and state.get("provider_success") is True
        and state.get("video_sha256") == _file_hash_or_empty(candidate)
        and is_valid_mp4_file(candidate)
        and report.get("success") is True
        and report.get("pet_sitcom_provenance") == state
        and all(
            state.get(key) == expected.get(key)
            for key in _PET_PROVENANCE_BINDING_FIELDS
        )
    )


def _has_current_retry_selection(
    plan: PetSitcomPlan,
    shot: Any,
    selections: Mapping[str, dict[str, Any]],
) -> bool:
    selection = selections.get(shot.shot_id)
    candidate_number = selection.get("candidate_number") if selection else None
    if (
        not _current_pet_selection(selection)
        or candidate_number not in {2, 3, 4, 5, 6}
    ):
        return False
    candidate = _pet_candidate_path(shot, candidate_number)
    continuity_frame = _pet_continuity_frame_path(plan, shot.shot_id)
    try:
        references = _pet_shot_references(plan, shot, selections)
        drive_audio, source_tts_sha256 = _pet_shot_audio_bindings(plan, shot)
        state = _read_json_object(_pet_candidate_state_path(candidate))
        _validate_candidate_for_selection(
            plan,
            shot,
            candidate_number,
            candidate,
            state,
            references,
            selections,
            drive_audio,
            source_tts_sha256,
        )
    except PetSitcomGenerationError:
        return False
    if (
        selection.get("video_path") != str(candidate.resolve())
        or selection.get("video_sha256") != _file_hash_or_empty(candidate)
        or selection.get("prompt_sha256") != state.get("prompt_sha256")
        or selection.get("reference_paths") != state.get("reference_paths")
        or selection.get("reference_sha256") != state.get("reference_sha256")
        or selection.get("dependency_video_sha256")
        != state.get("dependency_video_sha256")
        or selection.get("source_tts_sha256")
        != state.get("source_tts_sha256")
        or selection.get("reference_audio_sha256")
        != state.get("reference_audio_sha256")
        or selection.get("continuity_frame_path")
        != str(continuity_frame.resolve())
        or selection.get("continuity_sidecar_path")
        != str(_pet_continuity_state_path(continuity_frame).resolve())
        or selection.get("continuity_frame_sha256")
        != _file_hash_or_empty(continuity_frame)
        or not _pet_continuity_matches(
            candidate,
            continuity_frame,
            edit_duration_seconds=shot.duration_seconds,
        )
    ):
        return False
    return True


def _pet_generation_entry(
    shot: Any,
    candidate_number: int,
    candidate: Path,
    provenance: Mapping[str, Any],
    status: str,
) -> dict[str, Any]:
    return {
        "shot_id": shot.shot_id,
        "candidate_number": candidate_number,
        "status": status,
        "video_path": str(candidate.resolve()),
        "video_sha256": _file_hash_or_empty(candidate),
        "prompt_sha256": provenance["prompt_sha256"],
        "references": [
            {"order": index, "path": path, "sha256": digest}
            for index, (path, digest) in enumerate(
                zip(provenance["reference_paths"], provenance["reference_sha256"]), start=1
            )
        ],
        "generation_duration_seconds": provenance[
            "generation_duration_seconds"
        ],
        "generate_audio": provenance["generate_audio"],
        "reference_audio_sha256": provenance["reference_audio_sha256"],
        "dependency_video_sha256": provenance["dependency_video_sha256"],
    }


def _validate_candidate_for_selection(
    plan: PetSitcomPlan,
    shot: Any,
    candidate_number: int,
    candidate: Path,
    state: Mapping[str, Any],
    references: list[Path],
    selections: Mapping[str, dict[str, Any]],
    drive_audio: Path | None,
    source_tts_sha256: str,
) -> None:
    if state.get("schema_version") == PET_LOCAL_RECUT_SCHEMA:
        _validate_local_recut_for_selection(
            plan,
            shot,
            candidate,
            state,
            references,
            selections,
            drive_audio,
            source_tts_sha256,
        )
        return
    retry_reason = state.get("retry_reason")
    if candidate_number == 1:
        retry_reason = ""
    if candidate_number > 1 and retry_reason not in PET_RETRY_SUFFIXES:
        raise PetSitcomGenerationError(
            f"Candidate {candidate_number} provenance requires an allowed retry reason."
        )
    prompt = _pet_shot_prompt(shot, candidate_number, str(retry_reason))
    expected = _pet_candidate_provenance(
        shot,
        candidate_number,
        prompt,
        str(retry_reason),
        references,
        selections,
        drive_audio,
        source_tts_sha256,
    )
    report = _read_json_object(_pet_gateway_report_path(candidate))
    probe = probe_media(candidate, required_stream="video")
    expected_audio_streams = 1 if drive_audio is not None else 0
    if (
        set(state) != _PET_PROVENANCE_FIELDS
        or state.get("provider_success") is not True
        or report.get("success") is not True
        or report.get("pet_sitcom_provenance") != state
        or not is_valid_mp4_file(candidate)
        or not probe.valid
        or probe.video_stream_count != 1
        or probe.audio_stream_count != expected_audio_streams
        or abs(
            float(probe.duration_seconds)
            - float(shot.generation_duration_seconds)
        )
        > 0.35
        or state.get("video_sha256") != _file_hash_or_empty(candidate)
        or any(
            state.get(key) != expected.get(key)
            for key in _PET_PROVENANCE_BINDING_FIELDS
        )
    ):
        raise PetSitcomGenerationError(
            f"Candidate {candidate_number} for {shot.shot_id} lacks current successful provenance."
        )


def _validate_local_recut_for_selection(
    plan: PetSitcomPlan,
    shot: Any,
    candidate: Path,
    state: Mapping[str, Any],
    references: list[Path],
    selections: Mapping[str, dict[str, Any]],
    drive_audio: Path | None,
    source_tts_sha256: str,
) -> None:
    candidate_number = int(state.get("candidate_number", 0) or 0)
    try:
        source_candidates = _validated_pet_recut_sources(
            plan,
            shot,
            references,
            selections,
            drive_audio,
            source_tts_sha256,
        )
    except PetSitcomGenerationError as exc:
        raise PetSitcomGenerationError(
            f"Candidate {candidate_number} for {shot.shot_id} local recut provenance is missing or stale."
        ) from exc
    expected = _pet_local_recut_provenance(
        shot,
        references,
        selections,
        drive_audio,
        source_tts_sha256,
        source_candidates,
    )
    output_duration_seconds = float(
        expected["recipe"]["output_duration_seconds"]
    )
    report = _read_json_object(_pet_gateway_report_path(candidate))
    probe = probe_media(candidate, required_stream="video")
    if (
        not _local_recut_is_reusable(
            state,
            report,
            candidate,
            expected,
        )
        or not probe.valid
        or probe.video_stream_count != 1
        or probe.audio_stream_count != (1 if drive_audio is not None else 0)
        or abs(
            float(probe.duration_seconds)
            - output_duration_seconds
        )
        > 0.35
    ):
        raise PetSitcomGenerationError(
            f"Candidate {candidate_number} for {shot.shot_id} local recut provenance is missing or stale."
        )


def _current_pet_selection(selection: Mapping[str, Any] | None) -> bool:
    return bool(selection and selection.get("status") == "selected")


def _selection_shots(selection: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw = selection.get("shots")
    return {
        str(key): dict(value)
        for key, value in raw.items()
        if isinstance(raw, dict) and isinstance(value, dict)
    } if isinstance(raw, dict) else {}


def _read_pet_selections(plan: PetSitcomPlan | None) -> dict[str, dict[str, Any]]:
    if plan is None:
        return {}
    return _selection_shots(_read_pet_selection_document(plan))


def _read_pet_selection_document(plan: PetSitcomPlan) -> dict[str, Any]:
    return _read_json_object(plan.selection_path)


def _selection_history(document: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    raw = document.get("history")
    if not isinstance(raw, dict):
        return {}
    return {
        str(shot_id): [dict(record) for record in records if isinstance(record, dict)]
        for shot_id, records in raw.items()
        if isinstance(records, list)
    }


def _write_pet_selections(
    plan: PetSitcomPlan,
    selections: Mapping[str, dict[str, Any]],
    history: Mapping[str, list[dict[str, Any]]],
) -> None:
    _write_atomic_json(
        plan.selection_path,
        {
            "schema_version": PET_SELECTION_SCHEMA,
            "shots": dict(selections),
            "history": dict(history),
        },
        output_dir=plan.output_dir,
    )


def _pet_continuity_state_path(frame: Path) -> Path:
    return frame.with_suffix(frame.suffix + ".state.json")


def _pet_continuity_matches(
    source: Path,
    frame: Path,
    *,
    edit_duration_seconds: float | None = None,
    source_duration_seconds: float | None = None,
    timestamp_seconds: float | None = None,
) -> bool:
    state = _read_json_object(_pet_continuity_state_path(frame))
    if set(state) != _PET_CONTINUITY_FIELDS:
        return False
    if edit_duration_seconds is not None:
        if state.get("edit_duration_seconds") != edit_duration_seconds:
            return False
    if source_duration_seconds is not None:
        if state.get("source_video_duration_seconds") != source_duration_seconds:
            return False
    if timestamp_seconds is not None:
        if state.get("timestamp_seconds") != timestamp_seconds:
            return False
    source_duration = state.get("source_video_duration_seconds")
    edit_duration = state.get("edit_duration_seconds")
    timestamp = state.get("timestamp_seconds")
    return bool(
        state.get("schema_version") == PET_CONTINUITY_SCHEMA
        and state.get("source_video_path") == str(source.resolve())
        and state.get("source_video_sha256") == _file_hash_or_empty(source)
        and isinstance(source_duration, (int, float))
        and isinstance(edit_duration, (int, float))
        and isinstance(timestamp, (int, float))
        and not isinstance(source_duration, bool)
        and not isinstance(edit_duration, bool)
        and not isinstance(timestamp, bool)
        and math.isfinite(float(source_duration))
        and math.isfinite(float(edit_duration))
        and math.isfinite(float(timestamp))
        and float(source_duration) > 0.08
        and float(edit_duration) > 0.08
        and float(timestamp) >= 0
        and abs(
            float(timestamp)
            - min(
                float(edit_duration) - 0.08,
                float(source_duration) - 0.08,
            )
        )
        <= 1e-9
        and isinstance(state.get("extracted_at"), str)
        and bool(state["extracted_at"])
        and state.get("frame_sha256") == _file_hash_or_empty(frame)
        and _is_png_file(frame)
    )


def _anchor_jobs(plan: PetSitcomPlan) -> list[dict[str, Any]]:
    return [
        *(
            {
                "name": character.slug,
                "prompt": character.prompt,
                "output": character.reference_path,
            }
            for character in plan.characters
        ),
        *(
            {"name": scene.slug, "prompt": scene.prompt, "output": scene.anchor_path}
            for scene in plan.scenes
        ),
    ]


def _requested_anchor_jobs(
    plan: PetSitcomPlan, anchor_names: tuple[str, ...] | None
) -> list[dict[str, Any]]:
    jobs = _anchor_jobs(plan)
    by_name = {str(job["name"]): job for job in jobs}
    if anchor_names is None:
        return jobs
    if not isinstance(anchor_names, tuple) or not anchor_names:
        raise PetSitcomGenerationError(
            "anchor_names must be a non-empty tuple of fixed anchor names."
        )
    if any(not isinstance(name, str) for name in anchor_names):
        raise PetSitcomGenerationError("anchor_names must contain only strings.")
    if len(set(anchor_names)) != len(anchor_names):
        raise PetSitcomGenerationError("anchor_names may not contain duplicates.")
    unknown = [name for name in anchor_names if name not in by_name]
    if unknown:
        raise PetSitcomGenerationError(
            f"Unknown pet anchor target: {unknown[0]}."
        )
    return [by_name[name] for name in anchor_names]


def _all_anchors_are_current(plan: PetSitcomPlan) -> bool:
    for job in _anchor_jobs(plan):
        signature = _hash_payload(
            {
                "provider": IMAGE_PROVIDER,
                "model": IMAGE_MODEL,
                "prompt": job["prompt"],
                "size": IMAGE_SIZE,
                "candidate_number": 1,
            }
        )
        if not _anchor_is_reusable(Path(job["output"]), signature):
            return False
    return True


def _generate_anchor(
    job: dict[str, Any], image_client: GatewayImageClient, signature: str
) -> None:
    output = Path(job["output"])
    _ensure_output_path(output)
    source = _temporary_path(output, ".source")
    normalized = _temporary_path(output, ".png")
    try:
        image_client.generate(str(job["prompt"]), source, size=IMAGE_SIZE, n=1)
        if not is_valid_image_file(source):
            raise PetSitcomGenerationError(
                "Anchor provider output is not JPEG, PNG, or WebP."
            )
        _convert_to_rgb_png(source, normalized)
        _require_png(normalized, "anchor")
        _fsync_file(normalized)
        os.replace(normalized, output)
        _fsync_directory(output.parent)
        _write_atomic_json(
            _anchor_state_path(output),
            {
                "schema_version": ANCHOR_STATE_SCHEMA,
                "signature": signature,
                "provider": IMAGE_PROVIDER,
                "model": IMAGE_MODEL,
                "prompt": job["prompt"],
                "size": IMAGE_SIZE,
                "candidate_number": 1,
                "output_sha256": _sha256_file(output),
            },
        )
    finally:
        source.unlink(missing_ok=True)
        normalized.unlink(missing_ok=True)


def _anchor_is_reusable(output: Path, signature: str) -> bool:
    state = _read_json_object(_anchor_state_path(output))
    return bool(
        state.get("schema_version") == ANCHOR_STATE_SCHEMA
        and state.get("signature") == signature
        and state.get("output_sha256") == _file_hash_or_empty(output)
        and _is_png_file(output)
    )


def _anchor_report(job: dict[str, Any], signature: str, status: str) -> dict[str, Any]:
    output = Path(job["output"])
    return {
        "name": job["name"],
        "status": status,
        "path": str(output),
        "sha256": _sha256_file(output),
        "signature": signature,
        "provider": IMAGE_PROVIDER,
        "model": IMAGE_MODEL,
        "size": IMAGE_SIZE,
        "candidate_number": 1,
    }


def _planned_anchor_report(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": job["name"],
        "status": "blocked",
        "path": str(job["output"]),
        "provider": IMAGE_PROVIDER,
        "model": IMAGE_MODEL,
        "size": IMAGE_SIZE,
        "candidate_number": 1,
    }


def _write_anchor_review_template(plan: PetSitcomPlan) -> None:
    current_hashes = _anchor_hashes(plan)
    existing = _read_json_object(_anchor_review_path(plan))
    if (
        existing.get("schema_version") == ANCHOR_REVIEW_SCHEMA
        and existing.get("source_hashes") == current_hashes
    ):
        return
    _write_atomic_json(
        _anchor_review_path(plan),
        {
            "schema_version": ANCHOR_REVIEW_SCHEMA,
            "completed": False,
            "approved": False,
            "source_hashes": current_hashes,
            **{field: False for field in _ANCHOR_REVIEW_FIELDS},
        },
    )


def _require_approved_anchors(plan: PetSitcomPlan) -> None:
    review = _read_json_object(_anchor_review_path(plan))
    if review.get("schema_version") != ANCHOR_REVIEW_SCHEMA:
        raise PetSitcomGenerationError("Anchor review uses an unsupported schema.")
    if review.get("completed") is not True:
        raise PetSitcomGenerationError(
            "Anchor review must be completed before the mouth test."
        )
    if review.get("approved") is not True:
        raise PetSitcomGenerationError(
            "Approved character and scene anchors are required first."
        )
    if any(review.get(field) is not True for field in _ANCHOR_REVIEW_FIELDS):
        raise PetSitcomGenerationError(
            "Approved anchor review requires every boolean check to be true."
        )
    if review.get("source_hashes") != _anchor_hashes(plan):
        raise PetSitcomGenerationError(
            "Approved anchor review hashes no longer match current PNG files."
        )


def _validate_mouth_candidate(
    plan: PetSitcomPlan, candidate_number: int, retry_reason: str
) -> None:
    if candidate_number == 3 or candidate_number not in {1, 2}:
        raise PetSitcomGenerationError(
            "mouth test candidate 3 and later are not allowed."
        )
    if not isinstance(retry_reason, str):
        raise PetSitcomGenerationError("mouth test retry reason must be a string.")
    if candidate_number == 1 and retry_reason:
        raise PetSitcomGenerationError(
            "mouth test candidate 1 must use an empty retry reason."
        )
    if candidate_number == 2 and retry_reason not in _RETRY_REASONS:
        raise PetSitcomGenerationError(
            "mouth test candidate 2 requires an allowed retry reason."
        )
    _raise_if_mouth_workflow_stopped(plan)
    first_review = _completed_mouth_review(plan, 1)
    second_review = _completed_mouth_review(plan, 2)
    if second_review:
        _validate_mouth_review(plan, second_review, 2)
        if second_review.get("passed") is False:
            _persist_terminal_mouth_failure(plan, second_review)
            raise PetSitcomGenerationError(
                "candidate 2 failed; the mouth-test workflow is stopped."
            )
        raise PetSitcomGenerationError(
            "candidate 2 is already reviewed; no further mouth-test generation is allowed."
        )
    if candidate_number == 1:
        if first_review:
            _validate_mouth_review(plan, first_review, 1)
            raise PetSitcomGenerationError(
                "candidate 1 is already reviewed; continue to candidate 2 only after a failed review."
            )
        return
    if first_review.get("passed") is not False:
        raise PetSitcomGenerationError(
            "candidate 2 requires a failed candidate-1 review."
        )
    _require_current_mouth_provenance(plan, 1, "")
    _validate_mouth_review(plan, first_review, 1)
    if first_review.get("retry_reason") != retry_reason:
        raise PetSitcomGenerationError(
            "mouth test candidate 2 retry reason must match the failed candidate 1 review."
        )


def _mouth_test_prompt(retry_reason: str = "") -> str:
    base_prompt = (
        "Show only Naitang, the orange-and-white cat from reference 1, in a medium close shot "
        "inside the living room from reference 2. Use a fixed camera. Naitang alone says exactly "
        '"我昨晚一直在睡觉。" with native audio. Keep photorealistic feline face, realistic whiskers, '
        "and normal cat mouth anatomy. Use restrained jaw opening and visible but subtle mouth and jaw "
        "movement. Forbid a human-like mouth, human teeth, exaggerated lips, any other voice, any other "
        "animal, person, text, subtitle, logo, or watermark. Reserve 0.20 seconds of silent closed mouth "
        "at both the beginning and the end."
    )
    if not retry_reason:
        return base_prompt
    return f"{base_prompt} Retry correction: {MOUTH_RETRY_SUFFIXES[retry_reason]}"


def _mouth_candidate_provenance(
    plan: PetSitcomPlan,
    candidate_number: int,
    retry_reason: str,
    base_prompt: str,
    prompt: str,
    gateway_report: Path,
) -> dict[str, Any]:
    references = [plan.characters[0].reference_path, plan.scenes[0].anchor_path]
    return {
        "schema_version": MOUTH_TEST_SCHEMA,
        "candidate_number": candidate_number,
        "provider": VIDEO_PROVIDER,
        "model": VIDEO_MODEL,
        "duration": MOUTH_TEST_DURATION,
        "ratio": MOUTH_TEST_RATIO,
        "resolution": MOUTH_TEST_RESOLUTION,
        "generate_audio": True,
        "base_prompt_sha256": _hash_payload({"prompt": base_prompt}),
        "prompt_sha256": _hash_payload({"prompt": prompt}),
        "retry_reason": "" if candidate_number == 1 else retry_reason,
        "retry_suffix": ""
        if candidate_number == 1
        else MOUTH_RETRY_SUFFIXES[retry_reason],
        "reference_paths": [str(path.resolve()) for path in references],
        "reference_sha256": [_sha256_file(path) for path in references],
        "gateway_report_path": str(gateway_report.resolve()),
        "video_sha256": "",
        "provider_success": False,
    }


def _mouth_candidate_is_reusable(
    candidate: Path, expected: Mapping[str, Any]
) -> bool:
    state = _read_json_object(_mouth_candidate_state_path(candidate))
    gateway = _read_json_object(_mouth_gateway_report_path(candidate))
    static_fields = tuple(
        field
        for field in _MOUTH_PROVENANCE_FIELDS
        if field not in {"video_sha256", "provider_success"}
    )
    return bool(
        candidate.is_file()
        and state.get("provider_success") is True
        and state.get("video_sha256") == _file_hash_or_empty(candidate)
        and all(
            state.get(key) == expected.get(key) for key in static_fields
        )
        and gateway.get("schema_version")
        == "motion-comic-factory.gateway-video.v2"
        and gateway.get("success") is True
        and gateway.get("provider") == VIDEO_PROVIDER
        and gateway.get("model") == VIDEO_MODEL
        and gateway.get("pet_sitcom_mouth_provenance") == state
    )


def _require_current_mouth_provenance(
    plan: PetSitcomPlan, candidate_number: int, retry_reason: str
) -> dict[str, Any]:
    candidate = _mouth_candidate_path(plan, candidate_number)
    gateway_report = _mouth_gateway_report_path(candidate)
    state_path = _mouth_candidate_state_path(candidate)
    state = _read_json_object(state_path)
    base_prompt = _mouth_test_prompt("")
    prompt = _mouth_test_prompt(retry_reason)
    expected = _mouth_candidate_provenance(
        plan,
        candidate_number,
        retry_reason,
        base_prompt,
        prompt,
        gateway_report,
    )
    expected["provider_success"] = True
    expected["video_sha256"] = _file_hash_or_empty(candidate)
    if not candidate.is_file() or any(
        state.get(field) != expected.get(field) for field in _MOUTH_PROVENANCE_FIELDS
    ):
        raise PetSitcomGenerationError(
            f"mouth test candidate {candidate_number} provenance is missing or stale."
        )
    gateway = _read_json_object(gateway_report)
    embedded = gateway.get("pet_sitcom_mouth_provenance")
    if (
        gateway.get("schema_version") != "motion-comic-factory.gateway-video.v2"
        or gateway.get("success") is not True
        or gateway.get("provider") != VIDEO_PROVIDER
        or gateway.get("model") != VIDEO_MODEL
        or not isinstance(embedded, Mapping)
        or any(
            embedded.get(field) != state.get(field)
            for field in _MOUTH_PROVENANCE_FIELDS
        )
    ):
        raise PetSitcomGenerationError(
            f"mouth test candidate {candidate_number} gateway provenance is missing or stale."
        )
    return state


def _prepare_mouth_candidate_regeneration(
    plan: PetSitcomPlan,
    candidate_number: int,
    candidate: Path,
    prompt: str,
    provenance: Mapping[str, Any],
    video_client: GatewayVideoClient,
) -> bool:
    artifacts = _mouth_candidate_artifacts(plan, candidate_number, candidate)
    if not any(path.exists() for path in artifacts):
        return False
    gateway_state = _read_json_object(_mouth_gateway_clip_state_path(candidate))
    signature = _mouth_gateway_signature(plan, candidate, prompt, video_client)
    matching_gateway_state = bool(
        gateway_state.get("schema_version") == _gateway_video_batch.CLIP_STATE_SCHEMA
        and gateway_state.get("signature") == signature
        and gateway_state.get("model") == VIDEO_MODEL
    )
    if (
        matching_gateway_state
        and gateway_state.get("status") == "submitted"
        and (gateway_state.get("task_id") or gateway_state.get("video_url"))
    ):
        return False
    if matching_gateway_state and gateway_state.get("status") == "submitting":
        raise PetSitcomGenerationError(
            "mouth test has an ambiguous same-signature gateway submission; explicit handling is required."
        )
    if _has_current_mouth_human_evidence(plan, candidate_number, candidate):
        raise PetSitcomGenerationError(
            "mouth test candidate has current human review evidence; resolve that evidence or use a new candidate before regeneration."
        )
    _archive_stale_mouth_artifacts(
        plan,
        candidate_number,
        candidate,
        artifacts,
        str(provenance["prompt_sha256"]),
    )
    return True


def _mouth_gateway_signature(
    plan: PetSitcomPlan,
    candidate: Path,
    prompt: str,
    video_client: GatewayVideoClient,
) -> str:
    references = [
        str(plan.characters[0].reference_path),
        str(plan.scenes[0].anchor_path),
    ]
    job = _gateway_video_batch.GatewayVideoJob(
        shot_id="single",
        index=1,
        prompt=prompt,
        images=tuple(references),
        duration=MOUTH_TEST_DURATION,
        ratio=MOUTH_TEST_RATIO,
        resolution=MOUTH_TEST_RESOLUTION,
        output_path=str(candidate),
    )
    return _gateway_video_batch._job_signature(
        job,
        model=str(video_client.config.model),
        generate_audio=True,
        endpoint_fingerprint=(
            _gateway_video_batch.gateway_endpoint_fingerprint(
                video_client.config.base_url
            )
        ),
    )


def _has_current_mouth_human_evidence(
    plan: PetSitcomPlan, candidate_number: int, candidate: Path
) -> bool:
    review = _read_json_object(_mouth_review_path(plan, candidate_number))
    return bool(
        candidate.is_file()
        and review.get("candidate_number") == candidate_number
        and review.get("mp4_sha256") == _file_hash_or_empty(candidate)
        and (review.get("completed") is True or review.get("approved") is True)
    )


def _mouth_candidate_artifacts(
    plan: PetSitcomPlan, candidate_number: int, candidate: Path
) -> tuple[Path, ...]:
    return (
        candidate,
        _mouth_gateway_report_path(candidate),
        _mouth_candidate_state_path(candidate),
        _mouth_gateway_clip_state_path(candidate),
        _mouth_report_path(candidate),
        _mouth_review_path(plan, candidate_number),
    )


def _archive_stale_mouth_artifacts(
    plan: PetSitcomPlan,
    candidate_number: int,
    candidate: Path,
    artifacts: tuple[Path, ...],
    prompt_sha256: str,
) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive = (
        candidate.parent
        / "archive"
        / f"candidate_{candidate_number:03d}"
        / f"{stamp}-{prompt_sha256[:12]}"
    )
    _require_pet_output_path(archive, plan.output_dir, "Mouth archive path")
    for artifact in artifacts:
        if artifact.is_symlink():
            raise PetSitcomGenerationError(
                f"Mouth artifact may not be a symlink: {artifact}"
            )
        if artifact.exists() and not artifact.is_file():
            raise PetSitcomGenerationError(
                f"Mouth artifact is not a regular file: {artifact}"
            )
    archive.mkdir(parents=True, exist_ok=False)
    for artifact in artifacts:
        if artifact.is_file():
            os.replace(artifact, archive / artifact.name)
    _fsync_directory(archive)
    _fsync_directory(archive.parent)
    _fsync_directory(candidate.parent)


def _anchor_hashes(plan: PetSitcomPlan) -> dict[str, str]:
    paths = [
        *(character.reference_path for character in plan.characters),
        *(scene.anchor_path for scene in plan.scenes),
    ]
    hashes = {
        str(path.relative_to(plan.output_dir)): _file_hash_or_empty(path)
        for path in paths
    }
    if any(not value for value in hashes.values()):
        raise PetSitcomGenerationError(
            "All character and scene anchor PNG files are required."
        )
    return hashes


def _reference_reports(paths: list[Path]) -> list[dict[str, str]]:
    return [
        {"order": str(index), "path": str(path), "sha256": _sha256_file(path)}
        for index, path in enumerate(paths, start=1)
    ]


def _anchor_state_path(output: Path) -> Path:
    return output.with_suffix(output.suffix + ".anchor-state.json")


def _anchor_review_path(plan: PetSitcomPlan) -> Path:
    return plan.output_dir / "anchor_review_template.json"


def _anchor_report_path(plan: PetSitcomPlan) -> Path:
    return plan.output_dir / "anchor_generation_report.json"


def _mouth_candidate_path(plan: PetSitcomPlan, candidate_number: int) -> Path:
    return (
        plan.output_dir
        / "tests"
        / "mouth_test"
        / f"candidate_{candidate_number:03d}.mp4"
    )


def _mouth_gateway_report_path(candidate: Path) -> Path:
    return candidate.with_suffix(".report.json")


def _mouth_report_path(candidate: Path) -> Path:
    return candidate.with_suffix(".mouth-test.json")


def _mouth_candidate_state_path(candidate: Path) -> Path:
    return candidate.with_suffix(".mouth-provenance.json")


def _mouth_gateway_clip_state_path(candidate: Path) -> Path:
    return _gateway_video_batch._clip_state_path(candidate)


def _mouth_review_path(plan: PetSitcomPlan, candidate_number: int) -> Path:
    return (
        plan.output_dir
        / "tests"
        / "mouth_test"
        / f"candidate_{candidate_number:03d}.review.json"
    )


def _mouth_terminal_state_path(plan: PetSitcomPlan) -> Path:
    return plan.output_dir / "tests" / "mouth_test" / "terminal_failure.json"


def _read_mouth_review_history(
    plan: PetSitcomPlan, candidate_number: int
) -> dict[str, Any]:
    review = _read_json_object(_mouth_review_path(plan, candidate_number))
    if review:
        return review
    legacy_review = _read_json_object(plan.mouth_test_review_path)
    if legacy_review.get("candidate_number") != candidate_number:
        return {}
    _validate_mouth_review(plan, legacy_review, candidate_number)
    migrated = dict(legacy_review)
    migrated["schema_version"] = MOUTH_REVIEW_SCHEMA
    migrated["completed"] = True
    _write_atomic_json(_mouth_review_path(plan, candidate_number), migrated)
    return migrated


def _completed_mouth_review(
    plan: PetSitcomPlan, candidate_number: int
) -> dict[str, Any]:
    review = _read_mouth_review_history(plan, candidate_number)
    if review.get("completed") is not True:
        return {}
    return review


def _write_mouth_review_template(plan: PetSitcomPlan, candidate_number: int) -> None:
    candidate = _mouth_candidate_path(plan, candidate_number)
    path = _mouth_review_path(plan, candidate_number)
    existing = _read_json_object(path)
    if existing.get("completed") is True:
        return
    if (
        existing.get("schema_version") == MOUTH_REVIEW_SCHEMA
        and existing.get("candidate_number") == candidate_number
        and existing.get("mp4_sha256") == _file_hash_or_empty(candidate)
    ):
        return
    _write_atomic_json(
        path,
        {
            "schema_version": MOUTH_REVIEW_SCHEMA,
            "candidate_number": candidate_number,
            "mp4_sha256": _sha256_file(candidate),
            "completed": False,
            **{field: False for field in _MOUTH_REVIEW_FIELDS},
            "passed": None,
            "notes": "",
            "retry_reason": "",
        },
    )


def _raise_if_mouth_workflow_stopped(plan: PetSitcomPlan) -> None:
    terminal = _read_json_object(_mouth_terminal_state_path(plan))
    if (
        terminal.get("schema_version") == MOUTH_TERMINAL_SCHEMA
        and terminal.get("status") == "terminal_failure"
        and terminal.get("candidate_number") == 2
    ):
        raise PetSitcomGenerationError(
            "candidate 2 failed; the mouth-test workflow is stopped."
        )


def _persist_terminal_mouth_failure(
    plan: PetSitcomPlan, review: Mapping[str, Any]
) -> None:
    _write_atomic_json(
        _mouth_terminal_state_path(plan),
        {
            "schema_version": MOUTH_TERMINAL_SCHEMA,
            "status": "terminal_failure",
            "candidate_number": 2,
            "mp4_sha256": review["mp4_sha256"],
            "retry_reason": review["retry_reason"],
        },
    )


def _validate_mouth_review(
    plan: PetSitcomPlan, review: Mapping[str, Any], candidate_number: int
) -> None:
    if review.get("schema_version") not in {None, MOUTH_REVIEW_SCHEMA}:
        raise PetSitcomGenerationError("mouth test review uses an unsupported schema.")
    if review.get("candidate_number") != candidate_number:
        raise PetSitcomGenerationError("mouth test review candidate does not match.")
    if not isinstance(review.get("passed"), bool):
        raise PetSitcomGenerationError("mouth test review must record pass or fail.")
    if any(not isinstance(review.get(field), bool) for field in _MOUTH_REVIEW_FIELDS):
        raise PetSitcomGenerationError(
            "mouth test review must include every quality-check boolean."
        )
    candidate = _mouth_candidate_path(plan, candidate_number)
    if not candidate.is_file() or review.get("mp4_sha256") != _file_hash_or_empty(
        candidate
    ):
        raise PetSitcomGenerationError(
            "mouth test review hash no longer matches the current candidate."
        )
    if (
        review.get("passed") is False
        and review.get("retry_reason") not in _RETRY_REASONS
    ):
        raise PetSitcomGenerationError(
            "Failed mouth test requires an allowed retry reason."
        )


def _validate_plan(plan: PetSitcomPlan) -> None:
    if not isinstance(plan, PetSitcomPlan):
        raise PetSitcomGenerationError("plan must be a PetSitcomPlan.")
    root = plan.output_dir.expanduser().absolute()
    _reject_pet_symlink_components(root, "Pet sitcom output directory")
    for path in [
        *(character.reference_path for character in plan.characters),
        *(scene.anchor_path for scene in plan.scenes),
        *(_anchor_state_path(character.reference_path) for character in plan.characters),
        *(_anchor_state_path(scene.anchor_path) for scene in plan.scenes),
        _anchor_review_path(plan),
        _anchor_report_path(plan),
        plan.audio_manifest_path,
        plan.audio_probe_path,
        plan.audio_probe_review_path,
        plan.generation_report_path,
        plan.selection_path,
    ]:
        _require_pet_output_path(path, root, "Pet sitcom output path")
    for shot in plan.shots:
        for candidate_number in (1, 2, 3, 4, 5, 6):
            candidate = _pet_candidate_path(shot, candidate_number)
            paths = [
                candidate,
                _pet_gateway_report_path(candidate),
                _pet_candidate_state_path(candidate),
                _gateway_video_batch._clip_state_path(candidate),
            ]
            for path in paths:
                _require_pet_output_path(path, root, "Pet shot output path")
        continuity_frame = _pet_continuity_frame_path(plan, shot.shot_id)
        for path in (
            continuity_frame,
            _pet_continuity_state_path(continuity_frame),
        ):
            _require_pet_output_path(
                path, root, "Pet continuity output path"
            )


def _require_pet_output_path(path: Path, root: Path, label: str) -> None:
    target = path.expanduser().absolute()
    _reject_pet_symlink_components(target, label)
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise PetSitcomGenerationError(
            f"{label} escapes its output directory: {path}"
        ) from exc


def _reject_pet_symlink_components(path: Path, label: str) -> None:
    absolute = path.expanduser().absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise PetSitcomGenerationError(f"{label} may not use a symlink: {current}")


def _require_client_model(client: Any, expected: str, kind: str) -> None:
    actual = str(getattr(getattr(client, "config", None), "model", "")).strip()
    if actual != expected:
        raise PetSitcomGenerationError(f"Gateway {kind} client must use {expected}.")


def _ensure_output_path(path: Path, *, output_dir: Path | None = None) -> None:
    if output_dir is not None:
        _require_pet_output_path(path, output_dir, "Generated output")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise PetSitcomGenerationError(f"Generated output may not be a symlink: {path}")


def _temporary_path(destination: Path, suffix: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=suffix,
    )
    os.close(descriptor)
    return Path(temporary_name)


def _convert_to_rgb_png(source: Path, destination: Path) -> None:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(source) as image:
            image.convert("RGB").save(destination, format="PNG")
    except (OSError, UnidentifiedImageError) as exc:
        raise PetSitcomGenerationError(
            "Anchor provider output could not be decoded."
        ) from exc


def _require_png(path: Path, label: str) -> None:
    if not _is_png_file(path):
        raise PetSitcomGenerationError(
            f"{label.capitalize()} is not a true PNG: {path}"
        )


def _is_png_file(path: Path) -> bool:
    try:
        return path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise PetSitcomGenerationError(
            f"Unable to hash local artifact: {path}"
        ) from exc
    return digest.hexdigest()


def _file_hash_or_empty(path: Path) -> str:
    try:
        return _sha256_file(path)
    except PetSitcomGenerationError:
        return ""


def _hash_payload(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_report(
    path: Path,
    report: dict[str, Any],
    *clients: Any,
    output_dir: Path | None = None,
) -> None:
    _write_atomic_json(
        path,
        _sanitize_report(report, _client_secrets(*clients)),
        output_dir=output_dir,
    )


def _write_atomic_json(
    path: Path, payload: dict[str, Any], *, output_dir: Path | None = None
) -> None:
    _ensure_output_path(path, output_dir=output_dir)
    temporary = _temporary_path(path, ".json")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _client_secrets(*clients: Any) -> tuple[str, ...]:
    values = []
    for client in clients:
        value = getattr(getattr(client, "config", None), "api_key", "")
        if isinstance(value, str) and value:
            values.append(value)
    return tuple(values)


def _sanitize_error(error: Exception, client: Any) -> str:
    return str(_sanitize_report(str(error), _client_secrets(client)))


def _sanitize_json_file(
    path: Path, secrets: tuple[str, ...], *, output_dir: Path | None = None
) -> None:
    payload = _read_json_object(path)
    if payload:
        _write_atomic_json(
            path, _sanitize_report(payload, secrets), output_dir=output_dir
        )


def _sanitize_report(value: Any, known_secrets: tuple[str, ...] = ()) -> Any:
    sensitive = {
        "authorization",
        "apikey",
        "accesskey",
        "token",
        "secret",
        "credential",
        "clientsecret",
        "privatekey",
        "password",
        "passwd",
        "response",
        "signature",
        "sessiontoken",
    }
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            compact = re.sub(r"[^a-z]", "", str(key).lower())
            result[str(key)] = (
                "[redacted]"
                if any(word in compact for word in sensitive)
                else _sanitize_report(item, known_secrets)
            )
        return result
    if isinstance(value, list):
        return [_sanitize_report(item, known_secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_report(item, known_secrets) for item in value)
    if not isinstance(value, str):
        return value
    sanitized = value
    for secret in known_secrets:
        sanitized = sanitized.replace(secret, "[redacted]") if secret else sanitized
    sanitized = re.sub(
        r"(?i)\bdata:[^,\s<>'\"]+,[^\s<>'\"]+", "[inline-data]", sanitized
    )
    sanitized = re.sub(r"https?://[^\s<>'\"]+", "[remote-url]", sanitized)
    sanitized = re.sub(
        r"(?i)\bauthorization(?:\s*[:=]|\s+)[^\r\n]*",
        "Authorization: [redacted]",
        sanitized,
    )
    sanitized = re.sub(
        r"(?i)\b(?:client[-_ ]?secret|private[-_ ]?key|password|passwd|response|"
        r"signature|credentials?|session[-_ ]?token|x[-_ ]?api[-_ ]?key|"
        r"(?:access[-_ ]?)?token|(?:access[-_ ]?)?secret)\s*[:=]\s*"
        r"[^\r\n]*",
        "[redacted]",
        sanitized,
    )
    return re.sub(
        r"(?i)\b(?:bearer|token|secret)\s+(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        "[redacted]",
        sanitized,
    )


def _report_with_paths(report: dict[str, Any]) -> dict[str, Any]:
    result = dict(report)
    for key in (
        "candidate_path",
        "report_path",
        "gateway_report_path",
        "state_path",
    ):
        if key in result:
            result[key] = Path(str(result[key]))
    return result
