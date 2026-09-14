# -*- coding: utf-8 -*-
"""
Step 4: Decals texture bake, VFX markers, and audio markers.

Previously this step only wrote a decal_map.json with a fabricated
fallback tint and a godot_path pointing at a PNG that was never created —
none of the texture resolution cascade, render-target bake, or RGBA
composition from unreal_export_decals_vfx.py had survived the port (see
ANALYSE_PROFONDE_S15 §15.7). This version ports that logic faithfully:
resolve mask/tint/normal from the material (instance override -> parent
default -> parent scan), bake the mask via a temp unlit material, compose
the final tinted RGBA with png_codec — exactly as the reference does it,
just deduplicated per material path via ue2godot.ue.rendertarget instead
of duplicating the bake routine a second time.
"""

import os
import json
import time
from typing import Dict, Any, List, Tuple, Optional

from ue2godot.core.config import ResolvedConfig
from ue2godot.core.report import StepReport
from ue2godot.core import png_codec
from ue2godot.ue.rendertarget import export_texture_to_png

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
    """(texture, parameter_name, origin) — instance override, then parent
    default, then a substring scan of the parent's exposed names. Mirrors
    unreal_export_decals_vfx.py::resolve_texture_parameter() exactly."""
    if unreal is None:
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
    """(r, g, b) in 0..1 + the parameter name used, or white + None if
    nothing was found — visible rather than silently dark (§3.6)."""
    if unreal is None:
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

    by_material: Dict[str, int] = {}
    for entry in decals:
        path = entry.get("material_path") or (entry.get("material") or {}).get("path")
        if path:
            by_material[path] = by_material.get(path, 0) + 1

    results: Dict[str, Dict[str, Any]] = {}

    for material_path, count in sorted(by_material.items(), key=lambda kv: -kv[1]):
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

        if not export_texture_to_png(world, mask_texture, raw_path, bake_resolution):
            warnings.append(f"Decal material {label}: mask bake failed — skipped.")
            continue

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
            if export_texture_to_png(world, normal_texture, normal_path, bake_resolution):
                entry_out["normal_godot_path"] = f"{godot_decal_root}/DEC_{label}_normal.png"

        try:
            os.remove(raw_path)
        except Exception:
            pass

        results[material_path] = entry_out

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

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)

    effects = manifest_data.get("effects", {})
    decals = effects.get("decals", [])
    niagara = effects.get("niagara", [])
    audio_list = manifest_data.get("world_features", {}).get("audio", [])

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
            if len(decal_materials) < len({
                (d.get("material_path") or (d.get("material") or {}).get("path"))
                for d in decals if d.get("material_path") or d.get("material")
            }):
                warnings.append(
                    "Not every distinct decal material could be baked — see warnings above. "
                    "Placements referencing a failed material will have no texture in Godot."
                )

    vfx_systems: Dict[str, int] = {}
    for n in niagara:
        sys_name = n.get("system_name") or n.get("name", "UnknownSystem")
        vfx_systems[sys_name] = vfx_systems.get(sys_name, 0) + 1

    # H8 (voir doc d'architecture) : pas de défaut silencieux. Le champ doit
    # être explicitement positionné par l'appelant (main.py le fait depuis
    # le module "audio" de la page Options) ; "ignore" si rien n'est fourni,
    # plutôt qu'un "markers" implicite qui ferait comme si l'audio avait
    # toujours été un choix assumé — ce n'est pas le cas (§13.C.25).
    audio_policy = cfg.get("audio.policy", "ignore")
    audio_markers = []
    if audio_policy == "markers":
        for a in audio_list:
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
