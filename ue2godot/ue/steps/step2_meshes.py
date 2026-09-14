# -*- coding: utf-8 -*-
"""
Step 2: Mesh Exporter — StaticMesh AND SkeletalMesh.

SkeletalMesh assets were captured in the manifest (geometry.unique_
skeletal_meshes) since the reference's V10.1 but never exported anywhere
in the pipeline — asset_inventory.skeletal_mesh_assets stayed permanently
at 0 (§12.1/§12.6 of the analysis). Exported here the same way as
StaticMesh (same GLTFExporter call works on a SkeletalMesh asset), into
the SAME asset map `assets` dict keyed by ue_path, so geometry_builder.gd
can resolve a skeletal placement exactly like a static one without any
special-casing — see map_builder.gd, which now feeds it both placement
lists.

Explicit limitation, stated rather than silently implied: this exports
the BIND POSE geometry only. No animation, no skeleton, no retargeting —
a skeletal mesh becomes a static prop in Godot. Every exported skeletal
entry is tagged "asset_kind": "skeletal_mesh" in the asset map precisely
so a future consumer (or a human reading the JSON) is never misled into
thinking animation survived the trip.
"""

import os
import json
import time
from typing import Dict, Any, List, Tuple
from ue2godot.core.config import ResolvedConfig
from ue2godot.core.report import StepReport
from ue2godot.core.ids import unique_filename_for_path

try:
    import unreal
except ImportError:
    unreal = None


def export_single_glb(asset: Any, output_path: str) -> Tuple[bool, str]:
    exporter_cls = getattr(unreal, "GLTFExporter", None)
    if exporter_cls is None:
        return False, "GLTFExporter Python API is unavailable."

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        options = None
        options_cls = getattr(unreal, "GLTFExportOptions", None)
        if options_cls is not None:
            try:
                options = options_cls()
            except Exception:
                options = None

        result = exporter_cls.export_to_gltf(asset, output_path, options, set())
        if result is None:
            return False, "GLTFExporter returned None."

        if not os.path.isfile(output_path):
            return False, "Exporter returned without creating GLB file."

        if os.path.getsize(output_path) < 20:
            return False, f"GLB file suspiciously small ({os.path.getsize(output_path)} bytes)."

        with open(output_path, "rb") as f:
            if f.read(4) != b"glTF":
                return False, "Invalid glTF magic header."

        return True, ""
    except Exception as exc:
        return False, str(exc)


def _export_registry(
    registry: Dict[str, Any],
    mesh_output_dir: str,
    godot_mesh_root: str,
    asset_kind: str,
    mapping: Dict[str, Any],
    failures: List[Dict[str, Any]],
    counters: Dict[str, int],
) -> None:
    for mesh_path, mesh_info in registry.items():
        if not mesh_path:
            continue

        try:
            asset = unreal.load_asset(mesh_path)
        except Exception:
            try:
                asset = unreal.EditorAssetLibrary.load_asset(mesh_path)
            except Exception:
                asset = None

        if asset is None:
            failures.append({"ue_path": mesh_path, "asset_kind": asset_kind, "reason": "Asset could not be loaded."})
            continue

        asset_name = mesh_info.get("name", "Mesh")
        filename = unique_filename_for_path(mesh_path, "glb")
        disk_path = os.path.join(mesh_output_dir, filename).replace("\\", "/")
        godot_path = f"{godot_mesh_root}/{filename}"

        entry_extra = {"asset_kind": asset_kind}
        if asset_kind == "skeletal_mesh":
            entry_extra["note"] = "Bind pose only — no animation, no skeleton exported."

        if os.path.isfile(disk_path) and os.path.getsize(disk_path) > 0:
            counters["existing"] += 1
            mapping[mesh_path] = {
                "ue_path": mesh_path, "ue_name": asset_name, "godot_path": godot_path,
                "disk_path": disk_path, "format": "glb", "status": "EXISTING", **entry_extra,
            }
            continue

        ok, reason = export_single_glb(asset, disk_path)
        if ok:
            counters["exported"] += 1
            mapping[mesh_path] = {
                "ue_path": mesh_path, "ue_name": asset_name, "godot_path": godot_path,
                "disk_path": disk_path, "format": "glb", "status": "EXPORTED", **entry_extra,
            }
        else:
            failures.append({
                "ue_path": mesh_path, "ue_name": asset_name, "asset_kind": asset_kind,
                "disk_path": disk_path, "reason": reason,
            })


def run(cfg: ResolvedConfig) -> StepReport:
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    start_time = time.time()

    export_root = cfg.get("paths.ue_export_root", "C:/Export")
    manifest_path = os.path.join(export_root, "level_manifest_v10.json")
    asset_map_path = os.path.join(export_root, "GodotAssets", "ue5_godot_asset_map.json")
    mesh_output_dir = os.path.join(export_root, "GodotAssets", "Meshes")

    godot_asset_root = cfg.get("paths.godot_asset_root", "res://UEAssets")
    godot_mesh_root = f"{godot_asset_root}/Meshes"
    export_skeletal = cfg.get("meshes.export_skeletal", True)

    errors: List[str] = []
    warnings: List[str] = []
    counters = {"unique_meshes": 0, "unique_skeletal_meshes": 0, "exported": 0, "existing": 0, "failures": 0}

    if unreal is None:
        return StepReport(
            step="step2_meshes", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="FAILED", started_at=started_at, duration_s=time.time() - start_time,
            errors=["Unreal Engine Python API is unavailable."], counters=counters
        )

    if not os.path.isfile(manifest_path):
        return StepReport(
            step="step2_meshes", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="FAILED", started_at=started_at, duration_s=time.time() - start_time,
            errors=[f"Manifest file not found: {manifest_path}"], counters=counters
        )

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)

    geometry = manifest_data.get("geometry", {})
    unique_meshes = geometry.get("unique_meshes", {})
    unique_skeletal_meshes = geometry.get("unique_skeletal_meshes", {}) if export_skeletal else {}
    counters["unique_meshes"] = len(unique_meshes)
    counters["unique_skeletal_meshes"] = len(unique_skeletal_meshes)

    os.makedirs(mesh_output_dir, exist_ok=True)
    mapping: Dict[str, Any] = {}
    failures: List[Dict[str, Any]] = []

    _export_registry(unique_meshes, mesh_output_dir, godot_mesh_root, "static_mesh", mapping, failures, counters)
    if unique_skeletal_meshes:
        _export_registry(
            unique_skeletal_meshes, mesh_output_dir, godot_mesh_root, "skeletal_mesh",
            mapping, failures, counters,
        )
        warnings.append(
            f"{len(unique_skeletal_meshes)} SkeletalMesh asset(s) exported as static bind-pose "
            "geometry — no animation or skeleton is preserved."
        )
    elif geometry.get("unique_skeletal_meshes") and not export_skeletal:
        warnings.append(
            f"{len(geometry.get('unique_skeletal_meshes', {}))} SkeletalMesh asset(s) present in "
            "the manifest but meshes.export_skeletal is disabled — skipped."
        )

    counters["failures"] = len(failures)

    asset_map_payload = {
        "exporter_version": "1.0",
        "manifest_version": manifest_data.get("manifest_version", "10.0"),
        "format": "glb",
        "godot_asset_root": godot_asset_root,
        "godot_mesh_root": godot_mesh_root,
        "source_manifest": manifest_path,
        "_resolved": {"run_id": cfg.run_id, "config_hash": cfg.config_hash},
        "unique_mesh_count": len(unique_meshes) + len(unique_skeletal_meshes),
        "exported_count": counters["exported"],
        "existing_count": counters["existing"],
        "failure_count": len(failures),
        "assets": mapping,
        "failures": failures
    }

    os.makedirs(os.path.dirname(asset_map_path), exist_ok=True)
    with open(asset_map_path, "w", encoding="utf-8") as f:
        json.dump(asset_map_payload, f, indent=2, ensure_ascii=False)

    outputs = [{"path": asset_map_path, "bytes": os.path.getsize(asset_map_path)}]
    status = "OK" if len(failures) == 0 else "OK_WITH_WARNINGS"

    return StepReport(
        step="step2_meshes", run_id=cfg.run_id, config_hash=cfg.config_hash,
        status=status, started_at=started_at, duration_s=time.time() - start_time,
        outputs=outputs, counters=counters, errors=errors, warnings=warnings
    )
