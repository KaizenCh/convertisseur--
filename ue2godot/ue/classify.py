# -*- coding: utf-8 -*-
"""
Actor and component classification, with external rule support (§9.5).

This used to exist in two places at once: this module (never imported by
anything, with a thinner taxonomy) and a second, inline, hand-duplicated
copy inside step1_manifest.py. Whoever edited the "wrong" one would have
changed nothing about the manifest actually produced (see ANALYSE_PROFONDE_
S15 §15.9). step1_manifest.py now imports and calls these two functions —
this IS the classification, not a parallel one.

Matches by substring on the class name, like the reference
(actor_category/component_kind in unreal_export_manifest_v10-8.py) —
explicitly documented there as "the most fragile point of the system"
(§3.2.4). `rules` lets a profile override or extend this ahead of the
built-in fallback; rule order matters (§13.B.16 — most specific first),
which is why `rules` is a list, never a dict.
"""

from typing import Any, Dict, List, Optional
from ue2godot.core.ids import class_name


def classify_actor(actor: Any, rules: Optional[List[Dict[str, str]]] = None) -> str:
    cname = class_name(actor)

    if rules:
        for rule in rules:
            match = rule.get("match", "")
            cat = rule.get("category", "")
            if match and match in cname:
                return cat

    if "WorldPartition" in cname:
        return "world_partition_system"
    if "LevelInstance" in cname:
        return "level_instance"
    if "Landscape" in cname:
        return "landscape"
    if "StaticMeshActor" in cname:
        return "static_mesh"
    if "Spline" in cname:
        return "spline"
    if "Camera" in cname:
        return "camera"
    if "Widget" in cname:
        return "ui"
    return "other"


def classify_component(component: Any, rules: Optional[List[Dict[str, str]]] = None) -> str:
    cname = class_name(component)

    if rules:
        for rule in rules:
            match = rule.get("match", "")
            kind = rule.get("kind", "")
            if match and match in cname:
                return kind

    # NiagaraComponent before ParticleSystemComponent-style generic
    # "Particle" matches would be wrong order if both existed as
    # substrings of the same class name — keep the more specific
    # component kinds ahead of "particle" in this default table too,
    # for the same reason §13.B.16 orders VFX category keywords.
    if "StaticMesh" in cname:
        return "static_mesh"
    if "SkeletalMesh" in cname:
        return "skeletal_mesh"
    if "Niagara" in cname:
        return "niagara"
    if "Decal" in cname:
        return "decal"
    if "Light" in cname:
        return "light"
    if "Audio" in cname:
        return "audio"
    if "Particle" in cname:
        return "particle"
    if "Collision" in cname:
        return "collision"
    return "other"
