# -*- coding: utf-8 -*-
"""
Garde-fou anti-manifeste-vide.

LE PROBLÈME QU'IL RÉSOUT, VÉCU EN CONDITIONS RÉELLES
-----------------------------------------------------
Le scanner réécrit `level_manifest_v10.json` INTÉGRALEMENT à chaque
exécution. Si la mauvaise map est chargée dans l'éditeur au moment du scan
— cas observé : ouverture du `.uproject` sans ouvrir explicitement la map
cible, l'éditeur restaure alors une map par défaut — le scanner tourne
quand même, ne trouve qu'une poignée d'acteurs, et écrit un manifeste de
quelques kilo-octets qui a l'air valide en surface mais ne décrit rien.

Le manifeste correct précédent est alors ÉCRASÉ. C'est le pire mode
d'échec possible : silencieux, destructif, et le symptôme (scène Godot
quasi vide) n'évoque à aucun moment « mauvaise map chargée ».

DEUX GARDE-FOUS, DANS CET ORDRE
--------------------------------
  1. `ensure_target_map_loaded()` — charge explicitement la map voulue si
     elle n'est pas déjà active, et REVÉRIFIE après coup : ne jamais se
     fier à l'absence d'exception seule, un `load_level` peut réussir sans
     rien charger si le chemin package est mal orthographié.
  2. `validate_scan_readiness()` — refuse de continuer si le compte
     d'acteurs est invraisemblable, en levant une exception plutôt qu'en
     émettant un avertissement. Un avertissement n'empêche pas l'écriture ;
     seule une exception le fait.

PHILOSOPHIE, alignée sur le reste du pipeline
----------------------------------------------
  - ne jamais avaler une exception en silence ;
  - API moderne, puis UN repli documenté — jamais une troisième tentative
    « au petit bonheur » ;
  - échouer bruyamment plutôt que produire un manifeste qui a l'air correct ;
  - ce qui n'est pas strictement nécessaire à la décision « on continue ou
    pas » (streaming World Partition) est best-effort et ne bloque jamais.

Basé sur manifest_scan_guard.py, adapté au framework : les paramètres
viennent de la configuration au lieu d'être en dur, et le résultat est
rendu sous forme de données exploitables par le rapport d'étape plutôt
qu'imprimé.
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional

try:
    import unreal
except ImportError:
    unreal = None


class ScanGuardError(RuntimeError):
    """Le scan doit être abandonné AVANT toute écriture."""


@dataclass
class GuardResult:
    actor_count: int = 0
    current_map: str = ""
    target_map: str = ""
    map_was_loaded: bool = False
    world_partition_note: str = ""
    notes: List[str] = field(default_factory=list)


def _get_editor_world() -> Any:
    """Monde éditeur actif, via l'API moderne puis le repli historique."""
    if unreal is None:
        raise ScanGuardError("API Unreal indisponible.")

    try:
        subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        world = subsystem.get_editor_world()
        if world is not None:
            return world
    except Exception:
        pass

    try:
        world = unreal.EditorLevelLibrary.get_editor_world()
        if world is not None:
            return world
    except Exception:
        pass

    raise ScanGuardError(
        "Impossible de récupérer le monde éditeur actif, ni via "
        "UnrealEditorSubsystem ni via EditorLevelLibrary. "
        "L'éditeur est-il lancé avec un niveau chargé ?"
    )


def current_map_name() -> str:
    world = _get_editor_world()
    try:
        return str(world.get_path_name())
    except Exception:
        return str(world)


def ensure_target_map_loaded(target_map: str, force_reload: bool = False,
                             notes: Optional[List[str]] = None) -> bool:
    """Charge `target_map` si elle n'est pas déjà active. Renvoie True si
    un chargement a réellement eu lieu.

    `target_map` est un chemin PACKAGE Unreal (`/Game/Maps/m1`), pas un
    chemin disque — c'est ce que donne « Copy Reference » dans le Content
    Browser, sans le suffixe `.m1` final.

    `force_reload` est False par défaut : recharger un niveau perd les
    modifications non sauvegardées, ce qu'on ne fait jamais d'autorité.
    """
    notes = notes if notes is not None else []
    current = current_map_name()

    if not force_reload and target_map and target_map in current:
        notes.append(f"Map cible déjà active : {current}")
        return False

    notes.append(
        f"Map active '{current}' différente de la cible '{target_map}' — "
        "chargement forcé."
    )

    loaded = False
    try:
        subsystem = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        subsystem.load_level(target_map)
        loaded = True
    except Exception as exc:
        notes.append(f"LevelEditorSubsystem.load_level a échoué ({exc}) — repli.")

    if not loaded:
        try:
            unreal.EditorLevelLibrary.load_level(target_map)
            loaded = True
        except Exception as exc:
            raise ScanGuardError(
                f"Impossible de charger la map cible '{target_map}' : "
                f"LevelEditorSubsystem ET EditorLevelLibrary ont échoué ({exc}). "
                "Vérifiez que le chemin package est exact (casse comprise) et "
                "qu'aucun suffixe '.NomDeMap' ne traîne à la fin."
            )

    # Ne jamais se fier à l'absence d'exception : on revérifie l'état réel.
    # Un load_level sur un chemin mal orthographié peut ne rien charger sans
    # rien signaler.
    new_current = current_map_name()
    if target_map and target_map not in new_current:
        raise ScanGuardError(
            f"load_level('{target_map}') n'a levé aucune exception, mais le "
            f"niveau actif est '{new_current}', pas la cible. Le chemin "
            "package est probablement incorrect."
        )

    notes.append(f"Map cible chargée : {new_current}")
    return True


def try_load_world_partition_cells(notes: Optional[List[str]] = None) -> str:
    """Force le streaming des cellules World Partition — best-effort.

    Ne bloque JAMAIS : une version d'Unreal sans cette API, ou un niveau
    qui n'est pas World Partition, ne sont pas des erreurs. Mais l'issue
    est rapportée, parce qu'un scan incomplet sur une map World Partition
    est justement le cas où le compte d'acteurs sera trompeur.
    """
    notes = notes if notes is not None else []
    try:
        subsystem = unreal.get_editor_subsystem(unreal.WorldPartitionSubsystem)
    except Exception:
        message = ("WorldPartitionSubsystem indisponible (version d'Unreal "
                   "différente, ou niveau non World Partition) — ignoré.")
        notes.append(message)
        return message

    try:
        for method_name in ("load_all_cells", "load_all_streaming_cells"):
            method = getattr(subsystem, method_name, None)
            if method is not None:
                method()
                message = f"World Partition : {method_name}() appelé."
                notes.append(message)
                return message
        message = ("WorldPartitionSubsystem présent mais aucune méthode de "
                   "chargement connue sur cette version. Si la map utilise "
                   "World Partition, vérifiez manuellement que les cellules "
                   "sont chargées.")
        notes.append(message)
        return message
    except Exception as exc:
        message = (f"Chargement des cellules World Partition en échec ({exc}) — "
                   "le scan continue, surveillez le compte d'acteurs.")
        notes.append(message)
        return message


def validate_scan_readiness(min_actors: int = 20,
                            expected_map_substring: str = "",
                            notes: Optional[List[str]] = None) -> int:
    """Refuse de scanner un niveau invraisemblable. Renvoie le compte.

    Lève une exception, pas un avertissement : un avertissement n'empêche
    pas l'écriture, et c'est précisément l'écriture qu'il faut empêcher —
    un manifeste vide écrase le manifeste correct précédent.
    """
    notes = notes if notes is not None else []
    current = current_map_name()

    if expected_map_substring and expected_map_substring not in current:
        raise ScanGuardError(
            f"Map active '{current}' : ne contient pas '{expected_map_substring}'. "
            "Abandon avant scan pour éviter d'écrire un manifeste vide."
        )

    try:
        subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    except Exception as exc:
        raise ScanGuardError(
            f"EditorActorSubsystem indisponible ({exc}). L'éditeur tourne-t-il "
            "en mode interactif normal ?"
        )

    count = len(subsystem.get_all_level_actors())
    notes.append(f"Niveau '{current}' — {count} acteur(s) détecté(s).")

    if count < min_actors:
        raise ScanGuardError(
            f"Seulement {count} acteur(s) sur le niveau '{current}' "
            f"(seuil minimum : {min_actors}). C'est la signature typique d'un "
            "niveau par défaut chargé par erreur plutôt que la map cible. "
            "Scan abandonné — AUCUN manifeste n'est écrit, pour ne pas "
            "écraser un manifeste correct existant par un manifeste vide."
        )

    return count


def guard_before_scan(target_map: str = "",
                      min_actors: int = 20,
                      force_reload: bool = False,
                      check_world_partition: bool = True,
                      enforce_map: bool = True) -> GuardResult:
    """Point d'entrée unique, à appeler AVANT tout scan d'acteurs.

    `target_map` vide désactive le contrôle de map mais PAS le seuil
    d'acteurs : on ne connaît pas toujours le chemin package, alors qu'un
    niveau quasi vide reste suspect dans tous les cas.
    """
    result = GuardResult(target_map=target_map)

    if target_map and enforce_map:
        result.map_was_loaded = ensure_target_map_loaded(
            target_map, force_reload=force_reload, notes=result.notes)

    if check_world_partition:
        result.world_partition_note = try_load_world_partition_cells(result.notes)

    result.actor_count = validate_scan_readiness(
        min_actors=min_actors,
        expected_map_substring=target_map if enforce_map else "",
        notes=result.notes,
    )
    result.current_map = current_map_name()
    return result


def package_path_from_filesystem(map_path: str) -> str:
    """Devine le chemin package `/Game/...` depuis un chemin disque `.umap`.

    Même convention `<Projet>/Content/...` que partout ailleurs dans le
    framework. Renvoie une chaîne vide si le chemin ne suit pas cette
    convention — on préfère désactiver le contrôle de map plutôt que de
    fabriquer un chemin package plausible mais faux, qui déclencherait un
    chargement de la mauvaise map.
    """
    if not map_path:
        return ""
    normalized = str(map_path).replace("\\", "/")
    marker = "/content/"
    index = normalized.lower().find(marker)
    if index == -1:
        return ""
    rest = normalized[index + len(marker):]
    if rest.lower().endswith(".umap"):
        rest = rest[:-5]
    else:
        return ""
    return "/Game/" + rest if rest else ""
