# -*- coding: utf-8 -*-
"""
Moteur de l'application — tout ce qui n'est pas de la présentation.

SÉPARATION UI / MOTEUR
-----------------------
Ce module contient l'analyse de source, la configuration, la découverte
de Godot, la copie d'assets, l'exécution distante Unreal et la
construction headless. Il ne construit AUCUN widget de mise en page.
"""

from __future__ import annotations

import json
import os
import re
import sys
import shutil
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

try:
    from PySide6.QtCore import QObject, QProcess, Signal
except ImportError:
    class QObject:
        def __init__(self, *args, **kwargs): pass
    class QProcess:
        pass
    class _DummySignal:
        def connect(self, *args, **kwargs): pass
        def disconnect(self, *args, **kwargs): pass
        def emit(self, *args, **kwargs): pass
    def Signal(*args, **kwargs):
        return _DummySignal()


import ue2godot
from ue2godot.core.config import ResolvedConfig, deep_merge, compute_config_hash
from ue2godot.orchestrator.step5_copy import copy_step5


APP_NAME = "UE5 → GODOT CONSTRUCTOR"
APP_VERSION = "0.2.0"
CONFIG_EXTENSION = ".gconstructor.json"


# ============================================================================
# PERSISTED CONFIGURATION
# ============================================================================

DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "project": {
        "name": "",
        "godot_project": "",
        "config_path": "",
    },
    "source": {
        "unreal_map": "",
        "export_output": "",
        "preprocessing_confirmed": False,
    },
    "construction": {
        "workflow": "full_reconstruction",
        "objective": "maximum",
        "fidelity": "maximum",
        "fidelity_percent": 100,
        "modules": {
            "landscape": True,
            "materials": True,
            "decals": True,
            "lighting": True,
            "vfx": True,
            "audio": False,
            "level_instances": True,
        },
        "vfx_mode": "markers",
    },
    "advanced": {
        "transform_policy": "preserve",
        "hierarchy_policy": "preserve",
        "asset_resolution": "strict",
        "validation_mode": "strict",
        "fallback_policy": "report",
        "overwrite_policy": "safe",
        "resume_enabled": True,
        "keep_intermediate": True,
        "verbose_logs": False,
        "godot_executable": "",
        "unreal_executable": "",
        "min_expected_actors": 20,
        "force_map_reload": False,
        "constructor_script": "",
        "godot_asset_root": "UEAssets",
        "expected_axis_convention": "(ue.x, ue.z, ue.y) * 0.01",
    },
    "ui": {
        "expert_mode": False,
        "theme": "dark",
    },
}


# ============================================================================
# GENERIC HELPERS
# ============================================================================

def deep_copy_config() -> dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_CONFIG))


def format_number(value: int | float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,}".replace(",", " ")


def now_string() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def find_first_existing(root: Path, candidates: list[str]) -> Optional[Path]:
    for relative in candidates:
        candidate = root / relative
        if candidate.exists():
            return candidate
    return None


def read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def recursive_find_json(root: Path, names: set[str]) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []

    result: list[Path] = []
    try:
        for path in root.rglob("*.json"):
            if path.name in names:
                result.append(path)
    except (OSError, PermissionError):
        pass
    return result


def count_glb(root: Path) -> int:
    if not root.exists() or not root.is_dir():
        return 0

    try:
        return sum(1 for _ in root.rglob("*.glb"))
    except (OSError, PermissionError):
        return 0


def count_png(root: Path) -> int:
    if not root.exists() or not root.is_dir():
        return 0

    try:
        return sum(1 for _ in root.rglob("*.png"))
    except (OSError, PermissionError):
        return 0


# ============================================================================
# SOURCE ANALYSIS
# ============================================================================

@dataclass
class SourceInventory:
    source_root: str = ""
    manifest: str = ""
    asset_map: str = ""
    decal_map: str = ""
    mesh_directory: str = ""
    decal_directory: str = ""

    manifest_found: bool = False
    asset_map_found: bool = False
    decal_map_found: bool = False
    mesh_directory_found: bool = False

    mesh_files: int = 0
    decal_files: int = 0

    placements: Optional[int] = None
    unique_meshes: Optional[int] = None
    unique_skeletal_meshes: Optional[int] = None
    skeletal_mesh_placements: Optional[int] = None
    level_instances: Optional[int] = None
    landscapes: Optional[int] = None
    lights: Optional[int] = None
    vfx: Optional[int] = None
    decals: Optional[int] = None
    audio: Optional[int] = None
    materials: Optional[int] = None

    ready_for_godot_geometry: Optional[bool] = None
    ready_for_godot_fx: Optional[bool] = None
    reconstruction_blockers: list[str] = field(default_factory=list)

    asset_map_entries: Optional[int] = None
    asset_map_failures: Optional[int] = None
    landscape_entry_present: Optional[bool] = None
    axis_map_label: str = ""

    decal_material_entries: Optional[int] = None
    decal_placement_total: Optional[int] = None

    manifest_version: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def is_detected(self) -> bool:
        return bool(self.source_root)

    @property
    def core_valid(self) -> bool:
        return (
            self.manifest_found
            and self.asset_map_found
            and self.mesh_directory_found
            and self.mesh_files > 0
        )


class SourceAnalyzer:
    MANIFEST_NAMES = {
        "level_manifest_v10.json",
        "level_manifest_v9.json",
        "level_manifest_v8.json",
    }

    ASSET_MAP_NAMES = {
        "ue5_godot_asset_map.json",
    }

    DECAL_MAP_NAMES = {
        "ue5_godot_decal_map.json",
    }

    LANDSCAPE_SYNTHETIC_KEY = "/AutoTerrain/Landscape.BakedLandscape"

    def analyze(self, source: str) -> SourceInventory:
        input_path = Path(source).expanduser()

        if not input_path.exists():
            inventory = SourceInventory(source_root=str(input_path))
            inventory.errors.append("Le chemin source n'existe pas.")
            return inventory

        if input_path.is_file():
            root = input_path.parent
        else:
            root = input_path

        curr = root
        search_roots = [root]
        for _ in range(5):
            if (curr / "level_manifest_v10.json").exists() or (curr / "GodotAssets").exists():
                root = curr
                break
            if (curr / "Export").exists():
                search_roots.append(curr / "Export")
            if curr.parent == curr:
                break
            curr = curr.parent

        inventory = SourceInventory(source_root=str(root))

        manifest = find_first_existing(
            root,
            [
                "level_manifest_v10.json",
                "manifest/level_manifest_v10.json",
                "level_manifest.json",
            ],
        )

        if manifest is None:
            matches = recursive_find_json(root, self.MANIFEST_NAMES)
            manifest = matches[0] if matches else None

        if manifest:
            inventory.manifest = str(manifest)
            inventory.manifest_found = True
            self._read_manifest(manifest, inventory)
        else:
            inventory.notes.append(
                "Manifest introuvable dans le dossier sélectionné. L'étape 1 (Manifest) générera "
                "level_manifest_v10.json à partir de cette map Unreal."
            )

        asset_map = find_first_existing(
            root,
            [
                "ue5_godot_asset_map.json",
                "GodotAssets/ue5_godot_asset_map.json",
                "assets/ue5_godot_asset_map.json",
            ],
        )

        if asset_map is None:
            matches = recursive_find_json(root, self.ASSET_MAP_NAMES)
            asset_map = matches[0] if matches else None

        if asset_map:
            inventory.asset_map = str(asset_map)
            inventory.asset_map_found = True
            self._read_asset_map(asset_map, inventory)
        else:
            inventory.notes.append(
                "Asset map introuvable (ue5_godot_asset_map.json). L'étape 2 (Meshes) générera l'asset map."
            )

        decal_map = find_first_existing(
            root,
            [
                "ue5_godot_decal_map.json",
                "GodotAssets/ue5_godot_decal_map.json",
                "assets/ue5_godot_decal_map.json",
            ],
        )

        if decal_map is None:
            matches = recursive_find_json(root, self.DECAL_MAP_NAMES)
            decal_map = matches[0] if matches else None

        if decal_map:
            inventory.decal_map = str(decal_map)
            inventory.decal_map_found = True
            self._read_decal_map(decal_map, inventory)

        mesh_dir = self._find_mesh_directory(root)
        if mesh_dir:
            inventory.mesh_directory = str(mesh_dir)
            inventory.mesh_directory_found = True
            inventory.mesh_files = count_glb(mesh_dir)
        else:
            inventory.notes.append(
                "Dossier de meshes introuvable (GodotAssets/Meshes). L'étape 2 (Meshes) exportera les GLB."
            )

        decal_dir = self._find_decal_directory(root)
        if decal_dir:
            inventory.decal_directory = str(decal_dir)
            inventory.decal_files = count_png(decal_dir)

        self._cross_validate(inventory)

        return inventory

    def _find_mesh_directory(self, root: Path) -> Optional[Path]:
        candidates = [
            root / "GodotAssets" / "Meshes",
            root / "assets" / "Meshes",
            root / "Meshes",
        ]

        for candidate in candidates:
            if candidate.exists() and candidate.is_dir():
                return candidate

        try:
            for candidate in root.rglob("Meshes"):
                if candidate.is_dir() and any(candidate.glob("*.glb")):
                    return candidate
        except (OSError, PermissionError):
            pass

        return None

    def _find_decal_directory(self, root: Path) -> Optional[Path]:
        candidates = [
            root / "GodotAssets" / "Decals",
            root / "assets" / "Decals",
            root / "Decals",
        ]

        for candidate in candidates:
            if candidate.exists() and candidate.is_dir():
                return candidate

        try:
            for candidate in root.rglob("Decals"):
                if candidate.is_dir() and any(candidate.glob("*.png")):
                    return candidate
        except (OSError, PermissionError):
            pass

        return None

    def _read_manifest(self, path: Path, inventory: SourceInventory) -> None:
        data = read_json(path)
        if data is None:
            inventory.errors.append("Le manifest existe mais n'est pas lisible (JSON invalide).")
            return

        inventory.manifest_version = str(data.get("manifest_version") or "inconnue")

        geometry = data.get("geometry") if isinstance(data.get("geometry"), dict) else {}
        level_instances = data.get("level_instances") if isinstance(data.get("level_instances"), dict) else {}
        materials = data.get("materials") if isinstance(data.get("materials"), dict) else {}
        effects = data.get("effects") if isinstance(data.get("effects"), dict) else {}
        world_features = data.get("world_features") if isinstance(data.get("world_features"), dict) else {}
        reconstruction = data.get("reconstruction") if isinstance(data.get("reconstruction"), dict) else {}

        inventory.unique_meshes = self._len_of(geometry.get("unique_meshes"))
        inventory.placements = self._len_of(geometry.get("placements"))
        inventory.unique_skeletal_meshes = self._len_of(geometry.get("unique_skeletal_meshes"))
        inventory.skeletal_mesh_placements = self._len_of(geometry.get("skeletal_mesh_placements"))

        inventory.level_instances = self._len_of(level_instances.get("placements"))

        inventory.materials = self._len_of(materials.get("unique_materials"))

        inventory.vfx = self._len_of(effects.get("niagara"))
        inventory.decals = self._len_of(effects.get("decals"))

        inventory.landscapes = self._len_of(world_features.get("landscape"))
        inventory.lights = self._len_of(world_features.get("lights"))
        inventory.audio = self._len_of(world_features.get("audio"))

        if isinstance(reconstruction.get("ready_for_godot_geometry"), bool):
            inventory.ready_for_godot_geometry = reconstruction["ready_for_godot_geometry"]
        if isinstance(reconstruction.get("ready_for_godot_fx"), bool):
            inventory.ready_for_godot_fx = reconstruction["ready_for_godot_fx"]

        for key, value in reconstruction.items():
            if key in ("ready_for_godot_geometry", "ready_for_godot_fx"):
                continue
            if isinstance(value, str) and value:
                inventory.reconstruction_blockers.append(f"{key}: {value}")
            elif isinstance(value, list) and value:
                inventory.reconstruction_blockers.append(
                    f"{key}: {len(value)} élément(s)"
                )

    def _read_asset_map(self, path: Path, inventory: SourceInventory) -> None:
        data = read_json(path)
        if data is None:
            inventory.errors.append("L'asset map existe mais n'est pas lisible (JSON invalide).")
            return

        assets = data.get("assets") if isinstance(data.get("assets"), dict) else {}
        inventory.asset_map_entries = len(assets)

        failures = data.get("failures")
        if isinstance(failures, list):
            inventory.asset_map_failures = len(failures)

        inventory.landscape_entry_present = self.LANDSCAPE_SYNTHETIC_KEY in assets

        landscape_entry = assets.get(self.LANDSCAPE_SYNTHETIC_KEY)
        if isinstance(landscape_entry, dict):
            axis_label = landscape_entry.get("axis_map") or landscape_entry.get("axis_map_label")
            if isinstance(axis_label, str):
                inventory.axis_map_label = axis_label

    def _read_decal_map(self, path: Path, inventory: SourceInventory) -> None:
        data = read_json(path)
        if data is None:
            inventory.errors.append("La decal map existe mais n'est pas lisible (JSON invalide).")
            return

        decal_materials = data.get("decal_materials") if isinstance(data.get("decal_materials"), dict) else {}
        inventory.decal_material_entries = len(decal_materials)

        total = data.get("decal_placement_total")
        if isinstance(total, int) and not isinstance(total, bool):
            inventory.decal_placement_total = total

    def _cross_validate(self, inventory: SourceInventory) -> None:
        if inventory.asset_map_entries is not None and inventory.mesh_files:
            expected_min = inventory.asset_map_entries - (1 if inventory.landscape_entry_present else 0)
            if inventory.mesh_files < expected_min:
                inventory.errors.append(
                    f"L'asset map référence {inventory.asset_map_entries} assets, mais seulement "
                    f"{inventory.mesh_files} fichiers GLB sont présents sur disque : au moins "
                    f"{expected_min - inventory.mesh_files} export(s) manquant(s) ou déplacé(s)."
                )
            elif inventory.mesh_files > inventory.asset_map_entries:
                inventory.warnings.append(
                    f"{inventory.mesh_files} fichiers GLB présents pour {inventory.asset_map_entries} "
                    "entrées déclarées dans l'asset map — des GLB orphelins (anciens exports) "
                    "traînent peut-être dans le dossier Meshes."
                )

        if inventory.unique_meshes is not None and inventory.asset_map_entries is not None:
            expected = inventory.unique_meshes + (1 if inventory.landscape_entry_present else 0)
            if inventory.asset_map_entries != expected:
                inventory.errors.append(
                    f"Incohérence manifest ↔ asset map : {inventory.unique_meshes} unique_meshes "
                    f"dans le manifest, {inventory.asset_map_entries} entrées dans l'asset map "
                    f"(attendu {expected}). Ordre d'exécution probablement rompu : le manifest ou "
                    "l'export des meshes a peut-être été relancé après une autre étape, écrasant "
                    "un travail précédent."
                )

        if inventory.asset_map_failures:
            inventory.warnings.append(
                f"{inventory.asset_map_failures} échec(s) d'export signalés dans l'asset map."
            )

        if (inventory.landscapes or 0) > 0 and inventory.asset_map_found and not inventory.landscape_entry_present:
            inventory.warnings.append(
                "Le manifest décrit un Landscape mais aucune entrée Landscape "
                f"({self.LANDSCAPE_SYNTHETIC_KEY}) n'est présente dans l'asset map."
            )

        if (inventory.decals or 0) > 0:
            if not inventory.decal_map_found:
                inventory.warnings.append(
                    f"{inventory.decals} decal(s) déclaré(s) dans le manifest mais "
                    "ue5_godot_decal_map.json est introuvable."
                )
            elif not inventory.decal_files:
                inventory.warnings.append(
                    "La decal map est présente mais aucun PNG n'a été trouvé dans le dossier Decals."
                )

        if (inventory.unique_skeletal_meshes or 0) > 0:
            inventory.warnings.append(
                f"Le manifest référence {inventory.unique_skeletal_meshes} SkeletalMesh unique(s) "
                f"({inventory.skeletal_mesh_placements or 0} placement(s))."
            )

        if (inventory.audio or 0) > 0:
            inventory.notes.append(
                f"{inventory.audio} élément(s) audio détecté(s) dans le manifest."
            )

        if inventory.ready_for_godot_geometry is False:
            inventory.errors.append(
                "Le manifest indique lui-même reconstruction.ready_for_godot_geometry = false."
            )
        if inventory.ready_for_godot_fx is False:
            inventory.warnings.append(
                "Le manifest indique reconstruction.ready_for_godot_fx = false."
            )

        if inventory.axis_map_label:
            inventory.notes.append(
                f"Convention d'axe déclarée par le Landscape bake : {inventory.axis_map_label}"
            )

        if inventory.manifest_found and inventory.asset_map_found:
            inventory.notes.append(
                f"Manifest détecté : manifest_version={inventory.manifest_version or 'inconnue'}"
            )

    @staticmethod
    def _len_of(value: Any) -> Optional[int]:
        if isinstance(value, (dict, list)):
            return len(value)
        return None


# ============================================================================
# PIPELINE MODEL
# ============================================================================

@dataclass
class PipelineStep:
    key: str
    label: str
    description: str
    state: str = "pending"
    detail: str = ""


def _build_pipeline_step_list() -> list["PipelineStep"]:
    return [
        PipelineStep("preprocess", "Prétraitement UE5",
                     "Détacher les acteurs (KEEP_WORLD)."),
        PipelineStep("manifest", "Manifest",
                     "Scanner la map et produire level_manifest_v10.json."),
        PipelineStep("meshes", "Meshes",
                     "Exporter les StaticMesh uniques en GLB."),
        PipelineStep("landscape", "Landscape",
                     "Assigner/vérifier le matériau du Landscape, puis reconstruire sa géométrie."),
        PipelineStep("decals", "Decals / VFX",
                     "Résoudre les textures de décal et placer les marqueurs VFX/audio."),
        PipelineStep("crosscheck", "Vérification croisée",
                     "Comparer run_id/compte d'assets entre les 3 JSON."),
    ]


# ============================================================================
# FUTURE ORCHESTRATOR CONTRACT
# ============================================================================

class OrchestratorAdapter(QObject):
    log = Signal(str)
    progress = Signal(int)
    step_changed = Signal(str)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.process: Optional[subprocess.Popen] = None
        self.last_copy_stats: dict[str, int] = {}
        self._headless_proc: Optional[QProcess] = None
        self._headless_plan: dict[str, Any] = {}
        self.last_build_report: dict[str, Any] = {}

    _COMMON_INSTALL_DIRS = (
        r"C:\Godot",
        r"C:\Program Files\Godot",
        r"C:\Program Files (x86)\Godot",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Godot"),
        os.path.expandvars(r"%USERPROFILE%\Downloads"),
        os.path.expandvars(r"%USERPROFILE%\Desktop"),
    )

    @staticmethod
    def read_project_godot_version(project_root: str) -> Optional[str]:
        if not project_root:
            return None
        cfg = Path(project_root) / "project.godot"
        if not cfg.is_file():
            return None
        try:
            text = cfg.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        match = re.search(r'config/features\s*=\s*PackedStringArray\(([^)]*)\)', text)
        if match:
            for token in re.findall(r'"([^"]+)"', match.group(1)):
                if re.fullmatch(r"\d+\.\d+", token):
                    return token
        match = re.search(r'config_version\s*=\s*(\d+)', text)
        if match and match.group(1) == "5":
            return "4"
        return None

    @staticmethod
    def executable_version(path: str) -> Optional[str]:
        match = re.search(r'[vV]?(\d+)\.(\d+)', Path(path).name)
        return f"{match.group(1)}.{match.group(2)}" if match else None

    @classmethod
    def find_godot(cls, executable: str = "", log: Optional[Any] = None,
                   project_root: str = "") -> Optional[str]:
        from ue2godot.orchestrator.godot_version import resolve_godot

        resolution = resolve_godot(
            godot_project_root=project_root,
            configured_executable=executable,
        )

        if log is not None:
            for line in resolution.trace:
                log(f"[find_godot] {line}")
            if resolution.executable and not resolution.is_version_safe:
                log(f"[find_godot] ATTENTION : {resolution.summary()}")

        return resolution.executable

    def prepare(self, config: dict[str, Any], inventory: SourceInventory) -> dict[str, Any]:
        selected_modules = [
            key for key, enabled in config["construction"]["modules"].items()
            if enabled
        ]
        return {
            "created_at": now_string(),
            "source": inventory.source_root,
            "manifest": inventory.manifest,
            "asset_map": inventory.asset_map,
            "decal_map": inventory.decal_map,
            "mesh_directory": inventory.mesh_directory,
            "decal_directory": inventory.decal_directory,
            "godot_project": config["project"]["godot_project"],
            "godot_asset_root": config["advanced"].get("godot_asset_root", "UEAssets"),
            "constructor_script": config["advanced"].get("constructor_script", ""),
            "godot_executable": config["advanced"].get("godot_executable", ""),
            "objective": config["construction"]["objective"],
            "fidelity": config["construction"]["fidelity"],
            "modules": selected_modules,
            "overwrite_policy": config["advanced"].get("overwrite_policy", "safe"),
            "fallback_policy": config["advanced"].get("fallback_policy", "report"),
            "resume_enabled": bool(config["advanced"].get("resume_enabled", True)),
            "keep_intermediate": bool(config["advanced"].get("keep_intermediate", True)),
            "verbose_logs": bool(config["advanced"].get("verbose_logs", False)),
            "decals_selected": bool(config["construction"]["modules"].get("decals", False)),
        }

    def copy_assets(self, plan: dict[str, Any]) -> bool:
        project = Path(plan.get("godot_project", "")).expanduser()
        if not project.is_dir() or not (project / "project.godot").is_file():
            self.failed.emit("Projet Godot invalide ou project.godot introuvable.")
            return False

        source_root = plan.get("source", "")
        cfg_dict = {
            "paths": {
                "ue_export_root": source_root,
                "godot_project_root": str(project),
                "godot_asset_root": plan.get("godot_asset_root", "res://UEAssets")
            }
        }
        from ue2godot.profiles.loader import load_profile, detect_profile_for_source
        profile = load_profile(plan.get("profile") or detect_profile_for_source(source_root))
        res_cfg = ResolvedConfig.resolve(cfg_dict, profile, {})

        self.log.emit(f"[copy] source : {source_root}")
        self.log.emit(f"[copy] destination : {project}")
        report = copy_step5(res_cfg)

        copied = report.counters.get("files_copied", 0)
        errors = report.errors

        for key, value in report.counters.items():
            self.log.emit(f"[copy] {key} = {value}")
        for warning in report.warnings:
            self.log.emit(f"[copy] ⚠ {warning}")
        for error in errors:
            self.log.emit(f"[copy] ✕ {error}")

        self.last_copy_stats = {"copied": copied, "skipped": 0, "errors": len(errors)}

        if report.status == "FAILED":
            self.failed.emit(f"Échec de la copie step5: {', '.join(errors)}")
            return False

        self.log.emit(f"Step 5 Copy (ue2godot) terminée : {copied} fichier(s) copié(s) et vérifié(s).")

        candidates = [
            Path(__file__).resolve().parents[2] / "godot" / "addons" / "ue2godot",
            Path(__file__).resolve().parents[3] / "godot" / "addons" / "ue2godot",
            Path.cwd() / "godot" / "addons" / "ue2godot",
            Path.cwd() / "ue2godot_v21" / "godot" / "addons" / "ue2godot",
            Path(__file__).resolve().parent / "godot" / "addons" / "ue2godot",
        ]
        framework_root = next((c for c in candidates if (c / "entry_headless.gd").is_file()), candidates[0])
        target_addon = project / "addons" / "ue2godot"
        entry_headless = framework_root / "entry_headless.gd"

        if not entry_headless.is_file():
            self.failed.emit(
                f"Runtime Godot du framework introuvable : {entry_headless}"
            )
            return False

        try:
            copied_runtime = 0
            for src in framework_root.rglob("*"):
                if not src.is_file():
                    continue
                rel = src.relative_to(framework_root)
                dst = target_addon / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied_runtime += 1

            deployed_entry = target_addon / "entry_headless.gd"
            if not deployed_entry.is_file():
                self.failed.emit(
                    "Déploiement du runtime Godot incomplet : entry_headless.gd est absent du projet cible."
                )
                return False

            self.log.emit(
                f"Runtime Godot ue2godot déployé : {copied_runtime} fichier(s) dans {target_addon}."
            )
        except (OSError, shutil.Error) as exc:
            self.failed.emit(f"Déploiement du runtime Godot impossible : {exc}")
            return False

        return True

    def open_godot(self, plan: dict[str, Any]) -> bool:
        project = Path(plan.get("godot_project", "")).expanduser()
        if not project.is_dir() or not (project / "project.godot").is_file():
            self.failed.emit("Projet Godot invalide ou project.godot introuvable.")
            return False

        godot = self.find_godot(plan.get("godot_executable", ""), log=self.log.emit,
                                project_root=plan.get("godot_project", ""))
        if not godot:
            self.failed.emit(
                "Godot n'a pas été trouvé. Configure l'exécutable dans les options avancées."
            )
            return False

        self.step_changed.emit("godot_open")

        try:
            self.process = subprocess.Popen(
                [godot, "--editor", "--path", str(project)],
                cwd=str(project),
            )
        except OSError as exc:
            self.failed.emit(f"Impossible de lancer Godot : {exc}")
            return False

        self.log.emit(f"Godot ouvert : {project}")
        script = plan.get("constructor_script", "")
        if script:
            self.log.emit(f"Constructeur sélectionné : {script}")
        else:
            self.log.emit("Aucun EditorScript explicite configuré.")
        self.step_changed.emit("done")
        self.finished.emit({"opened": True, "project": str(project)})
        return True

    def execute(self, plan: dict[str, Any]) -> None:
        if not self.copy_assets(plan):
            return
        self.open_godot(plan)

    def build_scene_headless(self, plan: dict[str, Any]) -> bool:
        project = Path(plan.get("godot_project", "")).expanduser()
        if not project.is_dir() or not (project / "project.godot").is_file():
            self.failed.emit("Projet Godot invalide ou project.godot introuvable.")
            return False

        godot = self.find_godot(plan.get("godot_executable", ""), log=self.log.emit,
                                project_root=plan.get("godot_project", ""))
        if not godot:
            self.failed.emit("Godot n'a pas été trouvé.")
            return False

        manifest_path = plan.get("manifest", "")
        if not manifest_path:
            self.failed.emit("Aucun manifest détecté.")
            return False

        entry_script = project / "addons" / "ue2godot" / "entry_headless.gd"
        if not entry_script.is_file():
            self.failed.emit(
                "Runtime Godot absent du projet cible : res://addons/ue2godot/entry_headless.gd."
            )
            return False

        self._headless_plan = dict(plan)
        self._headless_plan["_godot_exe"] = godot
        self._headless_plan["_project_path"] = str(project)

        self.step_changed.emit("headless_import")
        self.log.emit(f"Import headless des assets dans {project}…")

        self._headless_proc = QProcess(self)
        self._headless_proc.setWorkingDirectory(str(project))
        self._headless_proc.readyReadStandardOutput.connect(self._on_headless_stdout)
        self._headless_proc.readyReadStandardError.connect(self._on_headless_stdout)
        self._headless_proc.finished.connect(self._on_headless_import_finished)
        import_args = ["--headless", "--path", str(project), "--import"]
        self.log.emit(f"[cmd] {godot} {' '.join(import_args)}")
        self._headless_proc.start(godot, import_args)
        return True

    def _on_headless_stdout(self) -> None:
        if self._headless_proc is None:
            return
        data = bytes(self._headless_proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        data += bytes(self._headless_proc.readAllStandardError()).decode("utf-8", errors="replace")
        for line in data.splitlines():
            if line.strip():
                self.log.emit(line.strip())

    def _on_headless_import_finished(self, exit_code: int, _exit_status) -> None:
        try:
            self._headless_proc.finished.disconnect(self._on_headless_import_finished)
        except (RuntimeError, TypeError):
            pass

        self.log.emit(f"[cmd] import headless terminé, code de retour {exit_code}")
        if exit_code != 0:
            self.failed.emit(f"L'import headless des assets a échoué (code {exit_code}).")
            return

        project = self._headless_plan.get("_project_path", "")
        godot = self._headless_plan.get("_godot_exe", "")
        manifest_path = "res://level_manifest_v10.json"
        report_path = str(Path(project) / "ue2godot_build_report.json")

        self.step_changed.emit("headless_build")
        self.log.emit("Import terminé — construction de la scène (entry_headless.gd)…")

        self._headless_proc = QProcess(self)
        self._headless_proc.setWorkingDirectory(project)
        self._headless_proc.readyReadStandardOutput.connect(self._on_headless_stdout)
        self._headless_proc.readyReadStandardError.connect(self._on_headless_stdout)
        self._headless_proc.finished.connect(self._on_headless_build_finished)
        build_args = [
            "--headless", "--path", project,
            "--script", "res://addons/ue2godot/entry_headless.gd", "--",
            "--manifest", manifest_path,
            "--report", report_path,
        ]
        self.log.emit(f"[cmd] {godot} {' '.join(build_args)}")
        self._headless_proc.start(godot, build_args)

    def _on_headless_build_finished(self, exit_code: int, _exit_status) -> None:
        try:
            self._headless_proc.finished.disconnect(self._on_headless_build_finished)
        except (RuntimeError, TypeError):
            pass

        project = self._headless_plan.get("_project_path", "")
        report_path = Path(project) / "ue2godot_build_report.json"
        self.log.emit(f"[cmd] construction headless terminée, code de retour {exit_code}")

        report: dict[str, Any] = {}
        if report_path.is_file():
            report = read_json(report_path) or {}

        self.last_build_report = report

        if report:
            declared = report.get("declared", {})
            stats = report.get("stats", {})
            self.log.emit(f"[build] statut : {report.get('status', '?')}")
            if declared:
                self.log.emit(
                    "[build] déclaré par le manifeste — "
                    + ", ".join(f"{k}={v}" for k, v in declared.items())
                )
            if stats:
                self.log.emit(
                    "[build] réellement construit — "
                    + ", ".join(f"{k}={v}" for k, v in stats.items())
                )
            self.log.emit(f"[build] nœuds dans la scène : {report.get('built_nodes', 0)}")
            for warning in report.get("warnings", []):
                self.log.emit(f"[build] ⚠ {warning}")
            for error in report.get("errors", []):
                self.log.emit(f"[build] ✕ {error}")

        report_status = report.get("status", "UNKNOWN")
        output_scene = report.get("output_scene", "")
        scene_exists = bool(output_scene) and (Path(project) / output_scene.replace("res://", "")).is_file()

        if exit_code != 0 or report_status not in ("OK", "OK_WITH_WARNINGS") or not scene_exists:
            reasons = []
            if exit_code != 0:
                reasons.append(f"code de sortie Godot {exit_code}")
            if report_status != "OK":
                reasons.append(f"rapport de construction : {report_status}")
                for err in report.get("errors", []):
                    reasons.append(err)
            if output_scene and not scene_exists:
                reasons.append(f"le fichier de scène déclaré ({output_scene}) est introuvable sur disque")
            if not report:
                reasons.append(f"aucun rapport lu à {report_path}")
            self.failed.emit("Construction headless échouée — " + " ; ".join(reasons))
            return

        stats = report.get("stats", {})
        self.step_changed.emit("done")
        self.finished.emit({
            "built": True, "project": project,
            "output_scene": output_scene, "stats": stats,
        })
