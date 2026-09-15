# -*- coding: utf-8 -*-
"""
Step 2: Mesh Exporter — StaticMesh AND SkeletalMesh.
"""

import os
import json
import time
from typing import Dict, Any, List, Tuple
from ue2godot.core.config import ResolvedConfig
from ue2godot.core.report import StepReport
from ue2godot.core.ids import unique_filename_for_path
from ue2godot.core.strategies import summarize_outcomes
from ue2godot.ue.mesh_export import build_mesh_export_chain

try:
    import unreal
except ImportError:
    unreal = None


def _export_registry(
    registry: Dict[str, Any],
    mesh_output_dir: str,
    godot_mesh_root: str,
    asset_kind: str,
    mapping: Dict[str, Any],
    failures: List[Dict[str, Any]],
    counters: Dict[str, int],
    chain=None,
    outcomes: Dict[str, Any] = None,
) -> None:
    chain = chain or build_mesh_export_chain()
    outcomes = outcomes if outcomes is not None else {}
    for mesh_path, mesh_info in registry.items():
        if not mesh_path:
            continue

        try:
            asset_name = mesh_info.get("name", "Mesh") if isinstance(mesh_info, dict) else "Mesh"
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

            base = disk_path[:-4] if disk_path.lower().endswith(".glb") else disk_path
            outcome = chain.run(asset, base)
            outcomes[mesh_path] = outcome

            if outcome.succeeded:
                counters["exported"] += 1
                produced = outcome.value
                produced_name = os.path.basename(produced)
                entry = {
                    "ue_path": mesh_path, "ue_name": asset_name,
                    "godot_path": f"{godot_mesh_root}/{produced_name}",
                    "disk_path": produced.replace("\\", "/"),
                    "format": os.path.splitext(produced)[1].lstrip(".").lower(),
                    "status": "EXPORTED",
                    "export_strategy": outcome.strategy,
                    **entry_extra,
                }
                if outcome.used_fallback():
                    entry["export_quality"] = outcome.quality
                    entry["export_caveat"] = outcome.caveat
                    entry["export_attempts"] = outcome.attempts
                    counters["exported_via_fallback"] = counters.get("exported_via_fallback", 0) + 1
                mapping[mesh_path] = entry
            else:
                failures.append({
                    "ue_path": mesh_path, "ue_name": asset_name, "asset_kind": asset_kind,
                    "disk_path": disk_path, "reason": outcome.failure_summary(),
                    "attempts": outcome.attempts,
                })
        except Exception as exc:
            failures.append({
                "ue_path": mesh_path, "asset_kind": asset_kind,
                "reason": f"Unexpected exception during mesh export: {exc}",
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

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
    except Exception as exc:
        return StepReport(
            step="step2_meshes", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="FAILED", started_at=started_at, duration_s=time.time() - start_time,
            errors=[f"Failed to read manifest JSON ({manifest_path}): {exc}"], counters=counters
        )

    geometry = manifest_data.get("geometry", {})
    unique_meshes = geometry.get("unique_meshes", {})
    unique_skeletal_meshes = geometry.get("unique_skeletal_meshes", {}) if export_skeletal else {}
    counters["unique_meshes"] = len(unique_meshes)
    counters["unique_skeletal_meshes"] = len(unique_skeletal_meshes)

    os.makedirs(mesh_output_dir, exist_ok=True)
    mapping: Dict[str, Any] = {}
    failures: List[Dict[str, Any]] = []

    export_chain = build_mesh_export_chain(
        enable_manual=cfg.get("meshes.enable_manual_fallback", True),
        enable_task=cfg.get("meshes.enable_export_task_fallback", True),
    )
    export_outcomes: Dict[str, Any] = {}

    _export_registry(unique_meshes, mesh_output_dir, godot_mesh_root, "static_mesh",
                     mapping, failures, counters, export_chain, export_outcomes)
    if unique_skeletal_meshes:
        _export_registry(
            unique_skeletal_meshes, mesh_output_dir, godot_mesh_root, "skeletal_mesh",
            mapping, failures, counters, export_chain, export_outcomes,
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

    strategy_summary = summarize_outcomes(export_outcomes)
    counters["export_strategies"] = strategy_summary["by_strategy"]

    if strategy_summary["fallback_count"]:
        warnings.append(
            f"{strategy_summary['fallback_count']} mesh(es) exporté(s) par une voie "
            f"de repli — répartition : {strategy_summary['by_strategy']}. "
            "Vérifiez que le plugin GLTFExporter est bien activé."
        )
    for note in strategy_summary["degraded"][:10]:
        warnings.append(f"Export dégradé — {note}")

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
        "export_strategies": strategy_summary["by_strategy"],
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
