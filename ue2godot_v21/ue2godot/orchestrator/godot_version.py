# -*- coding: utf-8 -*-
"""
Résolution de l'exécutable Godot — en accord avec la version du projet cible.

PROBLÈME RÉEL
-------------
La recherche précédente prenait le PREMIER binaire Godot trouvé (PATH, puis
dossiers d'installation, puis n'importe quel `Godot*.exe` dans le dossier
Téléchargements). Rien ne garantissait qu'il s'agisse de la version avec
laquelle le projet cible a été créé. Ouvrir un projet Godot 4.3 avec un
binaire 4.7 déclenche une migration de projet silencieuse ; l'inverse
(projet 4.7 ouvert en 4.3) échoue ou réimporte tout de travers. Dans les
deux cas le symptôme apparaît bien plus loin, sous une forme qui n'évoque
jamais « mauvaise version de moteur ».

`project.godot` déclare la version qui l'a créé via `config/features`
(ex: PackedStringArray("4.7", "Forward Plus")). On lit cette valeur, et on
choisit un binaire dont la version CORRESPOND — en préférant une
correspondance mineure exacte (4.7), puis majeure (4.x), et seulement en
dernier recours n'importe quel binaire, avec un avertissement explicite.
"""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

# (major, minor) extraits d'un nom de fichier de release officielle :
# Godot_v4.7.2-stable_win64.exe, Godot_v4.3-stable_linux.x86_64, …
_FILENAME_VERSION = re.compile(r"[Gg]odot[_-]v?(\d+)\.(\d+)")
# config/features=PackedStringArray("4.7", "Forward Plus")
_FEATURE_VERSION = re.compile(r'"(\d+)\.(\d+)"')


@dataclass
class GodotCandidate:
    path: str
    version: Optional[Tuple[int, int]] = None
    source: str = ""

    def label(self) -> str:
        version = f"{self.version[0]}.{self.version[1]}" if self.version else "version inconnue"
        return f"{Path(self.path).name} ({version})"


@dataclass
class GodotResolution:
    """Résultat complet : le binaire retenu, pourquoi, et ce qui a été écarté."""
    executable: Optional[str] = None
    executable_version: Optional[Tuple[int, int]] = None
    project_version: Optional[Tuple[int, int]] = None
    match_quality: str = "none"  # exact | major | mismatch | unknown | none
    candidates: List[GodotCandidate] = field(default_factory=list)
    trace: List[str] = field(default_factory=list)

    @property
    def is_version_safe(self) -> bool:
        return self.match_quality in ("exact", "major", "unknown_project")

    def summary(self) -> str:
        if not self.executable:
            return "Aucun exécutable Godot trouvé."
        proj = (f"{self.project_version[0]}.{self.project_version[1]}"
                if self.project_version else "inconnue")
        exe = (f"{self.executable_version[0]}.{self.executable_version[1]}"
               if self.executable_version else "inconnue")
        if self.match_quality == "exact":
            return f"Godot {exe} — correspond à la version du projet ({proj})."
        if self.match_quality == "major":
            return (f"Godot {exe} — même version majeure que le projet ({proj}), "
                    "mineure différente.")
        if self.match_quality == "unknown_project":
            return (f"Godot {exe} — la version du projet n'a pas pu être lue, "
                    "aucune vérification possible.")
        if self.match_quality == "unknown_exe":
            return (f"Binaire retenu sans version identifiable — projet en {proj}. "
                    "Vérifiez qu'il s'agit du bon moteur.")
        return (f"ATTENTION : Godot {exe} retenu alors que le projet déclare {proj}. "
                "Ouvrir un projet avec une version différente peut le migrer "
                "irréversiblement ou casser les imports.")


def read_project_version(godot_project_root: str) -> Optional[Tuple[int, int]]:
    """Lit la version déclarée par project.godot (config/features)."""
    if not godot_project_root:
        return None
    project_file = Path(godot_project_root) / "project.godot"
    if not project_file.is_file():
        return None
    try:
        text = project_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("config/features"):
            continue
        match = _FEATURE_VERSION.search(stripped)
        if match:
            return int(match.group(1)), int(match.group(2))

    # Repli : les projets Godot 3 n'ont pas config/features mais déclarent
    # config_version=4 (Godot 3.x). On ne devine pas la mineure.
    for line in text.splitlines():
        if line.strip().startswith("config_version="):
            try:
                config_version = int(line.split("=", 1)[1].strip())
            except ValueError:
                continue
            if config_version <= 4:
                return (3, 0)
    return None


def version_from_filename(path: str) -> Optional[Tuple[int, int]]:
    match = _FILENAME_VERSION.search(Path(path).name)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def version_from_binary(path: str, timeout: float = 8.0) -> Optional[Tuple[int, int]]:
    """Interroge le binaire (`--version`). Plus fiable qu'un nom de fichier,
    qui peut avoir été renommé — mais plus lent, donc réservé aux binaires
    dont le nom ne dit rien."""
    try:
        completed = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (completed.stdout or "") + (completed.stderr or "")
    match = re.search(r"(\d+)\.(\d+)", output)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def discover_candidates(extra_dirs: Optional[List[str]] = None,
                        probe_unknown: bool = True) -> List[GodotCandidate]:
    """Tous les binaires Godot plausibles de la machine, avec leur version."""
    candidates: List[GodotCandidate] = []
    seen: set = set()

    def add(path: str, source: str) -> None:
        resolved = os.path.normcase(os.path.abspath(path))
        if resolved in seen or not os.path.isfile(path):
            return
        seen.add(resolved)
        candidates.append(GodotCandidate(
            path=path, version=version_from_filename(path), source=source,
        ))

    for name in ("godot", "godot4", "Godot"):
        found = shutil.which(name)
        if found:
            add(found, "PATH")

    search_dirs: List[str] = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    home = Path.home()
    search_dirs += [
        str(home / "Downloads"), str(home / "Téléchargements"), str(home / "Desktop"),
        str(home / "Bureau"), str(home / "Documents"),
        r"C:\Program Files\Godot", r"C:\Program Files (x86)\Godot",
        "/usr/local/bin", "/usr/bin", "/opt/godot",
    ]
    if extra_dirs:
        search_dirs += [d for d in extra_dirs if d]

    for directory in dict.fromkeys(search_dirs):
        dir_path = Path(directory)
        if not dir_path.is_dir():
            continue
        try:
            matches = list(dir_path.glob("[Gg]odot*"))
        except OSError:
            continue
        for match in matches:
            if match.is_file() and (os.access(match, os.X_OK) or match.suffix.lower() == ".exe"):
                add(str(match), str(dir_path))

    # Les binaires dont le nom ne révèle pas la version sont interrogés —
    # sinon un Godot renommé « godot.exe » resterait éternellement
    # « version inconnue » et ne pourrait jamais correspondre au projet.
    if probe_unknown:
        for candidate in candidates:
            if candidate.version is None:
                candidate.version = version_from_binary(candidate.path)

    return candidates


def resolve_godot(godot_project_root: str = "",
                  configured_executable: str = "",
                  extra_dirs: Optional[List[str]] = None) -> GodotResolution:
    """Choisit le binaire Godot le plus adapté au projet cible.

    Un chemin explicitement configuré par l'utilisateur est TOUJOURS
    respecté — on ne le remplace jamais d'autorité — mais sa version est
    quand même comparée à celle du projet, et un désaccord est signalé.
    """
    resolution = GodotResolution()
    resolution.project_version = read_project_version(godot_project_root)

    if resolution.project_version:
        resolution.trace.append(
            f"Version déclarée par project.godot : "
            f"{resolution.project_version[0]}.{resolution.project_version[1]}"
        )
    else:
        resolution.trace.append(
            "project.godot n'a pas pu être lu ou ne déclare pas de version — "
            "aucune vérification de compatibilité possible."
        )

    def classify(exe_version: Optional[Tuple[int, int]]) -> str:
        if resolution.project_version is None:
            return "unknown_project"
        if exe_version is None:
            return "unknown_exe"
        if exe_version == resolution.project_version:
            return "exact"
        if exe_version[0] == resolution.project_version[0]:
            return "major"
        return "mismatch"

    # 1) Chemin configuré : prioritaire, jamais écrasé.
    if configured_executable:
        path = Path(configured_executable).expanduser()
        if path.is_file():
            version = version_from_filename(str(path)) or version_from_binary(str(path))
            resolution.executable = str(path)
            resolution.executable_version = version
            resolution.match_quality = classify(version)
            resolution.trace.append(f"Exécutable configuré retenu : {path}")
            if resolution.match_quality == "mismatch":
                resolution.trace.append(
                    "Il ne correspond PAS à la version du projet — conservé car "
                    "explicitement configuré, mais à vérifier."
                )
            return resolution
        resolution.trace.append(f"Exécutable configuré introuvable sur disque : {path}")

    # 2) Découverte, puis choix par qualité de correspondance.
    candidates = discover_candidates(extra_dirs=extra_dirs)
    resolution.candidates = candidates
    if not candidates:
        resolution.trace.append("Aucun binaire Godot trouvé sur la machine.")
        return resolution

    resolution.trace.append(
        f"{len(candidates)} binaire(s) trouvé(s) : "
        + ", ".join(c.label() for c in candidates[:8])
        + (" …" if len(candidates) > 8 else "")
    )

    ranking = {"exact": 0, "major": 1, "unknown_project": 1, "unknown_exe": 2, "mismatch": 3}
    best: Optional[GodotCandidate] = None
    best_rank = 99
    for candidate in candidates:
        quality = classify(candidate.version)
        rank = ranking.get(quality, 99)
        # À qualité égale, préférer la version la plus récente : entre deux
        # 4.7.x, le plus récent est le plus sûr.
        if rank < best_rank or (rank == best_rank and best is not None
                                and (candidate.version or (0, 0)) > (best.version or (0, 0))):
            best, best_rank = candidate, rank

    if best is None:
        return resolution

    resolution.executable = best.path
    resolution.executable_version = best.version
    resolution.match_quality = classify(best.version)
    resolution.trace.append(f"Retenu : {best.label()} (source : {best.source})")
    if resolution.match_quality == "mismatch":
        resolution.trace.append(
            "ATTENTION : aucun binaire ne correspond à la version du projet. "
            "Ouvrir le projet avec celui-ci risque de le migrer de façon "
            "irréversible. Configurez le bon exécutable dans les options."
        )
    return resolution
