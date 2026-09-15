# -*- coding: utf-8 -*-
"""
Chargement des profils par pack d'assets.

DÉFAUT CORRIGÉ ICI
------------------
`ResolvedConfig.resolve(defaults, profile, run_config)` attend un profil
en deuxième position depuis l'origine, mais TOUS les appelants passaient
`{}`. Les fichiers de `ue2godot/profiles/` n'étaient donc lus par
personne : les noms de paramètres de matériau de décal, les presets VFX et
les règles de catégorisation déclarés là restaient sans effet, et le
framework se rabattait silencieusement sur ses valeurs par défaut.

Le symptôme était invisible — tout « marchait », simplement avec les
mauvaises conventions pour un pack donné.

RÈGLE DE PRIORITÉ
-----------------
    defaults (livrés)  <  profil (pack)  <  run (cette map-ci)

Le run gagne toujours : un réglage choisi dans l'interface ne doit jamais
être écrasé par un fichier de profil.
"""

import json
import os
from typing import Any, Dict, List, Optional

PROFILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PROFILE = "_default"


def available_profiles() -> List[str]:
    """Noms de profils installés, `_default` en tête s'il existe."""
    names: List[str] = []
    try:
        for entry in sorted(os.listdir(PROFILES_DIR)):
            if entry.endswith(".json"):
                names.append(entry[:-5])
    except OSError:
        return []
    names.sort(key=lambda n: (n != DEFAULT_PROFILE, n))
    return names


def load_profile(name: Optional[str]) -> Dict[str, Any]:
    """Charge un profil par son nom. Renvoie un dict vide si absent.

    Un profil introuvable n'est PAS une erreur fatale : le framework doit
    rester utilisable sans profil (c'est le cas d'une map nouvelle, dont
    le pack n'a pas encore le sien). Mais l'absence doit être visible, d'où
    la clé `_profile_missing` ajoutée au résultat plutôt qu'un silence.
    """
    if not name:
        return {}

    path = os.path.join(PROFILES_DIR, f"{name}.json")
    if not os.path.isfile(path):
        return {"_profile_missing": name}

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        # Un profil malformé est plus dangereux qu'un profil absent : il
        # laisse croire que les conventions du pack sont appliquées. On le
        # signale explicitement au lieu de le traiter comme vide.
        return {"_profile_error": f"{name}: {exc}"}

    if not isinstance(data, dict):
        return {"_profile_error": f"{name}: le fichier ne contient pas un objet JSON"}

    data.setdefault("profile_name", name)
    return data


def profile_notes(profile: Dict[str, Any]) -> List[str]:
    """Avertissements à faire remonter dans le rapport d'étape."""
    notes: List[str] = []
    if profile.get("_profile_missing"):
        notes.append(
            f"Profil '{profile['_profile_missing']}' introuvable — les conventions "
            "par défaut sont utilisées (noms de paramètres de matériau, presets VFX). "
            "Si ce pack a des conventions propres, les décals risquent d'être "
            "résolus en blanc."
        )
    if profile.get("_profile_error"):
        notes.append(f"Profil illisible : {profile['_profile_error']}")
    return notes


def detect_profile_for_source(source_path: str) -> Optional[str]:
    """Devine un profil à partir du chemin source, sans jamais l'imposer.

    Le nom du projet Unreal est un indice raisonnable (un projet
    « Necropolis » utilise probablement le profil du même nom), mais ce
    n'est qu'une suggestion : la fonction rend `None` si aucun profil
    installé ne correspond, et l'appelant reste libre de l'ignorer. On ne
    charge jamais un profil « approchant » — appliquer les conventions du
    mauvais pack serait pire que de n'en appliquer aucune.
    """
    if not source_path:
        return None
    installed = {name.lower() for name in available_profiles()}
    parts = [p.lower() for p in os.path.normpath(source_path).split(os.sep) if p]
    for part in reversed(parts):
        if part in installed and part != DEFAULT_PROFILE:
            return part
    return None
