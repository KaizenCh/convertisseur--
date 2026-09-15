# -*- coding: utf-8 -*-
"""
Actor and component classification, with external rule support (§9.5).
"""

from typing import Any, Dict, List, Optional
from ue2godot.core.ids import class_name


def classify_actor(actor: Any, rules: Optional[List[Dict[str, str]]] = None) -> str:
    if actor is None:
        return "other"
    try:
        cname = class_name(actor) or ""
    except Exception:
        cname = ""

    if rules:
        for rule in rules:
            if not isinstance(rule, dict):
                continue
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
    if component is None:
        return "other"
    try:
        cname = class_name(component) or ""
    except Exception:
        cname = ""

    if rules:
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            match = rule.get("match", "")
            kind = rule.get("kind", "")
            if match and match in cname:
                return kind

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
