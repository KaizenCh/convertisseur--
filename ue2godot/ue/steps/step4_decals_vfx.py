# -*- coding: utf-8 -*-
"""
Step 4: Decals texture bake, VFX markers, and audio markers.
"""

import os
import json
import time
from typing import Dict, Any, List, Tuple, Optional

from ue2godot.core.config import ResolvedConfig
from ue2godot.core.report import StepReport
from ue2godot.core import png_codec
from ue2godot.ue.rendertarget import (
    export_texture_to_png, build_texture_export_chain, export_texture_with_fallbacks,
)
from ue2godot.core.strategies import summarize_outcomes

try:
    import unreal
except ImportError:
    unreal = None


def _safe_name(value: str) -> str:
    out = []
    for char in str(value or "decal"):
        out.append(char if (char.isalnum() or char in "_-") else "_")
    return "".join(out).strip("_") or "decal"


def _resolve_texture_parameter(material: Any, candidate_names: List[str]):
    if unreal is None or material is None:
        return None, None, None
    MEL = unreal.MaterialEditingLibrary

    if isinstance(material, unreal.MaterialInstance):
        try:
            entries = material.get_editor_property("texture_parameter_values")
        except Exception:
            entries = []
        for entry in entries:
            try:
                name = str(entry.parameter_info.name)
                value = entry.get_editor_property("parameter_value")
            except Exception:
                continue
            if value is None:
                continue
            for candidate in candidate_names:
                if name.lower().replace(" ", "") == candidate.lower().replace(" ", ""):
                    return value, name, "instance override"

    parent = material
    if isinstance(material, unreal.MaterialInstance):
        try:
            parent = material.get_editor_property("parent")
        except Exception:
            parent = None
    if parent is None:
        return None, None, None

    for candidate in candidate_names:
        try:
            value = MEL.get_material_default_texture_parameter_value(parent, candidate)
        except Exception:
            value = None
        if isinstance(value, unreal.Texture):
            return value, candidate, "parent default"

    try:
        names = MEL.get_texture_parameter_names(parent)
    except Exception:
        names = []
    for name in names:
        lowered = str(name).lower()
        for candidate in candidate_names:
            if candidate.lower().replace(" ", "") in lowered.replace(" ", ""):
                try:
                    value = MEL.get_material_default_texture_parameter_value(parent, name)
                except Exception:
                    value = None
                if isinstance(value, unreal.Texture):
                    return value, str(name), "parent scan"

    return None, None, None


def _resolve_tint(material: Any, tint_candidates: List[str]) -> Tuple[Tuple[float, float, float], Optional[str]]:
    if unreal is None or material is None:
        return (1.0, 1.0, 1.0), None
    MEL = unreal.MaterialEditingLibrary

    if isinstance(material, unreal.MaterialInstance):
        try:
            entries = material.get_editor_property("vector_parameter_values")
        except Exception:
            entries = []
        by_name = {}
        for entry in entries:
            try:
                by_name[str(entry.parameter_info.name)] = entry.get_editor_property("parameter_value")
            except Exception:
                continue
        for candidate in tint_candidates:
            for name, value in by_name.items():
                if name.lower().replace(" ", "") == candidate.lower().replace(" ", "") and value is not None:
                    return (value.r, value.g, value.b), name

        parent = None
        try:
            parent = material.get_editor_property("parent")
        except Exception:
            parent = None
        if parent is not None:
            for candidate in tint_candidates:
                try:
                    value = MEL.get_material_default_vector_parameter_value(parent, candidate)
                except Exception:
                    value = None
                if value is not None:
                    return (value.r, value.g, value.b), candidate + " (parent)"

    return (1.0, 1.0, 1.0), None


def _export_decal_materials(world, decals: List[Dict[str, Any]], cfg: ResolvedConfig,
                             decal_output_dir: str, godot_decal_root: str,
                             warnings: List[str]) -> Dict[str, Dict[str, Any]]:
    mask_names = cfg.get("decals.material_parameters.mask", ["Mask", "Opacity Mask", "Alpha"])
    tint_names = cfg.get("decals.material_parameters.tint", ["Tint 02", "Tint 01", "Color"])
    normal_names = cfg.get("decals.material_parameters.normal", ["Normal", "NormalMap"])
    bake_resolution = cfg.get("decals.bake_resolution", 1024)
    mask_from_alpha = cfg.get("decals.mask_from_alpha", False)
    invert_mask = cfg.get("decals.invert_mask", False)
    flat_mask_policy = cfg.get("decals.flat_mask_policy", "warn")

    texture_chain = build_texture_export_chain(
        enable_task=cfg.get("decals.enable_export_task_fallback", True),
        enable_source=cfg.get("decals.enable_source_file_fallback", True),
    )
    texture_outcomes: Dict[str, Any] = {}

    by_material: Dict[str, int] = {}
    for entry in decals:
        if not isinstance(entry, dict):
            continue
        path = entry.get("material_path") or (entry.get("material") or {}).get("path")
        if path:
            by_material[path] = by_material.get(path, 0) + 1

    results: Dict[str, Dict[str, Any]] = {}

    for material_path, count in sorted(by_material.items(), key=lambda kv: -kv[1]):
        try:
            try:
                material = unreal.load_asset(material_path)
            except Exception:
                material = None
            if material is None:
                warnings.append(f"Decal material not found: {material_path}")
                continue

            label = _safe_name(material_path.rsplit(".", 1)[-1])

            mask_texture, mask_param, mask_origin = _resolve_texture_parameter(material, mask_names)
            if mask_texture is None:
                warnings.append(
                    f"Decal material {label}: no mask texture found among {mask_names} — "
                    "skipped (would be a solid rectangle without one)."
                )
                continue

            tint, tint_name = _resolve_tint(material, tint_names)

            raw_path = os.path.join(decal_output_dir, f"_raw_{label}.png")
            final_path = os.path.join(decal_output_dir, f"DEC_{label}.png")

            mask_outcome = export_texture_with_fallbacks(
                world, mask_texture, raw_path, bake_resolution, chain=texture_chain)
            texture_outcomes[f"{label}:mask"] = mask_outcome
            if not mask_outcome.succeeded:
                warnings.append(
                    f"Decal material {label}: extraction du masque impossible par "
                    f"aucune des {len(texture_chain.strategies)} voies — "
                    f"{mask_outcome.failure_summary()}"
                )
                continue
            if mask_outcome.used_fallback():
                warnings.append(
                    f"Decal material {label}: masque obtenu par la voie de repli "
                    f"'{mask_outcome.strategy}' ({mask_outcome.caveat})"
                )

            try:
                width, height, mean, lowest, highest = png_codec.compose_tinted_rgba(
                    raw_path, final_path, list(tint),
                    mask_from_alpha=mask_from_alpha, invert=invert_mask,
                )
            except Exception as exc:
                warnings.append(f"Decal material {label}: RGBA composition failed ({exc}) — skipped.")
                continue

            entry_out: Dict[str, Any] = {
                "ue_material_path": material_path,
                "name": label,
                "godot_path": f"{godot_decal_root}/DEC_{label}.png",
                "disk_path": final_path.replace("\\", "/"),
                "placement_count": count,
                "tint": list(tint),
                "tint_parameter": tint_name,
                "mask_texture": mask_texture.get_name() if hasattr(mask_texture, "get_name") else str(mask_texture),
                "mask_parameter": mask_param,
                "resolution": [width, height],
                "alpha_mean": round(mean, 2),
                "alpha_min": lowest,
                "alpha_max": highest,
            }

            if lowest == highest:
                msg = (f"Decal material {label}: mask is completely flat (alpha {lowest} "
                       "everywhere) — every placement using it will render as a solid rectangle.")
                entry_out["warning"] = msg
                warnings.append(msg)
                if flat_mask_policy == "fail":
                    continue

            normal_texture, normal_param, _ = _resolve_texture_parameter(material, normal_names)
            if normal_texture is not None:
                normal_path = os.path.join(decal_output_dir, f"DEC_{label}_normal.png")
                normal_outcome = export_texture_with_fallbacks(
                    world, normal_texture, normal_path, bake_resolution, chain=texture_chain)
                texture_outcomes[f"{label}:normal"] = normal_outcome
                if normal_outcome.succeeded:
                    entry_out["normal_godot_path"] = f"{godot_decal_root}/DEC_{label}_normal.png"
                    entry_out["normal_strategy"] = normal_outcome.strategy

            try:
                os.remove(raw_path)
            except Exception:
                pass

            results[material_path] = entry_out
        except Exception as exc:
            warnings.append(f"Error processing decal material {material_path}: {exc}")

    summary = summarize_outcomes(texture_outcomes)
    if summary["fallback_count"]:
        warnings.append(
            f"{summary['fallback_count']} texture(s) obtenue(s) par une voie de repli "
            f"— répartition : {summary['by_strategy']}"
        )
    return results


def run(cfg: ResolvedConfig) -> StepReport:
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    start_time = time.time()

    enabled = cfg.get("decals.enabled", True)
    export_root = cfg.get("paths.ue_export_root", "C:/Export")
    manifest_path = os.path.join(export_root, "level_manifest_v10.json")
    decal_map_path = os.path.join(export_root, "GodotAssets", "ue5_godot_decal_map.json")
    decal_output_dir = os.path.join(export_root, "GodotAssets", "Decals")
    godot_asset_root = cfg.get("paths.godot_asset_root", "res://UEAssets")
    godot_decal_root = f"{godot_asset_root}/Decals"

    errors: List[str] = []
    warnings: List[str] = []
    counters = {"decals": 0, "decal_materials_baked": 0, "vfx": 0, "audio": 0}

    if not enabled:
        return StepReport(
            step="step4_decals_vfx", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="OK", started_at=started_at, duration_s=time.time() - start_time,
            warnings=["Decals/VFX export disabled in config."], counters=counters,
        )

    if not os.path.isfile(manifest_path):
        return StepReport(
            step="step4_decals_vfx", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="FAILED", started_at=started_at, duration_s=time.time() - start_time,
            errors=[f"Manifest file not found: {manifest_path}"], counters=counters,
        )

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
    except Exception as exc:
        return StepReport(
            step="step4_decals_vfx", run_id=cfg.run_id, config_hash=cfg.config_hash,
            status="FAILED", started_at=started_at, duration_s=time.time() - start_time,
            errors=[f"Failed to load manifest JSON ({manifest_path}): {exc}"], counters=counters,
        )

    effects = manifest_data.get("effects", {})
    decals = effects.get("decals", []) if isinstance(effects, dict) else []
    niagara = effects.get("niagara", []) if isinstance(effects, dict) else []
    world_features = manifest_data.get("world_features", {})
    audio_list = world_features.get("audio", []) if isinstance(world_features, dict) else []

    counters["decals"] = len(decals)
    counters["vfx"] = len(niagara)
    counters["audio"] = len(audio_list)

    decal_materials: Dict[str, Dict[str, Any]] = {}
    if decals:
        if unreal is None:
            errors.append("Unreal Engine Python API is unavailable — cannot bake decal textures.")
        else:
            os.makedirs(decal_output_dir, exist_ok=True)
            world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
            decal_materials = _export_decal_materials(
                world, decals, cfg, decal_output_dir, godot_decal_root, warnings
            )
            counters["decal_materials_baked"] = len(decal_materials)

    vfx_systems: Dict[str, int] = {}
    for n in niagara:
        if isinstance(n, dict):
            sys_name = n.get("system_name") or n.get("name", "UnknownSystem")
            vfx_systems[sys_name] = vfx_systems.get(sys_name, 0) + 1

    audio_policy = cfg.get("audio.policy", "ignore")
    audio_markers = []
    if audio_policy == "markers":
        for a in audio_list:
            if isinstance(a, dict):
                audio_markers.append({
                    "component_path": a.get("component_path"),
                    "sound_path": a.get("sound_path"),
                    "transform": a.get("transform"),
                })
    elif audio_policy not in ("ignore",):
        warnings.append(f"Unknown audio.policy '{audio_policy}' — treated as 'ignore'.")

    decal_map_payload = {
        "exporter_version": "1.0",
        "manifest_version": manifest_data.get("manifest_version", "10.0"),
        "godot_decal_root": godot_decal_root,
        "_resolved": {"run_id": cfg.run_id, "config_hash": cfg.config_hash},
        "decal_materials": decal_materials,
        "decal_placement_total": len(decals),
        "vfx": {
            "converted": False,
            "reason": "Niagara non convertible, placement seul",
            "systems": vfx_systems,
            "placement_total": len(niagara),
        },
        "audio": {
            "policy": audio_policy,
            "markers": audio_markers,
            "placement_total": len(audio_list),
        },
    }

    os.makedirs(os.path.dirname(decal_map_path), exist_ok=True)
    with open(decal_map_path, "w", encoding="utf-8") as f:
        json.dump(decal_map_payload, f, indent=2, ensure_ascii=False)

    outputs = [{"path": decal_map_path, "bytes": os.path.getsize(decal_map_path)}]
    status = "FAILED" if errors else ("OK_WITH_WARNINGS" if warnings else "OK")

    return StepReport(
        step="step4_decals_vfx", run_id=cfg.run_id, config_hash=cfg.config_hash,
        status=status, started_at=started_at, duration_s=time.time() - start_time,
        outputs=outputs, counters=counters, errors=errors, warnings=warnings,
    )
