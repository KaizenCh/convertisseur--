# -*- coding: utf-8 -*-
"""Crosschecking tools between manifest, asset_map, and decal_map."""

from typing import Dict, Any, List, Tuple, Optional
from ue2godot.core.schema.validators import (
    validate_manifest_schema, validate_asset_map_schema, validate_decal_map_schema,
)


def crosscheck_json_outputs(
    manifest: Dict[str, Any],
    asset_map: Dict[str, Any],
    decal_map: Optional[Dict[str, Any]] = None
) -> Tuple[bool, List[str], List[str]]:
    """Vérification croisée automatique des JSONs.

    Returns: (is_valid, errors, warnings)
    """
    errors: List[str] = []
    warnings: List[str] = []

    # Schema shape first — validate_*_schema() existed but was never
    # called from anywhere in the repo (ANALYSE_PROFONDE_S15 §15.5). A
    # malformed JSON should fail loudly here, before the run_id/count
    # checks below try to read keys that might not exist.
    manifest_ok, manifest_schema_errors = validate_manifest_schema(manifest)
    if not manifest_ok:
        errors.extend(f"Schéma manifest : {e}" for e in manifest_schema_errors)

    asset_map_ok, asset_map_schema_errors = validate_asset_map_schema(asset_map)
    if not asset_map_ok:
        errors.extend(f"Schéma asset map : {e}" for e in asset_map_schema_errors)

    if decal_map:
        decal_map_ok, decal_map_schema_errors = validate_decal_map_schema(decal_map)
        if not decal_map_ok:
            errors.extend(f"Schéma decal map : {e}" for e in decal_map_schema_errors)

    # Check run_id / config_hash consistency if present
    m_resolved = manifest.get("_resolved", {}) or manifest.get("pipeline", {}).get("resolved_config", {}).get("_resolved", {})
    a_resolved = asset_map.get("_resolved", {})

    m_run_id = m_resolved.get("run_id")
    a_run_id = a_resolved.get("run_id")

    if m_run_id and a_run_id and m_run_id != a_run_id:
        errors.append(f"Divergence run_id entre manifest ({m_run_id}) et asset_map ({a_run_id}). Relance détectée !")

    # Unique meshes vs asset_map assets
    unique_meshes = len(manifest.get("geometry", {}).get("unique_meshes", {}))
    assets = asset_map.get("assets", {})

    has_landscape = "/AutoTerrain/Landscape.BakedLandscape" in assets
    expected_assets = unique_meshes + (1 if has_landscape else 0)

    if len(assets) != expected_assets:
        warnings.append(
            f"Compte unique_meshes ({unique_meshes}) + landscape ({1 if has_landscape else 0}) != assets ({len(assets)})"
        )

    # Check missing GLB files if disk status is available
    failures = asset_map.get("failures", [])
    if failures:
        warnings.append(f"{len(failures)} échec(s) d'export GLB signalés dans asset_map.")

    if decal_map:
        d_resolved = decal_map.get("_resolved", {})
        d_run_id = d_resolved.get("run_id")
        if m_run_id and d_run_id and m_run_id != d_run_id:
            errors.append(f"Divergence run_id entre manifest ({m_run_id}) et decal_map ({d_run_id}).")

    # Contrat de readiness (§3.2.8, §9/§11, décision H2 du doc d'architecture) :
    # policy "warn" — le manifeste peut désormais réellement dire "non prêt"
    # (step1_manifest.py ne force plus True inconditionnellement), donc ce
    # signal a un sens à faire remonter ici. On avertit plutôt que d'échouer
    # dur, pour ne pas bloquer un run avec seulement quelques placements
    # isolés en échec — cohérent avec la tolérance par catégorie du
    # reconstructeur .gd (5 FAIL_ON_* distincts, jamais un seul flag global).
    reconstruction = manifest.get("reconstruction", {})
    if reconstruction.get("ready_for_godot_geometry") is False:
        warnings.append("manifest.reconstruction.ready_for_godot_geometry = false — voir les warnings du manifest.")
    if reconstruction.get("ready_for_godot_fx") is False:
        warnings.append("manifest.reconstruction.ready_for_godot_fx = false — voir les warnings du manifest.")

    return len(errors) == 0, errors, warnings
