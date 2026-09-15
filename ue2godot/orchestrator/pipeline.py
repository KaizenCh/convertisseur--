# -*- coding: utf-8 -*-
"""
Main pipeline state machine — steps 0-5, in the order dependency demands
(§1/§10: manifest and meshes rewrite their JSON from scratch, landscape
and decals only append, so they must always run after).

Previously this class's docstring promised gates G0-G6 that were never
implemented — run_pipeline() called nothing but copy_step5(), a single
line duplicating (worse) what main.py already did directly. main.py has
since stopped duplicating this logic itself and calls this class instead
(see ANALYSE_PROFONDE_S15 §15.1/§15.5) — this is now the one real
implementation, not a second one.

Steps 0-4 (Unreal-side) only import `unreal`-dependent modules lazily,
inside run_pipeline(), so this file itself stays importable from a plain
Python environment (e.g. for the Godot-side steps or from main.py's UI
process, which never has `unreal` available).
"""

from typing import Dict, Any, List, Optional
from ue2godot.core.config import ResolvedConfig
from ue2godot.core.report import StepReport
from ue2godot.orchestrator.step5_copy import copy_step5


class PipelineOrchestrator:
    def __init__(self, cfg: ResolvedConfig):
        self.cfg = cfg

    def run_unreal_steps(self, run_preprocess: bool = False) -> List[StepReport]:
        """Steps 0-4, in dependency order. Must be called from inside the
        Unreal-embedded Python session (`import unreal` available) — see
        ue2godot.ue.entry for the actual in-editor entry point. Returns
        the reports in execution order; stops early (without running
        later steps) if manifest or meshes FAILED, since everything after
        depends on their output existing."""
        from ue2godot.ue.steps import (
            step0_preprocess, step1_manifest, step2_meshes,
            step3_landscape, step4_decals_vfx,
        )

        reports: List[StepReport] = []

        if run_preprocess:
            reports.append(step0_preprocess.run(self.cfg))

        manifest_report = step1_manifest.run(self.cfg)
        reports.append(manifest_report)
        if manifest_report.status == "FAILED":
            return reports

        meshes_report = step2_meshes.run(self.cfg)
        reports.append(meshes_report)
        if meshes_report.status == "FAILED":
            return reports

        # step3/step4 already check cfg.get("landscape.enabled"/"decals.enabled")
        # themselves and return an informative OK report ("disabled in
        # config") rather than nothing at all — so they are always called
        # here, and the config toggle is respected inside each step, not
        # duplicated as a second check at the orchestrator level.
        reports.append(step3_landscape.run(self.cfg))
        reports.append(step4_decals_vfx.run(self.cfg))

        return reports

    def crosscheck(self, manifest_path: str, asset_map_path: str,
                    decal_map_path: Optional[str] = None) -> StepReport:
        """Gate before any Godot-side reconstruction: compares run_id /
        asset counts across the 3 JSON outputs. Catches, mechanically, the
        one mistake the reference source of truth calls out repeatedly
        (§1/§10): relaunching manifest or meshes after landscape/decals
        silently erases their work."""
        import json
        import time
        from ue2godot.core.crosscheck import crosscheck_json_outputs

        started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        start = time.time()

        def _load(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}

        manifest = _load(manifest_path)
        asset_map = _load(asset_map_path)
        decal_map = _load(decal_map_path) if decal_map_path else None

        is_valid, cc_errors, cc_warnings = crosscheck_json_outputs(manifest, asset_map, decal_map)

        return StepReport(
            step="crosscheck", run_id=self.cfg.run_id, config_hash=self.cfg.config_hash,
            status="OK" if is_valid else "FAILED",
            started_at=started_at, duration_s=time.time() - start,
            errors=cc_errors, warnings=cc_warnings,
        )

    def run_pipeline(self) -> List[StepReport]:
        """Godot-side only: copy exported assets into the target project.
        Kept separate from run_unreal_steps() because it runs in a
        different process (no `unreal` import needed/available here)."""
        return [copy_step5(self.cfg)]
