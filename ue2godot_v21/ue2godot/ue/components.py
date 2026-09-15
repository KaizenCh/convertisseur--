# -*- coding: utf-8 -*-
"""
Fiches détaillées par type de composant.

PORTÉ depuis unreal_export_manifest_v10-8.py (static_mesh_component_info,
decal_component_info, niagara_component_info, light_component_info,
audio_component_info, landscape_info, blueprint_component_detail,
actor_origin_type, component_statistics).

RÈGLE STRUCTURANTE HÉRITÉE, valable pour TOUS les types
-------------------------------------------------------
Tout composant passe par `component_transform_diagnostic()`, jamais par
`get_component_transform()` nu. La référence a découvert cela en deux
temps : d'abord pour les StaticMesh (V10.2), puis en constatant que les
Decal et Niagara vivant dans des Blueprints à l'intérieur de
LevelInstances heurtaient exactement la même référence périmée et
renvoyaient silencieusement `transform: null`. Le correctif y est resté
partiel — `blueprint_component_detail` utilisait encore l'appel nu. Ici,
il n'existe aucun chemin qui contourne le diagnostic : c'est le seul moyen
d'être sûr que la généralisation ne réintroduira pas ce trou sur un type
de composant ajouté plus tard.

GÉNÉRALISATIONS au-delà de la map de référence
-----------------------------------------------
  - SkeletalMeshComponent, SplineComponent, TextRender et volumes de
    post-traitement reconnus (absents de la map de référence, présents
    dans la plupart des autres) ;
  - lumières : type déduit de la classe ET des propriétés réellement
    présentes (cône intérieur/extérieur pour un spot, longueur pour une
    rect light), là où la référence ne lisait qu'un jeu fixe ;
  - audio : atténuation et déclenchement automatique capturés, pour que la
    décision « marqueur ou source réelle » puisse être prise plus tard sur
    des données plutôt qu'en devinant ;
  - toute fiche renvoie une clé `kind` normalisée : c'est elle que
    consomme le reconstructeur, jamais le nom de classe Unreal, qui varie
    entre versions du moteur.
"""

from typing import Any, Dict, List, Optional

from ue2godot.core.safe import safe_call, safe_property, safe_float, safe_bool
from ue2godot.core.ids import object_path, object_name, class_name, class_path
from ue2godot.ue.materials import material_full_info, component_materials
from ue2godot.ue.assets import mesh_info, asset_info

try:
    import unreal
except ImportError:
    unreal = None


def vector_to_list(vector: Any) -> Optional[List[float]]:
    if vector is None:
        return None
    try:
        return [float(vector.x), float(vector.y), float(vector.z)]
    except Exception:
        return None


def color_to_dict(color: Any) -> Optional[Dict[str, float]]:
    if color is None:
        return None
    try:
        return {"r": float(color.r), "g": float(color.g),
                "b": float(color.b), "a": float(getattr(color, "a", 1.0))}
    except Exception:
        return None


def actor_origin_type(actor: Any) -> str:
    """D'où vient cet acteur : placé à la main, Blueprint, ou système.

    Distinction utile en aval : un acteur issu d'un Blueprint peut porter
    une logique non convertible, alors qu'un StaticMeshActor placé à la
    main est intégralement représentable.
    """
    cname = class_name(actor) or ""
    cpath = class_path(actor) or ""
    if cpath.endswith("_C") and (cpath.startswith("/Game/") or cpath.startswith("/Plugin")):
        return "blueprint"
    if "WorldPartition" in cname or "WorldDataLayers" in cname or "LevelBounds" in cname:
        return "system"
    if "LevelInstance" in cname:
        return "level_instance"
    return "placed"


def _actor_block(actor: Any) -> Dict[str, Any]:
    return {
        "path": object_path(actor), "name": object_name(actor),
        "class": class_name(actor), "class_path": class_path(actor),
        "origin_type": actor_origin_type(actor),
    }


def _component_block(component: Any) -> Dict[str, Any]:
    return {
        "path": object_path(component), "name": object_name(component),
        "class": class_name(component), "class_path": class_path(component),
    }


def _base_record(actor, component, kind, transform_fn,
                 source_level=None, level_instance_chain=None) -> Dict[str, Any]:
    """Squelette commun — garantit qu'aucun type ne contourne le diagnostic
    de transform ni n'oublie sa chaîne de LevelInstances."""
    value, error = transform_fn(component, actor)
    return {
        "kind": kind,
        "actor": _actor_block(actor),
        "component": _component_block(component),
        "source_level": object_path(source_level) if source_level else "",
        "level_instance_chain": list(level_instance_chain or []),
        "transform": value,
        "transform_extraction_error": error,
        "actor_transform": None,
        "mobility": str(safe_property(component, "mobility", "") or ""),
    }


# ----------------------------------------------------------------------
# GÉOMÉTRIE
# ----------------------------------------------------------------------

def extract_instance_transforms(component: Any, instance_count: int,
                                transform_to_dict) -> Dict[str, Any]:
    """Transform monde de CHAQUE instance d'un ISM/HISM.

    Trois signatures d'appel essayées : `get_instance_transform` a changé
    de convention entre versions du moteur, et une seule d'entre elles
    fonctionne sur une version donnée. Sans ce repli, un composant à 165
    instances ne produisait qu'un seul placement — bug mesuré sur la map de
    référence (V10.4).
    """
    transforms: List[Optional[Dict]] = []
    errors: List[int] = []
    for index in range(instance_count):
        raw = None
        for attempt in (
            lambda: component.get_instance_transform(index, True),
            lambda: component.get_instance_transform(index, world_space=True),
            lambda: component.get_instance_transform(instance_index=index, world_space=True),
        ):
            try:
                candidate = attempt()
            except Exception:
                candidate = None
            if candidate is not None:
                raw = candidate
                break
        if raw is None:
            errors.append(index)
            transforms.append(None)
            continue
        try:
            transforms.append(transform_to_dict(raw))
        except Exception:
            errors.append(index)
            transforms.append(None)
    return {"instance_transforms": transforms, "instance_transform_errors": errors}


def static_mesh_component_info(actor, component, transform_fn, transform_to_dict,
                               source_level=None, level_instance_chain=None,
                               deep_mesh: bool = True) -> Dict[str, Any]:
    cls = class_name(component) or ""
    if "HierarchicalInstancedStaticMesh" in cls:
        kind = "hierarchical_instanced_mesh"
    elif "InstancedStaticMesh" in cls:
        kind = "instanced_mesh"
    else:
        kind = "static_mesh"

    record = _base_record(actor, component, kind, transform_fn,
                          source_level, level_instance_chain)
    mesh = safe_property(component, "static_mesh", None)
    record["mesh"] = mesh_info(mesh, deep=deep_mesh)
    record["materials"] = component_materials(component)
    record["empty_mesh_slot"] = mesh is None

    instance_count = 1
    if kind in ("instanced_mesh", "hierarchical_instanced_mesh"):
        instance_count = safe_call(lambda: int(component.get_instance_count()), 0) or 0
        if instance_count > 0:
            record.update(extract_instance_transforms(
                component, instance_count, transform_to_dict))
    record["instance_count"] = instance_count

    collision: Dict[str, Any] = {}
    enabled = safe_call(lambda: component.get_editor_property("collision_enabled"), None)
    if enabled is not None:
        collision["enabled"] = str(enabled)
    body = safe_call(lambda: component.get_editor_property("body_instance"), None)
    if body is not None:
        profile = safe_call(lambda: body.get_editor_property("collision_profile_name"), None)
        if profile is not None:
            collision["profile"] = str(profile)
    record["collision"] = collision

    for prop, key in (("cast_shadow", "cast_shadow"),
                      ("visible", "visible"),
                      ("hidden_in_game", "hidden_in_game")):
        value = safe_property(component, prop, None)
        if value is not None:
            record[key] = bool(value)
    return record


def skeletal_mesh_component_info(actor, component, transform_fn,
                                 source_level=None, level_instance_chain=None,
                                 deep_mesh: bool = True) -> Dict[str, Any]:
    """Absent de la référence. Exporté en pose de repos côté meshes ; la
    fiche signale explicitement ce qui NE traversera pas (animation,
    squelette, physics asset), pour qu'aucun consommateur ne suppose le
    contraire."""
    record = _base_record(actor, component, "skeletal_mesh", transform_fn,
                          source_level, level_instance_chain)
    mesh = (safe_property(component, "skeletal_mesh", None)
            or safe_property(component, "skeletal_mesh_asset", None))
    record["mesh"] = mesh_info(mesh, deep=deep_mesh) if mesh is not None else None
    record["materials"] = component_materials(component)
    record["skeleton"] = asset_info(safe_property(mesh, "skeleton", None)) if mesh else None
    record["physics_asset"] = asset_info(safe_property(component, "physics_asset_override", None))
    record["anim_class"] = str(safe_property(component, "anim_class", "") or "")
    record["conversion_note"] = (
        "Exporté en pose de repos uniquement : ni animation, ni squelette, "
        "ni physics asset ne traversent la conversion."
    )
    return record


# ----------------------------------------------------------------------
# EFFETS
# ----------------------------------------------------------------------

def decal_component_info(actor, component, transform_fn,
                         source_level=None, level_instance_chain=None,
                         include_material_parameters: bool = True) -> Dict[str, Any]:
    record = _base_record(actor, component, "decal", transform_fn,
                          source_level, level_instance_chain)
    material = safe_property(component, "decal_material", None)
    record["material"] = material_full_info(
        material, include_parameters=include_material_parameters)
    record["material_path"] = object_path(material) if material else ""
    record["size"] = vector_to_list(safe_property(component, "decal_size", None)) \
        or [256.0, 256.0, 256.0]

    # Ordre de tri et fondu : deux décals superposés se départagent par
    # sort_order côté Unreal. Sans cette donnée, leur empilement côté Godot
    # est arbitraire, et un décal de sang peut disparaître sous une tache
    # de saleté alors qu'il était visible dans l'original.
    for prop, key, cast in (("sort_order", "sort_order", int),
                            ("fade_screen_size", "fade_screen_size", float),
                            ("fade_start_delay", "fade_start_delay", float),
                            ("fade_duration", "fade_duration", float)):
        value = safe_property(component, prop, None)
        if value is not None:
            try:
                record[key] = cast(value)
            except Exception:
                pass
    return record


def niagara_component_info(actor, component, transform_fn,
                           source_level=None, level_instance_chain=None) -> Dict[str, Any]:
    record = _base_record(actor, component, "niagara", transform_fn,
                          source_level, level_instance_chain)
    system = None
    for prop in ("asset", "template", "niagara_system_asset"):
        system = safe_property(component, prop, None)
        if system is not None:
            break
    record["system"] = asset_info(system)
    record["system_name"] = object_name(system) if system else "UnknownNiagara"
    record["auto_activate"] = safe_bool(safe_property(component, "auto_activate", None), True)
    record["conversion_note"] = (
        "Niagara n'a pas d'équivalent Godot : seuls le placement et le nom "
        "du système traversent. Le rendu est reconstruit approximativement "
        "ou remplacé par un marqueur, selon vfx.mode."
    )
    return record


def light_component_info(actor, component, transform_fn,
                         source_level=None, level_instance_chain=None) -> Dict[str, Any]:
    """Type de lumière déduit de la classe, propriétés lues selon ce type.

    Lire un jeu de propriétés fixe (comme la référence) donne des valeurs
    absentes sur les spots et les rect lights, dont les paramètres
    déterminants ne sont pas ceux d'une point light.
    """
    cls = class_name(component) or ""
    if "Directional" in cls:
        light_type = "directional"
    elif "Spot" in cls:
        light_type = "spot"
    elif "Rect" in cls:
        light_type = "rect"
    elif "Sky" in cls:
        light_type = "sky"
    else:
        light_type = "point"

    record = _base_record(actor, component, "light", transform_fn,
                          source_level, level_instance_chain)
    record["light_type"] = light_type
    record["intensity"] = safe_float(safe_property(component, "intensity", None), 0.0)
    record["color"] = color_to_dict(safe_property(component, "light_color", None))
    record["cast_shadows"] = safe_bool(safe_property(component, "cast_shadows", None), True)
    record["intensity_units"] = str(safe_property(component, "intensity_units", "") or "")
    record["temperature"] = safe_float(safe_property(component, "temperature", None), None)
    record["use_temperature"] = safe_bool(safe_property(component, "use_temperature", None), False)

    if light_type in ("point", "spot", "rect"):
        record["attenuation_radius"] = safe_float(
            safe_property(component, "attenuation_radius", None), 0.0)
        record["source_radius"] = safe_float(
            safe_property(component, "source_radius", None), None)
    if light_type == "spot":
        record["inner_cone_angle"] = safe_float(
            safe_property(component, "inner_cone_angle", None), None)
        record["outer_cone_angle"] = safe_float(
            safe_property(component, "outer_cone_angle", None), None)
    if light_type == "rect":
        record["source_width"] = safe_float(safe_property(component, "source_width", None), None)
        record["source_height"] = safe_float(safe_property(component, "source_height", None), None)
    if light_type == "directional":
        record["dynamic_shadow_distance"] = safe_float(
            safe_property(component, "dynamic_shadow_distance_movable_light", None), None)
    return record


def audio_component_info(actor, component, transform_fn,
                         source_level=None, level_instance_chain=None) -> Dict[str, Any]:
    record = _base_record(actor, component, "audio", transform_fn,
                          source_level, level_instance_chain)
    sound = safe_property(component, "sound", None)
    record["sound"] = asset_info(sound)
    record["sound_path"] = object_path(sound) if sound else ""
    record["auto_activate"] = safe_bool(safe_property(component, "auto_activate", None), True)
    record["volume_multiplier"] = safe_float(
        safe_property(component, "volume_multiplier", None), 1.0)
    record["pitch_multiplier"] = safe_float(
        safe_property(component, "pitch_multiplier", None), 1.0)

    # Rayon d'atténuation : sans lui, on ne peut pas décider entre un
    # AudioStreamPlayer3D avec portée correcte et un simple marqueur.
    attenuation = safe_property(component, "attenuation_settings", None)
    if attenuation is not None:
        record["attenuation_asset"] = object_path(attenuation)
    settings = safe_property(component, "adjusted_attenuation_settings", None)
    if settings is not None:
        radius = safe_property(settings, "attenuation_shape_extents", None)
        if radius is not None:
            record["attenuation_extents"] = vector_to_list(radius)
    return record


def landscape_info(actor) -> Dict[str, Any]:
    """Fiche d'un acteur Landscape.

    Le terrain n'est pas exportable comme un mesh : il est reconstruit par
    raycast puis texturé par bake. Cette fiche sert à savoir si cette
    reconstruction est possible (matériau assigné, composants présents)
    avant de lancer une passe coûteuse.
    """
    record: Dict[str, Any] = {
        "actor": _actor_block(actor),
        "kind": "landscape",
    }
    material = safe_property(actor, "landscape_material", None)
    record["material"] = asset_info(material)
    record["has_custom_material"] = material is not None

    for prop, key in (("landscape_guid", "guid"),
                      ("components_x", "components_x"),
                      ("components_y", "components_y"),
                      ("subsection_size_quads", "subsection_size_quads")):
        value = safe_property(actor, prop, None)
        if value is not None:
            record[key] = str(value)

    bounds = safe_call(lambda: actor.get_actor_bounds(False), None)
    if bounds is not None:
        try:
            origin, extent = bounds
            record["bounds"] = {
                "origin": [float(origin.x), float(origin.y), float(origin.z)],
                "extent": [float(extent.x), float(extent.y), float(extent.z)],
            }
        except Exception:
            pass

    if not record["has_custom_material"]:
        record["warning"] = (
            "Aucun matériau personnalisé assigné : le bake produirait le "
            "damier par défaut d'Unreal, pas le rendu voulu."
        )
    return record


def component_statistics(components: List[Any], classify_fn) -> Dict[str, int]:
    """Répartition des composants par type normalisé.

    Utile comme contrôle croisé : si le manifeste déclare 400 composants
    décals mais que la decal map n'en résout que 3 matériaux, l'écart est
    visible sans ouvrir les fichiers.
    """
    stats: Dict[str, int] = {}
    for component in components or []:
        kind = classify_fn(component)
        stats[kind] = stats.get(kind, 0) + 1
    return stats


# Table de dispatch : un type de composant → son constructeur de fiche.
# Ajouter un type se fait ICI, et le scanner le prend en compte sans
# modification — c'est ce qui évite qu'un nouveau type soit capturé par le
# scanner mais jamais décrit (le trou exact qu'avait la référence pour les
# SkeletalMesh).
RECORD_BUILDERS = {
    "static_mesh": "static_mesh_component_info",
    "instanced_mesh": "static_mesh_component_info",
    "hierarchical_instanced_mesh": "static_mesh_component_info",
    "skeletal_mesh": "skeletal_mesh_component_info",
    "decal": "decal_component_info",
    "niagara": "niagara_component_info",
    "particle": "niagara_component_info",
    "light": "light_component_info",
    "audio": "audio_component_info",
}
