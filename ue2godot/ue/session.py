# -*- coding: utf-8 -*-
"""
Découverte d'acteurs — implémentation UNIQUE.

Méthode V4/V5/V6 éprouvée (ObjectIterator + filtrage par niveau) :
NE PAS REMPLACER par world.get_current_level(). Elle a survécu à dix
versions majeures du scanner de référence et reste la seule qui voie de
façon fiable les acteurs situés dans le sous-niveau chargé d'une
LevelInstance.

ATTENTION HISTORIQUE — ce fichier a été orphelin.
Une seconde implémentation de la même chose vivait en parallèle dans
step1_manifest.py, et c'est ELLE qui tournait ; modifier celle-ci n'aurait
rien changé au manifeste produit. C'est exactement le piège déjà rencontré
avec ue/classify.py. Les deux sont désormais fusionnées ici, et
step1_manifest importe d'ici : il n'existe plus qu'un seul endroit où
corriger la découverte.

Une TROISIÈME variante (`discover_level_actors`) a existé ici, fusionnant
le sous-système et ObjectIterator. Elle a été supprimée et non conservée
« au cas où » : elle ramenait des acteurs d'autres niveaux que celui
demandé, ce qui n'est pas le comportement validé, et une fonction inutilisée
au nom plausible est exactement ce qu'un futur lecteur appellerait par
erreur.
"""

from typing import List, Any
from ue2godot.core.ids import object_path

try:
    import unreal
except ImportError:
    unreal = None


def enumerate_level_actors(level: Any) -> List[Any]:
    """Tous les acteurs appartenant à un niveau donné.

    C'est la brique utilisée pour descendre dans le sous-niveau chargé
    d'une LevelInstance. Le filtrage se fait sur `actor.get_level()` et
    non sur une API de niveau courant : un acteur d'un sous-niveau n'est
    pas dans le niveau courant, et serait donc invisible autrement — la
    cause exacte d'un manifeste ne déclarant qu'une poignée de placements
    sur une map dont le contenu vit dans des LevelInstances.
    """
    result: List[Any] = []
    if unreal is None or level is None:
        return result
    try:
        for actor in unreal.ObjectIterator(unreal.Actor):
            try:
                if actor.get_level() == level:
                    result.append(actor)
            except Exception:
                continue
    except Exception:
        return result
    return result
