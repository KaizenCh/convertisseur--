# -*- coding: utf-8 -*-
"""
Step 1: manifest scanner — level_manifest_v10.json.
"""

import os
import json
import time
from typing import Dict, Any, List, Optional, Tuple

from ue2godot.core.config import ResolvedConfig
from ue2godot.core.report import StepReport
from ue2godot.core.safe import safe_call, safe_property, safe_int, safe_float, safe_bool
from ue2godot.core.ids import object_path, object_name, class_name, class_path, stable_id
from ue2godot.ue.classify import classify_actor, classify_component
from ue2godot.ue.session import enumerate_level_actors
from ue2godot.ue.scan_guard import (
    guard_before_scan, package_path_from_filesystem, ScanGuardError,
)
from ue2godot.core.registry import AssetRegistry
from ue2godot.ue.materials import (
    material_full_info, material_info, referenced_textures, texture_info,
)
from ue2godot.ue.assets import mesh_info, asset_info
from ue2godot.ue import components as comp_module

try:
    import unreal
except ImportError:
    unreal = None


MAX_LEVEL_INSTANCE_DEPTH = 64


def vector_to_list(vector):
    if vector is None:
        return None
    try:
        return [float(vector.x), float(vector.y), float(vector.z)]
    except Exception:
        return None


def color_to_dict(color):
    if color is None:
        return None
    try:
        return {"r": float(color.r), "g": float(color.g), "b": float(color.b), "a": float(color.a)}
    except Exception:
        return None


def rotator_to_dict(rotator):
    if rotator is None:
        return None
    try:
        return {"pitch": float(rotator.pitch), "yaw": float(rotator.yaw), "roll": float(rotator.roll)}
    except Exception:
        return None


def transform_to_dict(transform):
    if transform is None:
        return None
    try:
        return {
            "location": vector_to_list(transform.translation),
            "rotation": rotator_to_dict(transform.rotation.rotator()),
            "scale": vector_to_list(transform.scale3d)
        }
    except Exception:
        return None


def actor_transform(actor):
    return safe_call(lambda: transform_to_dict(actor.get_actor_transform()), None)


def _try_refetch_stale_component(component):
    try:
        path = component.get_path_name()
    except Exception:
        return None
    if not path:
        return None
    for loader_name in ("find_object", "load_object"):
        loader = getattr(unreal, loader_name, None)
        if loader is None:
            continue
        try:
            fresh = loader(None, path)
        except Exception:
            fresh = None
        if fresh is not None:
            try:
                if unreal.SystemLibrary.is_valid(fresh):
                    return fresh
            except Exception:
                return fresh
    return None


def _relative_transform_dict(component):
    location = safe_property(component, "relative_location", None)
    rotation = safe_property(component, "relative_rotation", None)
    scale = safe_property(component, "relative_scale3d", None)
    if location is None and rotation is None and scale is None:
        return None
    location_list = vector_to_list(location) or [0.0, 0.0, 0.0]
    rotation_dict = rotator_to_dict(rotation) or {"pitch": 0.0, "yaw": 0.0, "roll": 0.0}
    scale_list = vector_to_list(scale) or [1.0, 1.0, 1.0]
    return {"location": location_list, "rotation": rotation_dict, "scale": scale_list}


def component_transform_via_properties(component, actor=None, max_depth=32):
    chain = []
    current = component
    seen = set()
    depth = 0

    while current is not None and depth < max_depth:
        try:
            key = current.get_path_name()
        except Exception:
            key = None
        if key is not None:
            if key in seen:
                break
            seen.add(key)

        parent = safe_property(current, "attach_parent", None)
        if parent is None:
            break

        relative = _relative_transform_dict(current)
        if relative is None:
            return None, "relative transform unreadable on " + str(key)

        chain.append(relative)
        current = parent
        depth += 1

    if depth >= max_depth:
        return None, "attach_parent chain exceeded max depth"

    base = actor_transform(actor) if actor is not None else None
    if base is None:
        base = _relative_transform_dict(component)
        if base is None:
            return None, "no actor transform and no readable relative transform"
        return base, None

    result = base
    for relative in reversed(chain):
        result = compose_transform_dicts(result, relative)
        if result is None:
            return None, "compose_transform_dicts() failed walking attach_parent"

    return result, None


def component_transform_diagnostic(component, actor=None):
    if unreal is None:
        return None, "Unreal API unavailable"

    try:
        if not unreal.SystemLibrary.is_valid(component):
            component = _try_refetch_stale_component(component) or component
    except Exception:
        pass

    try:
        raw_transform = component.get_component_transform()
    except AttributeError as exc:
        refetched = _try_refetch_stale_component(component)
        if refetched is not None:
            try:
                raw_transform = refetched.get_component_transform()
                result = transform_to_dict(raw_transform)
                if result is not None:
                    return result, None
            except Exception:
                pass
        fallback_value, fallback_error = component_transform_via_properties(component, actor)
        if fallback_value is not None:
            return fallback_value, None
        return None, f"get_component_transform() unavailable ({exc}) and property fallback failed: {fallback_error}"
    except Exception as exc:
        fallback_value, fallback_error = component_transform_via_properties(component, actor)
        if fallback_value is not None:
            return fallback_value, None
        return None, f"get_component_transform() raised: {exc} and property fallback failed: {fallback_error}"

    try:
        result = transform_to_dict(raw_transform)
    except Exception as exc:
        return None, f"transform_to_dict() raised: {exc}"
    if result is None:
        return None, "transform_to_dict() returned None"
    return result, None


def is_blueprint_generated_actor(actor):
    cpath = class_path(actor)
    if not cpath or not cpath.endswith("_C"):
        return False
    return cpath.startswith("/Game/") or cpath.startswith("/Plugins/") or cpath.startswith("/Plugin/")


def dict_to_unreal_transform(data):
    if not isinstance(data, dict):
        return None
    loc, rot, scl = data.get("location"), data.get("rotation"), data.get("scale")
    if not loc or not rot or not scl:
        return None
    try:
        return unreal.Transform(
            location=unreal.Vector(float(loc[0]), float(loc[1]), float(loc[2])),
            rotation=unreal.Rotator(
                roll=float(rot.get("roll", 0.0)),
                pitch=float(rot.get("pitch", 0.0)),
                yaw=float(rot.get("yaw", 0.0))
            ),
            scale=unreal.Vector(float(scl[0]), float(scl[1]), float(scl[2]))
        )
    except Exception:
        return None


def compose_unreal_transforms(a, b):
    if a is None:
        return b
    if b is None:
        return a
    try:
        return unreal.KismetMathLibrary.compose_transforms(a, b)
    except Exception:
        try:
            return a * b
        except Exception:
            return None


def compose_transform_dicts(a_dict, b_dict):
    a = dict_to_unreal_transform(a_dict)
    b = dict_to_unreal_transform(b_dict)
    res = compose_unreal_transforms(a, b)
    return transform_to_dict(res) if res is not None else None


def compose_chain_transform(chain, source_transform, li_registry, diagnostics=None, context=None):
    current = None
    for li_path in chain or []:
        li_info = li_registry.get(li_path)
        if li_info is None:
            if diagnostics is not None:
                diagnostics["broken_li_chain_links"] = diagnostics.get("broken_li_chain_links", 0) + 1
            continue
        li_tf = li_info.get("transform")
        if li_tf is None:
            continue
        current = li_tf if current is None else compose_transform_dicts(li_tf, current)

    if current is None:
        return source_transform
    return compose_transform_dicts(source_transform, current)


def level_instance_info(actor, parent_chain, parent_actor_path, depth):
    world_asset = safe_call(lambda: actor.get_world_asset(), None)
    loaded_level = safe_call(lambda: actor.get_loaded_level(), None)
    chain = list(parent_chain or [])
    a_path = object_path(actor)

    return {
        "actor_path": a_path,
        "actor_name": object_name(actor),
        "class": class_name(actor),
        "world_asset": object_path(world_asset) if world_asset else "",
        "loaded": bool(safe_call(lambda: actor.is_loaded(), False)),
        "loaded_level": object_path(loaded_level) if loaded_level else "",
        "transform": actor_transform(actor),
        "parent_level_instance": parent_actor_path,
        "parent_level_instance_chain": chain,
        "instance_chain": chain + [a_path],
        "depth": depth,
    }


def _collect_accessible_actors(direct_actors, cfg_rules):
    registry: Dict[str, Any] = {}
    all_actors: Dict[str, Any] = {}
    counters = {
        "li_occurrences": 0, "li_duplicate_encounters": 0, "li_unique": 0,
        "nested_actors": 0, "max_depth_reached": 0,
    }
    visited_levels = set()

    def add_actor(actor):
        if actor is None:
            return
        try:
            path = object_path(actor)
            if path and path not in all_actors:
                all_actors[path] = actor
        except Exception:
            pass

    for actor in direct_actors:
        add_actor(actor)

    def register(actor, parent_chain, parent_actor_path, depth, source):
        counters["li_occurrences"] += 1
        counters["max_depth_reached"] = max(counters["max_depth_reached"], depth)
        a_path = object_path(actor)
        if not a_path:
            return None
        if a_path in registry:
            existing = registry[a_path]
            existing["encounter_count"] += 1
            counters["li_duplicate_encounters"] += 1
            return existing
        info = level_instance_info(actor, parent_chain, parent_actor_path, depth)
        info["encounter_count"] = 1
        info["encounter_sources"] = [source]
        info["children_actor_paths"] = []
        info["status"] = "LOADED" if info.get("loaded") else "NOT_LOADED"
        registry[a_path] = info
        counters["li_unique"] += 1
        return info

    root_level_instances = [a for a in direct_actors
                            if classify_actor(a, cfg_rules) == "level_instance"]
    for root_li in root_level_instances:
        try:
            register(root_li, [], None, 0, "direct_root")
        except Exception:
            pass

    def collect(level, parent_chain, parent_actor_path, depth):
        if level is None or depth > MAX_LEVEL_INSTANCE_DEPTH:
            return
        try:
            level_path = object_path(level)
            if level_path in visited_levels:
                return
            visited_levels.add(level_path)

            for actor in enumerate_level_actors(level):
                before = len(all_actors)
                add_actor(actor)
                if len(all_actors) > before:
                    counters["nested_actors"] += 1

                if classify_actor(actor, cfg_rules) != "level_instance":
                    continue
                a_path = object_path(actor)
                li_info = register(actor, parent_chain, parent_actor_path, depth,
                                   "recursive_level_scan")
                if li_info is None:
                    continue
                if parent_actor_path and parent_actor_path in registry:
                    children = registry[parent_actor_path]["children_actor_paths"]
                    if a_path not in children:
                        children.append(a_path)

                child_level = safe_call(lambda a=actor: a.get_loaded_level(), None)
                if child_level:
                    collect(child_level, (parent_chain or []) + [a_path], a_path, depth + 1)
        except Exception:
            pass

    for root_li in root_level_instances:
        try:
            a_path = object_path(root_li)
            loaded_level = safe_call(lambda li=root_li: li.get_loaded_level(), None)
            if loaded_level:
                collect(loaded_level, [a_path], a_path, 1)
        except Exception:
            pass

    level_to_chain: Dict[str, List[str]] = {}
    for info in registry.values():
        loaded_level_path = info.get("loaded_level")
        if loaded_level_path:
            level_to_chain[loaded_level_path] = info.get("instance_chain", [])

    actor_li_chain_map: Dict[str, List[str]] = {}
    for path, actor in all_actors.items():
        try:
            actor_level = safe_call(lambda a=actor: a.get_level(), None)
            level_path = object_path(actor_level) if actor_level else ""
            actor_li_chain_map[path] = level_to_chain.get(level_path, [])
        except Exception:
            actor_li_chain_map[path] = []

    return registry, all_actors, actor_li_chain_map, counters


def _extract_instance_transforms(component, instance_count: int) -> Tuple[List[Optional[Dict]], List[int]]:
    transforms: List[Optional[Dict]] = []
    errors: List[int] = []
    for i in range(instance_count):
        raw = None
        for attempt in (
            lambda: component.get_instance_transform(i, True),
            lambda: component.get_instance_transform(i, world_space=True),
            lambda: component.get_instance_transform(instance_index=i, world_space=True),
        ):
            try:
                candidate = attempt()
            except Exception:
                candidate = None
            if candidate is not None:
                raw = candidate
                break
        if raw is None:
            errors.append(i)
            transforms.append(None)
            continue
        try:
            transforms.append(transform_to_dict(raw))
        except Exception:
            errors.append(i)
            transforms.append(None)
    return transforms, errors


def run(cfg: ResolvedConfig) -> StepReport:
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    start_time = time.time()

    export_root = cfg.get("paths.ue_export_root", "C:/Export")
    manifest_path = os.path.join(export_root, "level_manifest_v10.json")
    os.makedirs(export_root, exist_ok=True)

    errors: List[str] = []
    warnings: List[str] = []
    diagnostics: Dict[str, int] = {
        "transform_failures": 0, "missing_mesh_references": 0,
        "empty_mesh_slot_count": 0, "zero_instance_ism_skipped": 0,
        "instance_transform_errors": 0, "broken_li_chain_links": 0,
        "fx_transform_failures": 0, "actor_processing_exceptions": 0,
        "component_processing_exceptions": 0,
    }
    counters: Dict[str, Any] = {
        "direct_actors": 0, "accessible_actors": 0, "placements": 0,
        "unique_meshes": 0, "unique_materials": 0, "level_instances": 0,
    }

    if unreal is None:
        return StepReport(
            step="step1_manifest", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="FAILED", started_at=started_at, duration_s=time.time() - start_time,
            errors=["Unreal Engine Python API is unavailable."], counters=counters,
        )

    actor_rules = cfg.get("classification.actor_rules", [])
    component_rules = cfg.get("classification.component_rules", [])

    target_map = cfg.get("scan.target_map_package", "") or \
        package_path_from_filesystem(cfg.get("landscape.map_filesystem_path", ""))
    min_actors = cfg.get("scan.min_expected_actors", 20)

    try:
        guard = guard_before_scan(
            target_map=target_map,
            min_actors=min_actors,
            force_reload=cfg.get("scan.force_map_reload", False),
            check_world_partition=cfg.get("scan.load_world_partition_cells", True),
            enforce_map=cfg.get("scan.enforce_target_map", bool(target_map)),
        )
    except ScanGuardError as exc:
        return StepReport(
            step="step1_manifest", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="FAILED", started_at=started_at,
            duration_s=time.time() - start_time,
            errors=[str(exc)], counters=counters,
            warnings=["Aucun manifeste écrit — le manifeste existant est intact."],
        )

    for note in guard.notes:
        warnings.append(f"[scan_guard] {note}")
    counters["guard_actor_count"] = guard.actor_count
    if guard.map_was_loaded:
        warnings.append(
            f"[scan_guard] La map cible a dû être chargée ({guard.current_map}) : "
            "elle ne l'était pas au lancement du scan."
        )

    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    direct_actors = actor_subsystem.get_all_level_actors()
    counters["direct_actors"] = len(direct_actors)

    li_registry, all_actors_map, actor_li_chain_map, li_counters = \
        _collect_accessible_actors(direct_actors, actor_rules)
    counters["level_instances"] = li_counters["li_unique"]
    counters["nested_actors"] = li_counters["nested_actors"]
    counters["li_max_depth"] = li_counters["max_depth_reached"]

    accessible_actors = list(all_actors_map.values())
    counters["accessible_actors"] = len(accessible_actors)

    if len(accessible_actors) <= len(direct_actors) and li_counters["li_unique"] > 0:
        warnings.append(
            f"{li_counters['li_unique']} LevelInstance(s) trouvée(s) mais aucun acteur "
            "supplémentaire collecté à l'intérieur : leurs niveaux ne sont probablement "
            "pas chargés. Ouvrez la map et assurez-vous que les LevelInstances sont "
            "chargées (ou cassées) avant de relancer le scan."
        )

    manifest_data = {
        "manifest_version": "10.0",
        "pipeline": {"resolved_config": cfg.to_dict()},
        "_resolved": {"run_id": cfg.run_id, "config_hash": cfg.config_hash},
        "exporter": {"name": "UE5 -> Godot Conversion Manifest", "version": "10.0", "engine": "Unreal Engine 5.5"},
        "world": {"path": "World", "name": "MainWorld"},
        "actors": {"records": []},
        "blueprints": {"actors": []},
        "geometry": {"unique_meshes": {}, "placements": [],
                     "unique_skeletal_meshes": {}, "skeletal_mesh_placements": []},
        "materials": {"unique_materials": {}},
        "textures": {"unique_textures": {}},
        "level_instances": {
            "placements": list(li_registry.values()),
            "hierarchy": [li for li in li_registry.values() if li.get("depth") == 0],
            "registry": li_registry,
        },
        "effects": {"niagara": [], "decals": [], "particles": []},
        "world_features": {"landscape": [], "lights": [], "foliage": [], "audio": [], "environment": []},
        "conversion_diagnostic": diagnostics,
        "asset_inventory": {},
    }

    unique_meshes: Dict[str, Any] = {}
    placements: List[Dict[str, Any]] = []
    actor_records: List[Dict[str, Any]] = []
    blueprint_actors: Dict[str, Dict[str, Any]] = {}

    material_registry: AssetRegistry = AssetRegistry()
    texture_registry: AssetRegistry = AssetRegistry()
    mesh_registry: AssetRegistry = AssetRegistry()

    deep_materials = cfg.get("scan.include_material_parameters", True)
    deep_meshes = cfg.get("scan.include_mesh_details", True)

    def register_material_tree(material, ref: str) -> None:
        if material is None:
            return
        m_path = object_path(material)
        if not m_path:
            return
        if not material_registry.contains(m_path):
            material_registry.register(
                m_path, material_full_info(material, include_parameters=deep_materials) or {}, ref)
            for tex_path in referenced_textures(material):
                if tex_path and not texture_registry.contains(tex_path):
                    try:
                        texture = unreal.load_asset(tex_path)
                    except Exception:
                        texture = None
                    texture_registry.register(tex_path, texture_info(texture) or
                                              {"path": tex_path}, m_path)
                elif tex_path:
                    texture_registry.register(tex_path, {}, m_path)
        else:
            material_registry.register(m_path, {}, ref)

    def register_component_materials(component, ref: str) -> None:
        try:
            materials = component.get_materials()
        except Exception:
            materials = []
        for material in materials or []:
            register_material_tree(material, ref)

    for actor in accessible_actors:
        if actor is None:
            continue
        try:
            a_path = object_path(actor)
            a_name = object_name(actor)
            a_class = class_name(actor)
            is_blueprint = is_blueprint_generated_actor(actor)
            li_chain: List[str] = actor_li_chain_map.get(a_path, [])

            actor_records.append({
                "path": a_path, "name": a_name, "class": a_class,
                "category": classify_actor(actor, actor_rules),
                "is_blueprint": is_blueprint,
                "transform": actor_transform(actor),
            })

            if classify_actor(actor, actor_rules) == "landscape":
                try:
                    manifest_data["world_features"]["landscape"].append(
                        comp_module.landscape_info(actor))
                except Exception as exc:
                    warnings.append(f"Failed to read landscape info for {a_path}: {exc}")

            if is_blueprint:
                blueprint_actors[a_path] = {
                    "actor_path": a_path, "actor_name": a_name, "class_path": class_path(actor),
                    "component_details": [],
                }

            try:
                comps = actor.get_components_by_class(unreal.ActorComponent)
            except Exception:
                comps = []

            for comp in comps:
                if comp is None:
                    continue
                try:
                    kind = classify_component(comp, component_rules)
                    tf_value, tf_err = component_transform_diagnostic(comp, actor)
                    if tf_err is not None:
                        diagnostics["transform_failures"] += 1

                    world_tf = compose_chain_transform(li_chain, tf_value, li_registry, diagnostics, context=a_path) \
                        if tf_value is not None else None

                    if is_blueprint:
                        mesh_ref = None
                        if kind == "static_mesh":
                            m = safe_property(comp, "static_mesh", None)
                            mesh_ref = object_path(m) if m else None
                        elif kind == "skeletal_mesh":
                            m = safe_property(comp, "skeletal_mesh", None) or safe_property(comp, "skeletal_mesh_asset", None)
                            mesh_ref = object_path(m) if m else None
                        blueprint_actors[a_path]["component_details"].append({
                            "component_path": object_path(comp), "component_name": comp.get_name(),
                            "component_class": class_name(comp), "kind": kind,
                            "transform": world_tf, "mesh_reference": mesh_ref,
                        })

                    if kind == "static_mesh":
                        cls = class_name(comp)
                        is_instanced = "InstancedStaticMesh" in cls
                        mesh = safe_property(comp, "static_mesh", None)
                        if mesh is None:
                            diagnostics["empty_mesh_slot_count"] += 1
                            continue

                        m_path = object_path(mesh)
                        m_name = object_name(mesh)
                        if m_path not in unique_meshes:
                            unique_meshes[m_path] = mesh_info(mesh, deep=deep_meshes) or {
                                "path": m_path, "name": m_name, "class": class_name(mesh)}
                        mesh_registry.register(m_path, {"path": m_path}, a_path)
                        register_component_materials(comp, object_path(comp))

                        base_pid = stable_id("MESH", f"{a_path}_{comp.get_name()}")

                        if is_instanced:
                            try:
                                instance_count = int(comp.get_instance_count())
                            except Exception:
                                instance_count = 0

                            if instance_count <= 0:
                                diagnostics["zero_instance_ism_skipped"] += 1
                                continue

                            instance_transforms, inst_errors = _extract_instance_transforms(comp, instance_count)
                            diagnostics["instance_transform_errors"] += len(inst_errors)

                            instance_final_world_transforms = [
                                compose_chain_transform(li_chain, t, li_registry, diagnostics, context=a_path)
                                if t is not None else None
                                for t in instance_transforms
                            ]

                            hisc = "HierarchicalInstancedStaticMesh" in cls
                            placements.append({
                                "kind": "hierarchical_instanced_mesh" if hisc else "instanced_mesh",
                                "placement_id": base_pid,
                                "actor": {"path": a_path, "name": a_name, "class": a_class},
                                "component": {"path": object_path(comp), "name": comp.get_name(), "class": cls},
                                "mesh": {"path": m_path, "name": m_name},
                                "level_instance_chain": li_chain,
                                "source_transform": tf_value,
                                "final_world_transform": world_tf,
                                "reconstruction_transform": world_tf,
                                "transform_space": "composed_world",
                                "instance_count": instance_count,
                                "instance_transforms": instance_transforms,
                                "instance_transform_errors": inst_errors,
                                "instance_final_world_transforms": instance_final_world_transforms,
                            })
                        else:
                            placements.append({
                                "kind": "static_mesh",
                                "placement_id": base_pid,
                                "actor": {"path": a_path, "name": a_name, "class": a_class},
                                "component": {"path": object_path(comp), "name": comp.get_name(), "class": cls},
                                "mesh": {"path": m_path, "name": m_name},
                                "level_instance_chain": li_chain,
                                "source_transform": tf_value,
                                "final_world_transform": world_tf,
                                "reconstruction_transform": world_tf,
                                "transform_space": "composed_world",
                            })

                    elif kind == "skeletal_mesh":
                        mesh = safe_property(comp, "skeletal_mesh", None) or safe_property(comp, "skeletal_mesh_asset", None)
                        if mesh is None:
                            diagnostics["empty_mesh_slot_count"] += 1
                            continue

                        m_path = object_path(mesh)
                        m_name = object_name(mesh)
                        if m_path not in manifest_data["geometry"]["unique_skeletal_meshes"]:
                            manifest_data["geometry"]["unique_skeletal_meshes"][m_path] = {
                                "path": m_path, "name": m_name, "class": class_name(mesh)
                            }

                        pid = stable_id("SKEL", f"{a_path}_{comp.get_name()}")
                        manifest_data["geometry"]["skeletal_mesh_placements"].append({
                            "kind": "skeletal_mesh",
                            "placement_id": pid,
                            "actor": {"path": a_path, "name": a_name, "class": a_class},
                            "component": {"path": object_path(comp), "name": comp.get_name(), "class": class_name(comp)},
                            "mesh": {"path": m_path, "name": m_name},
                            "level_instance_chain": li_chain,
                            "source_transform": tf_value,
                            "final_world_transform": world_tf,
                            "reconstruction_transform": world_tf,
                            "transform_space": "composed_world",
                        })

                    elif kind == "decal":
                        if tf_err is not None:
                            diagnostics["fx_transform_failures"] += 1
                        record = comp_module.decal_component_info(
                            actor, comp, component_transform_diagnostic,
                            level_instance_chain=li_chain,
                            include_material_parameters=deep_materials,
                        )
                        record["transform"] = world_tf
                        register_material_tree(
                            safe_property(comp, "decal_material", None), object_path(comp))
                        manifest_data["effects"]["decals"].append(record)

                    elif kind in ("niagara", "particle"):
                        if tf_err is not None:
                            diagnostics["fx_transform_failures"] += 1
                        record = comp_module.niagara_component_info(
                            actor, comp, component_transform_diagnostic,
                            level_instance_chain=li_chain)
                        record["transform"] = world_tf
                        manifest_data["effects"]["niagara"].append(record)

                    elif kind == "light":
                        if tf_err is not None:
                            diagnostics["fx_transform_failures"] += 1
                        record = comp_module.light_component_info(
                            actor, comp, component_transform_diagnostic,
                            level_instance_chain=li_chain)
                        record["transform"] = world_tf
                        manifest_data["world_features"]["lights"].append(record)

                    elif kind == "audio":
                        if tf_err is not None:
                            diagnostics["fx_transform_failures"] += 1
                        record = comp_module.audio_component_info(
                            actor, comp, component_transform_diagnostic,
                            level_instance_chain=li_chain)
                        record["transform"] = world_tf
                        record["component_path"] = object_path(comp)
                        manifest_data["world_features"]["audio"].append(record)
                except Exception as exc:
                    diagnostics["component_processing_exceptions"] += 1
                    warnings.append(f"Error processing component {object_path(comp)}: {exc}")
        except Exception as exc:
            diagnostics["actor_processing_exceptions"] += 1
            warnings.append(f"Error processing actor {object_path(actor)}: {exc}")

    counters["placements"] = len(placements)
    counters["unique_meshes"] = len(unique_meshes)
    counters["skeletal_mesh_placements"] = len(manifest_data["geometry"]["skeletal_mesh_placements"])
    counters["unique_skeletal_meshes"] = len(manifest_data["geometry"]["unique_skeletal_meshes"])

    manifest_data["actors"]["records"] = actor_records
    manifest_data["blueprints"]["actors"] = list(blueprint_actors.values())
    manifest_data["geometry"]["unique_meshes"] = unique_meshes
    manifest_data["geometry"]["placements"] = placements

    manifest_data["materials"]["unique_materials"] = material_registry.all_entries()
    manifest_data["textures"]["unique_textures"] = texture_registry.all_entries()

    counters["unique_materials"] = material_registry.count()
    counters["unique_textures"] = texture_registry.count()

    manifest_data["asset_inventory"] = {
        "static_mesh_assets": len(unique_meshes),
        "material_assets": material_registry.count(),
        "texture_assets": texture_registry.count(),
        "skeletal_mesh_assets": len(manifest_data["geometry"]["unique_skeletal_meshes"]),
        "level_instance_assets": len(li_registry),
        "actor_count": len(actor_records),
    }

    ready_geometry = (
        diagnostics["transform_failures"] == 0
        and diagnostics["missing_mesh_references"] == 0
        and diagnostics["broken_li_chain_links"] == 0
        and diagnostics["instance_transform_errors"] == 0
        and diagnostics["actor_processing_exceptions"] == 0
    )
    ready_fx = diagnostics["fx_transform_failures"] == 0

    manifest_data["reconstruction"] = {
        "ready_for_godot_geometry": ready_geometry,
        "ready_for_godot_fx": ready_fx,
        "asset_payload_included": False,
    }

    if not ready_geometry:
        warnings.append(
            f"reconstruction.ready_for_godot_geometry = false "
            f"(transform_failures={diagnostics['transform_failures']}, "
            f"broken_li_chain_links={diagnostics['broken_li_chain_links']}, "
            f"instance_transform_errors={diagnostics['instance_transform_errors']})"
        )
    if not ready_fx:
        warnings.append(
            f"reconstruction.ready_for_godot_fx = false "
            f"(fx_transform_failures={diagnostics['fx_transform_failures']})"
        )

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)

    slim_path = manifest_path.replace(".json", ".slim.json")
    slim = {
        "manifest_version": manifest_data["manifest_version"],
        "pipeline": manifest_data.get("pipeline", {}),
        "_resolved": manifest_data.get("_resolved", {}),
        "_slim": True,
        "_full_manifest": os.path.basename(manifest_path),
        "geometry": {
            "placements": manifest_data["geometry"]["placements"],
            "skeletal_mesh_placements": manifest_data["geometry"]["skeletal_mesh_placements"],
            "unique_meshes": {
                path: {"path": info.get("path", path), "name": info.get("name", "")}
                for path, info in manifest_data["geometry"]["unique_meshes"].items()
                if isinstance(info, dict)
            },
            "unique_skeletal_meshes": manifest_data["geometry"]["unique_skeletal_meshes"],
        },
        "effects": manifest_data["effects"],
        "world_features": {
            "lights": manifest_data["world_features"]["lights"],
            "audio": manifest_data["world_features"]["audio"],
            "landscape": manifest_data["world_features"]["landscape"],
        },
        "reconstruction": manifest_data.get("reconstruction", {}),
        "actors": {"records": []},
    }
    try:
        with open(slim_path, "w", encoding="utf-8") as f:
            json.dump(slim, f, separators=(",", ":"), ensure_ascii=False)
        slim_size = os.path.getsize(slim_path)
        full_size = os.path.getsize(manifest_path)
        counters["manifest_bytes"] = full_size
        counters["manifest_slim_bytes"] = slim_size
        if full_size > 64 * 1024 * 1024:
            warnings.append(
                f"Manifeste complet volumineux ({full_size // (1024*1024)} Mo). "
                f"Un manifeste allégé de {slim_size // 1024} Ko a été écrit en "
                "repli pour la reconstruction Godot."
            )
    except OSError as exc:
        warnings.append(f"Manifeste allégé non écrit : {exc}")

    outputs = [{"path": manifest_path, "bytes": os.path.getsize(manifest_path)}]
    status = "OK_WITH_WARNINGS" if (not ready_geometry or not ready_fx) else "OK"

    return StepReport(
        step="step1_manifest",
        run_id=cfg.run_id,
        config_hash=cfg.config_hash,
        status=status,
        started_at=started_at,
        duration_s=time.time() - start_time,
        outputs=outputs,
        counters=counters,
        errors=errors,
        warnings=warnings,
    )
