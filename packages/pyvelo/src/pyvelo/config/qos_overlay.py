"""Build the UI's edge QoS insert payload from the profile and planned edits."""

import copy
from typing import Any

from pyvelo.client.models.configuration.common import segment_id_of


def qos_overlay_data(
    data: dict[str, Any], profile_data: dict[str, Any]
) -> dict[str, Any]:
    """Keep the profile catalog inherited and the planned rules edge-owned.

    Copy raw profile values so inherited fields retain their wire format and
    unknown keys. Existing edge modules never pass through this conversion.
    """
    overlay = copy.deepcopy(profile_data)
    overlay.update(copy.deepcopy(data))
    profile_segments = profile_data.get("segments", [])
    segments: list[dict[str, Any]] = []
    for entry in data.get("segments", []):
        profile_segment = next(
            (
                candidate
                for candidate in profile_segments
                if segment_id_of(candidate) == segment_id_of(entry)
            ),
            {},
        )
        segment = copy.deepcopy(profile_segment)
        segment.update(copy.deepcopy(entry))
        segment["defaults"] = copy.deepcopy(entry.get("defaults", []))
        segment["inheritedRules"] = copy.deepcopy(profile_segment.get("rules", []))
        segment["inheritedDefaults"] = copy.deepcopy(
            profile_segment.get("defaults", [])
        )
        segment["cosMappingOverride"] = entry.get("cosMappingOverride", False)
        segments.append(segment)
    overlay["segments"] = segments
    for key in (
        "serviceRateLimitOverride",
        "qosClientConnectorOverride",
        "customizableQOEOverride",
    ):
        overlay[key] = data.get(key, False)
    return overlay
