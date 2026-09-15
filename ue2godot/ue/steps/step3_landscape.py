# -*- coding: utf-8 -*-
"""
Step 3: Landscape — material configuration + geometry rebuild + texture bake.
"""

import os
import json
import time
from typing import Dict, Any, List, Tuple

from ue2godot.core.config import ResolvedConfig
from ue2godot.core.report import StepReport
from ue2godot.core.axis import get_axis_config, convert_position_ue_to_godot
from ue2godot.ue.raycast import raycast_grid_heights
from ue2godot.ue.rendertarget import bake_top_down_base_color
from ue2godot.core.glb import build_grid_mesh, write_glb
from ue2godot.ue.landscape_material_config import auto_configure_landscape_material

try:
    import unreal
except ImportError:
    unreal = None


Bounds = Tuple[float, float, float, float, float, float]


def _discover_landscape_actors() -> List[Any]:
    if unreal is None:
        return []
    try:
        actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        actors = actor_subsystem.get_all_level_actors()
        return [a for a in actors if isinstance(a, (unreal.Landscape, unreal.LandscapeProxy))]
    except Exception:
        return []


def _bounds_gap(a: Bounds, b: Bounds) -> float:
    a_min_x, a_max_x, a_min_y, a_max_y = a[0], a[1], a[2], a[3]
    b_min_x, b_max_x, b_min_y, b_max_y = b[0], b[1], b[2], b[3]

    dx = max(b_min_x - a_max_x, a_min_x - b_max_x, 0.0)
    dy = max(b_min_y - a_max_y, a_min_y - b_max_y, 0.0)
    return (dx * dx + dy * dy) ** 0.5


def _cluster_landscapes(bounds_list: List[Bounds], gap_threshold_cm: float) -> List[List[int]]:
    n = len(bounds_list)
    if n == 0:
        return []
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        for j in range(i + 1, n):
            if _bounds_gap(bounds_list[i], bounds_list[j]) <= gap_threshold_cm:
                union(i, j)

    clusters: Dict[int, List[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)
    return list(clusters.values())


def configure_landscape_material(cfg: ResolvedConfig) -> StepReport:
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    start_time = time.time()
    errors: List[str] = []
    warnings: List[str] = []
    counters = {"landscapes_found": 0, "materials_assigned": 0}

    if unreal is None:
        return StepReport(
            step="step3_landscape.configure_material", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="FAILED", started_at=started_at, duration_s=time.time() - start_time,
            errors=["Unreal Engine Python API is unavailable."], counters=counters,
        )

    landscapes = _discover_landscape_actors()
    counters["landscapes_found"] = len(landscapes)
    if not landscapes:
        return StepReport(
            step="step3_landscape.configure_material", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="OK", started_at=started_at, duration_s=time.time() - start_time,
            warnings=["No Landscape actor found in level."], counters=counters,
        )

    mat_folder = cfg.get("landscape.material_folder", "/Game/LandscapeMaterials")
    map_fs_path = cfg.get("landscape.map_filesystem_path", "")
    require_material = cfg.get("landscape.require_assigned_material", True)

    for landscape in landscapes:
        try:
            result = auto_configure_landscape_material(landscape, mat_folder, map_fs_path)
            if result.get("assigned_material"):
                counters["materials_assigned"] += 1
                if result.get("auto_configured"):
                    warnings.append(f"Landscape material auto-assigned: {result['assigned_material']}")
                    if result.get("discovery_method") not in (None, "configured_candidate"):
                        warnings.append(
                            f"Material folder found by {result['discovery_method']} — "
                            f"packs considered: {[p['folder'] for p in result.get('packs_considered', [])]}"
                        )
            elif require_material:
                warnings.append("Landscape has no custom material assigned (default checker material).")
                if result.get("packs_considered"):
                    warnings.append(
                        f"Folder found but no pack yielded a usable material — "
                        f"packs tried: {[p['folder'] for p in result['packs_considered']]}"
                    )
            warnings.extend(result.get("notes", []))
        except Exception as exc:
            warnings.append(f"Error configuring material for landscape actor: {exc}")

    status = "OK" if not errors else "FAILED"
    if warnings and status == "OK":
        status = "OK_WITH_WARNINGS"

    return StepReport(
        step="step3_landscape.configure_material", run_id=cfg.run_id, config_hash=cfg.config_hash,
        status=status, started_at=started_at, duration_s=time.time() - start_time,
        counters=counters, errors=errors, warnings=warnings,
    )


def _actor_bounds(landscape: Any) -> Bounds:
    try:
        origin, extent = landscape.get_actor_bounds(False)
        return (
            float(origin.x - extent.x), float(origin.x + extent.x),
            float(origin.y - extent.y), float(origin.y + extent.y),
            float(origin.z - extent.z), float(origin.z + extent.z),
        )
    except Exception:
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _bake_one_cluster(
    cluster_index: int,
    landscapes: List[Any],
    cfg: ResolvedConfig,
    axis_cfg: Dict[str, Any],
    world: Any,
    mesh_output_dir: str,
    godot_mesh_root: str,
    warnings: List[str],
) -> Dict[str, Any]:
    def axis_map(x: float, y: float, z: float):
        return convert_position_ue_to_godot(x, y, z, axis_cfg["unit_scale"])

    min_x, max_x = float("inf"), float("-inf")
    min_y, max_y = float("inf"), float("-inf")
    min_z, max_z = float("inf"), float("-inf")

    for landscape in landscapes:
        try:
            b = _actor_bounds(landscape)
            min_x = min(min_x, b[0]); max_x = max(max_x, b[1])
            min_y = min(min_y, b[2]); max_y = max(max_y, b[3])
            min_z = min(min_z, b[4]); max_z = max(max_z, b[5])
        except Exception:
            pass

    if min_x == float("inf") or min_x == max_x or min_y == max_y:
        return {"ok": False, "reason": f"Cluster {cluster_index}: Could not determine valid bounds for landscape cluster."}

    resolution = cfg.get("landscape.grid_resolution", 256)
    trace_margin = cfg.get("landscape.trace_margin_cm", 50000.0)
    max_retries = cfg.get("landscape.max_trace_retries", 12)

    try:
        grid_heights = raycast_grid_heights(
            world, (min_x, max_x, min_y, max_y, min_z - trace_margin, max_z + trace_margin),
            resolution, max_retries,
        )

        side = resolution + 1
        positions, normals, uvs, indices = build_grid_mesh(
            grid_heights, side, min_x, max_x, min_y, max_y, axis_map,
        )
        vertex_count = len(positions) // 3
        triangle_count = len(indices) // 3

        if vertex_count == 0:
            return {"ok": False, "reason": f"Cluster {cluster_index}: raycast grid produced zero hits."}

        suffix = "" if cluster_index == 0 else f"_{cluster_index}"
        ue_path = f"/AutoTerrain/Landscape{suffix}.BakedLandscape"
        glb_name = f"BakedLandscape{suffix}.glb"
        png_name = f"BakedLandscape{suffix}_BaseColor.png"
        asset_name = f"BakedLandscape{suffix}"

        texture_resolution = cfg.get("landscape.texture_resolution", 4096)
        png_disk_path = os.path.join(mesh_output_dir, png_name).replace("\\", "/")
        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0
        half_size = max(max_x - min_x, max_y - min_y) / 2.0

        png_bytes = None
        texture_baked = bake_top_down_base_color(
            world, landscapes, center_x, center_y, half_size, max_z + trace_margin,
            png_disk_path, texture_resolution,
        )
        if texture_baked:
            try:
                with open(png_disk_path, "rb") as f:
                    png_bytes = f.read()
            except Exception as exc:
                warnings.append(f"Cluster {cluster_index}: baked texture could not be re-read: {exc}")
        else:
            warnings.append(
                f"Cluster {cluster_index}: BaseColor bake failed — mesh written without a texture."
            )

        os.makedirs(mesh_output_dir, exist_ok=True)
        glb_disk_path = os.path.join(mesh_output_dir, glb_name).replace("\\", "/")
        godot_path = f"{godot_mesh_root}/{glb_name}"
        write_glb(glb_disk_path, positions, normals, uvs, indices, png_bytes, name=asset_name)

        return {
            "ok": True,
            "ue_path": ue_path, "asset_name": asset_name,
            "glb_disk_path": glb_disk_path, "godot_path": godot_path,
            "png_disk_path": png_disk_path if png_bytes is not None else None,
            "png_bytes_len": len(png_bytes) if png_bytes is not None else 0,
            "vertex_count": vertex_count, "triangle_count": triangle_count,
            "texture_baked": texture_baked,
        }
    except Exception as exc:
        return {"ok": False, "reason": f"Cluster {cluster_index} failed with exception: {exc}"}


def run(cfg: ResolvedConfig) -> StepReport:
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    start_time = time.time()

    enabled = cfg.get("landscape.enabled", True)
    export_root = cfg.get("paths.ue_export_root", "C:/Export")
    manifest_path = os.path.join(export_root, "level_manifest_v10.json")
    asset_map_path = os.path.join(export_root, "GodotAssets", "ue5_godot_asset_map.json")
    mesh_output_dir = os.path.join(export_root, "GodotAssets", "Meshes")

    godot_asset_root = cfg.get("paths.godot_asset_root", "res://UEAssets")
    godot_mesh_root = f"{godot_asset_root}/Meshes"

    errors: List[str] = []
    warnings: List[str] = []
    counters: Dict[str, Any] = {"vertices": 0, "triangles": 0, "texture_baked": False, "clusters": 0}

    if not enabled:
        return StepReport(
            step="step3_landscape", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="OK", started_at=started_at, duration_s=time.time() - start_time,
            warnings=["Landscape export disabled in config."], counters=counters,
        )

    if unreal is None:
        return StepReport(
            step="step3_landscape", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="FAILED", started_at=started_at, duration_s=time.time() - start_time,
            errors=["Unreal Engine Python API is unavailable."], counters=counters,
        )

    landscapes = _discover_landscape_actors()
    if not landscapes:
        return StepReport(
            step="step3_landscape", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="OK", started_at=started_at, duration_s=time.time() - start_time,
            warnings=["No Landscape actor found in level."], counters=counters,
        )

    material_report = configure_landscape_material(cfg)
    warnings.extend(material_report.warnings)
    errors.extend(material_report.errors)

    axis_cfg = get_axis_config(cfg.get("axis.preset", "unreal_gltf_exporter"))
    try:
        world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    except Exception as exc:
        return StepReport(
            step="step3_landscape", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="FAILED", started_at=started_at, duration_s=time.time() - start_time,
            errors=[f"Could not get Unreal editor world: {exc}"], counters=counters,
        )

    gap_threshold = cfg.get("landscape.cluster_gap_threshold_cm", 5000.0)
    try:
        bounds_list = [_actor_bounds(l) for l in landscapes]
    except Exception as exc:
        return StepReport(
            step="step3_landscape", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="FAILED", started_at=started_at, duration_s=time.time() - start_time,
            errors=[f"Could not read landscape bounds: {exc}"], counters=counters,
        )
    clusters_idx = _cluster_landscapes(bounds_list, gap_threshold)
    counters["clusters"] = len(clusters_idx)

    if len(clusters_idx) > 1:
        warnings.append(
            f"{len(landscapes)} Landscape actor(s) grouped into {len(clusters_idx)} disjoint "
            f"cluster(s) (gap threshold {gap_threshold} cm) — each gets its own GLB and baked "
            "texture, registered as separate entries."
        )

    if os.path.isfile(asset_map_path):
        try:
            with open(asset_map_path, "r", encoding="utf-8") as f:
                asset_map = json.load(f)
        except Exception:
            asset_map = {"assets": {}}
    else:
        asset_map = {"assets": {}}
    asset_map.setdefault("assets", {})

    manifest = None
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            geometry = manifest.setdefault("geometry", {})
            placements = geometry.setdefault("placements", [])
            unique_meshes = geometry.setdefault("unique_meshes", {})
            placements[:] = [p for p in placements if not str(p.get("placement_id", "")).startswith("MESH_BAKED_LANDSCAPE")]
        except Exception as exc:
            warnings.append(f"Manifest file could not be parsed ({manifest_path}): {exc}")
    else:
        warnings.append("Manifest not found — asset map will be patched, but manifest placements cannot be added.")

    outputs = []
    any_ok = False

    for cluster_index, member_indices in enumerate(clusters_idx):
        cluster_landscapes = [landscapes[i] for i in member_indices]
        result = _bake_one_cluster(
            cluster_index, cluster_landscapes, cfg, axis_cfg, world,
            mesh_output_dir, godot_mesh_root, warnings,
        )
        if not result.get("ok"):
            errors.append(result.get("reason", f"Cluster {cluster_index} failed for an unknown reason."))
            continue

        any_ok = True
        counters["vertices"] += result["vertex_count"]
        counters["triangles"] += result["triangle_count"]
        counters["texture_baked"] = counters["texture_baked"] or result["texture_baked"]

        asset_map["assets"][result["ue_path"]] = {
            "ue_path": result["ue_path"], "ue_name": result["asset_name"],
            "godot_path": result["godot_path"], "disk_path": result["glb_disk_path"],
            "format": "glb", "status": "EXPORTED",
            "source": "raycast_grid + top_down_base_color_bake",
            "axis_map": axis_cfg["label"],
            "vertex_count": result["vertex_count"], "triangle_count": result["triangle_count"],
            "texture_baked": result["texture_baked"],
            "cluster_index": cluster_index, "cluster_member_count": len(cluster_landscapes),
        }

        outputs.append({"path": result["glb_disk_path"], "bytes": os.path.getsize(result["glb_disk_path"])})
        if result.get("png_disk_path"):
            outputs.append({"path": result["png_disk_path"], "bytes": result["png_bytes_len"]})

        if manifest is not None:
            placement_id = f"MESH_BAKED_LANDSCAPE_{cluster_index}" if cluster_index else "MESH_BAKED_LANDSCAPE"
            unique_meshes[result["ue_path"]] = {
                "path": result["ue_path"], "name": result["asset_name"], "class": "StaticMesh"
            }
            identity_tf = {"location": [0, 0, 0], "rotation": {"pitch": 0, "yaw": 0, "roll": 0}, "scale": [1, 1, 1]}
            placements.append({
                "kind": "static_mesh", "placement_id": placement_id,
                "actor": {"path": result["ue_path"], "name": result["asset_name"], "class": "BakedLandscape"},
                "component": {"path": f"{result['ue_path']}.Mesh", "name": f"{result['asset_name']}Mesh", "class": "StaticMeshComponent"},
                "mesh": {"path": result["ue_path"], "name": result["asset_name"]},
                "source_transform": identity_tf, "final_world_transform": identity_tf,
                "reconstruction_transform": identity_tf,
            })

    try:
        with open(asset_map_path, "w", encoding="utf-8") as f:
            json.dump(asset_map, f, indent=2, ensure_ascii=False)
        if manifest is not None:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        errors.append(f"Failed to write updated JSON files: {exc}")

    if not any_ok:
        status = "FAILED"
    elif errors:
        status = "OK_WITH_WARNINGS"
    else:
        status = "OK" if not warnings else "OK_WITH_WARNINGS"

    return StepReport(
        step="step3_landscape", run_id=cfg.run_id, config_hash=cfg.config_hash,
        status=status, started_at=started_at, duration_s=time.time() - start_time,
        outputs=outputs, counters=counters, errors=errors, warnings=warnings,
    )
