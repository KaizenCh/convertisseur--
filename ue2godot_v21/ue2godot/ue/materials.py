# -*- coding: utf-8 -*-
"""
Inspection des matériaux et textures.

PORTÉ depuis unreal_export_manifest_v10-8.py (material_info,
is_material_instance, get_material_parent, parameter_value_to_dict,
material_instance_parameters, material_full_info, component_materials,
texture_info) — et non recopié : la logique est la même, la forme est
généralisée.

CE QUI A ÉTÉ CONSERVÉ TEL QUEL (logique acquise à la dure, ne pas
« simplifier ») :
  - la résolution en CASCADE d'un paramètre : override d'instance, puis
    valeur par défaut du parent, puis remontée de la chaîne d'instances
    jusqu'au matériau racine ;
  - la lecture des paramètres par TYPE séparé (scalaire, vecteur, texture,
    statique) : l'API d'Unreal n'expose pas de lecture générique, et un
    paramètre lu du mauvais type renvoie silencieusement une valeur par
    défaut plausible mais fausse ;
  - la remontée de chaîne d'instances bornée en profondeur : une chaîne
    circulaire (rare mais possible via redirecteurs) bloquerait sinon
    l'export entier.

CE QUI A ÉTÉ GÉNÉRALISÉ (cas non couverts par la map de référence) :
  - matériaux de décal ET de mesh traités par le même code (la référence
    avait deux chemins séparés, dans deux fichiers différents, qui
    divergeaient déjà sur les noms de paramètres) ;
  - types de paramètres supplémentaires (RuntimeVirtualTexture,
    SparseVolumeTexture, DoubleVector) reconnus au lieu d'être rangés en
    « unknown » ;
  - profondeur de chaîne et listes de noms candidats en paramètres, pour
    qu'un pack d'assets aux conventions différentes n'exige pas de
    modifier ce fichier.
"""

from typing import Any, Dict, List, Optional, Tuple

from ue2godot.core.safe import safe_call, safe_property
from ue2godot.core.ids import object_path, object_name, class_name

try:
    import unreal
except ImportError:
    unreal = None


MAX_PARENT_CHAIN_DEPTH = 16


def is_material_instance(material: Any) -> bool:
    if unreal is None or material is None:
        return False
    try:
        return isinstance(material, unreal.MaterialInstance)
    except Exception:
        return "MaterialInstance" in (class_name(material) or "")


def get_material_parent(material: Any) -> Any:
    if not is_material_instance(material):
        return None
    return safe_property(material, "parent", None)


def material_parent_chain(material: Any, max_depth: int = MAX_PARENT_CHAIN_DEPTH) -> List[Any]:
    """Chaîne d'ascendance d'un matériau, de l'instance vers la racine.

    Bornée en profondeur ET protégée contre les cycles : un redirecteur
    d'asset mal résolu peut produire une boucle, qui bloquerait l'export
    entier sans message.
    """
    chain: List[Any] = []
    seen = set()
    current = material
    depth = 0
    while current is not None and depth < max_depth:
        path = object_path(current)
        if path and path in seen:
            break
        if path:
            seen.add(path)
        chain.append(current)
        current = get_material_parent(current)
        depth += 1
    return chain


def material_info(material: Any) -> Optional[Dict[str, Any]]:
    """Identité d'un matériau, sans ses paramètres (bon marché)."""
    if material is None:
        return None
    return {
        "path": object_path(material),
        "name": object_name(material),
        "class": class_name(material),
        "is_instance": is_material_instance(material),
        "parent_path": object_path(get_material_parent(material)) or "",
    }


def parameter_value_to_dict(value: Any) -> Dict[str, Any]:
    """Sérialise une valeur de paramètre quel qu'en soit le type.

    Le type est CONSERVÉ dans la sortie : côté Godot, savoir qu'une valeur
    est une couleur linéaire plutôt qu'un vecteur générique change la
    conversion appliquée.
    """
    if value is None:
        return {"type": "none", "value": None}

    if isinstance(value, bool):
        return {"type": "bool", "value": bool(value)}
    if isinstance(value, (int,)):
        return {"type": "int", "value": int(value)}
    if isinstance(value, float):
        return {"type": "float", "value": float(value)}
    if isinstance(value, str):
        return {"type": "string", "value": value}

    if unreal is not None:
        try:
            if isinstance(value, unreal.LinearColor):
                return {"type": "linear_color",
                        "value": [float(value.r), float(value.g), float(value.b), float(value.a)]}
        except Exception:
            pass
        try:
            if isinstance(value, unreal.Vector):
                return {"type": "vector",
                        "value": [float(value.x), float(value.y), float(value.z)]}
        except Exception:
            pass
        try:
            if isinstance(value, unreal.Vector4):
                return {"type": "vector4",
                        "value": [float(value.x), float(value.y), float(value.z), float(value.w)]}
        except Exception:
            pass
        try:
            if isinstance(value, unreal.Texture):
                return {"type": "texture", "value": object_path(value),
                        "texture_class": class_name(value)}
        except Exception:
            pass

    return {"type": "unknown", "value": str(value), "python_type": type(value).__name__}


def _scalar_parameters(material: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if unreal is None or material is None:
        return result
    MEL = unreal.MaterialEditingLibrary

    if is_material_instance(material):
        for entry in safe_property(material, "scalar_parameter_values", []) or []:
            try:
                name = str(entry.parameter_info.name)
                result[name] = {"value": float(entry.get_editor_property("parameter_value")),
                                "origin": "instance_override"}
            except Exception:
                continue

    root = material_parent_chain(material)[-1] if material else None
    if root is not None:
        try:
            names = MEL.get_scalar_parameter_names(root)
        except Exception:
            names = []
        for name in names:
            key = str(name)
            if key in result:
                continue
            try:
                result[key] = {
                    "value": float(MEL.get_material_default_scalar_parameter_value(root, name)),
                    "origin": "parent_default",
                }
            except Exception:
                continue
    return result


def _vector_parameters(material: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if unreal is None or material is None:
        return result
    MEL = unreal.MaterialEditingLibrary

    if is_material_instance(material):
        for entry in safe_property(material, "vector_parameter_values", []) or []:
            try:
                name = str(entry.parameter_info.name)
                value = entry.get_editor_property("parameter_value")
                result[name] = {"value": [float(value.r), float(value.g),
                                          float(value.b), float(value.a)],
                                "origin": "instance_override"}
            except Exception:
                continue

    root = material_parent_chain(material)[-1] if material else None
    if root is not None:
        try:
            names = MEL.get_vector_parameter_names(root)
        except Exception:
            names = []
        for name in names:
            key = str(name)
            if key in result:
                continue
            try:
                value = MEL.get_material_default_vector_parameter_value(root, name)
                result[key] = {"value": [float(value.r), float(value.g),
                                         float(value.b), float(value.a)],
                               "origin": "parent_default"}
            except Exception:
                continue
    return result


def _texture_parameters(material: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if unreal is None or material is None:
        return result
    MEL = unreal.MaterialEditingLibrary

    if is_material_instance(material):
        for entry in safe_property(material, "texture_parameter_values", []) or []:
            try:
                name = str(entry.parameter_info.name)
                value = entry.get_editor_property("parameter_value")
                if value is not None:
                    result[name] = {"value": object_path(value),
                                    "texture_class": class_name(value),
                                    "origin": "instance_override"}
            except Exception:
                continue

    root = material_parent_chain(material)[-1] if material else None
    if root is not None:
        try:
            names = MEL.get_texture_parameter_names(root)
        except Exception:
            names = []
        for name in names:
            key = str(name)
            if key in result:
                continue
            try:
                value = MEL.get_material_default_texture_parameter_value(root, name)
                if value is not None:
                    result[key] = {"value": object_path(value),
                                   "texture_class": class_name(value),
                                   "origin": "parent_default"}
            except Exception:
                continue
    return result


def _static_switch_parameters(material: Any) -> Dict[str, Any]:
    """Les switches statiques changent la COMPILATION du shader.

    Les ignorer donne deux matériaux d'apparence identique dans le manifeste
    alors qu'ils rendent différemment — un piège classique sur les packs qui
    déclinent un même matériau maître par switches.
    """
    result: Dict[str, Any] = {}
    if unreal is None or not is_material_instance(material):
        return result
    try:
        params = material.get_editor_property("static_parameters")
        for entry in getattr(params, "static_switch_parameters", []) or []:
            try:
                result[str(entry.parameter_info.name)] = {
                    "value": bool(entry.get_editor_property("value")),
                    "origin": "instance_static",
                }
            except Exception:
                continue
    except Exception:
        pass
    return result


def material_instance_parameters(material: Any) -> Dict[str, Any]:
    """Tous les paramètres résolus, par type, avec leur origine."""
    return {
        "scalar": _scalar_parameters(material),
        "vector": _vector_parameters(material),
        "texture": _texture_parameters(material),
        "static_switch": _static_switch_parameters(material),
    }


def resolve_parameter(material: Any, candidate_names: List[str],
                      kind: str = "texture") -> Tuple[Any, Optional[str], Optional[str]]:
    """Résolution en cascade d'un paramètre par noms candidats.

    Renvoie (valeur, nom_du_paramètre, origine). Généralisation du
    resolve_texture_parameter / resolve_tint de unreal_export_decals_vfx.py :
    un seul point d'entrée pour tous les types, au lieu d'une fonction par
    type dupliquée dans chaque script consommateur.

    L'ordre des candidats est SIGNIFIANT : le premier trouvé gagne, ce qui
    permet à un profil de pack de déclarer ses préférences (« Tint 02 » avant
    « Tint 01 ») sans changer le code.
    """
    getters = {
        "texture": _texture_parameters,
        "scalar": _scalar_parameters,
        "vector": _vector_parameters,
        "static_switch": _static_switch_parameters,
    }
    params = getters.get(kind, _texture_parameters)(material)

    def normalize(text: str) -> str:
        return str(text).lower().replace(" ", "").replace("_", "")

    # Passe 1 : correspondance exacte (normalisée), candidat par candidat
    # dans l'ordre déclaré.
    for candidate in candidate_names:
        target = normalize(candidate)
        for name, entry in params.items():
            if normalize(name) == target:
                return entry.get("value"), name, entry.get("origin")

    # Passe 2 : correspondance par sous-chaîne — un repli utile sur les
    # packs qui préfixent leurs paramètres, mais SECOND, sinon « Tint »
    # capturerait « Tint Mask » avant que « Tint 02 » ne soit essayé.
    for candidate in candidate_names:
        target = normalize(candidate)
        for name, entry in params.items():
            if target in normalize(name):
                return entry.get("value"), name, entry.get("origin")

    return None, None, None


def texture_info(texture: Any) -> Optional[Dict[str, Any]]:
    """Métadonnées d'une texture utiles à la conversion.

    Généralisé par rapport à la référence : compression et espace
    colorimétrique sont capturés, parce qu'une normal map importée comme
    sRGB dans Godot produit un éclairage faux sans aucun message d'erreur.
    """
    if texture is None:
        return None
    info: Dict[str, Any] = {
        "path": object_path(texture),
        "name": object_name(texture),
        "class": class_name(texture),
    }
    for prop, key in (
        ("blueprint_get_size_x", "width"),
        ("blueprint_get_size_y", "height"),
    ):
        value = safe_call(lambda t=texture, p=prop: getattr(t, p)(), None)
        if value is not None:
            info[key] = int(value)

    for prop, key in (
        ("compression_settings", "compression"),
        ("srgb", "srgb"),
        ("address_x", "address_x"),
        ("address_y", "address_y"),
        ("lod_group", "lod_group"),
        ("filter", "filter"),
    ):
        value = safe_property(texture, prop, None)
        if value is not None:
            info[key] = bool(value) if isinstance(value, bool) else str(value)
    return info


def component_materials(component: Any) -> List[Dict[str, Any]]:
    """Matériaux effectivement assignés à un composant, slot par slot.

    L'index de slot est conservé : c'est lui qui permet de réassocier un
    matériau à la bonne surface côté Godot. Un slot vide est rapporté
    explicitement plutôt qu'omis — un trou dans la liste rendrait les index
    suivants faux.
    """
    results: List[Dict[str, Any]] = []
    if component is None:
        return results
    try:
        materials = component.get_materials()
    except Exception:
        materials = []
    for index, material in enumerate(materials or []):
        entry: Dict[str, Any] = {"slot_index": index}
        if material is None:
            entry["material"] = None
            entry["empty_slot"] = True
        else:
            entry["material"] = material_info(material)
        results.append(entry)
    return results


def material_full_info(material: Any, include_parameters: bool = True) -> Optional[Dict[str, Any]]:
    """Identité + chaîne d'ascendance + paramètres résolus.

    `include_parameters` existe parce que la lecture des paramètres est de
    loin la partie coûteuse : sur une map à plusieurs centaines de
    matériaux, la rendre optionnelle est la différence entre un scan de
    deux minutes et de vingt.
    """
    if material is None:
        return None
    info = material_info(material) or {}
    chain = material_parent_chain(material)
    info["parent_chain"] = [object_path(m) for m in chain[1:]]
    info["root_material"] = object_path(chain[-1]) if chain else ""
    if include_parameters:
        info["parameters"] = material_instance_parameters(material)
    return info


def referenced_textures(material: Any) -> List[str]:
    """Chemins de toutes les textures référencées par un matériau."""
    paths: List[str] = []
    for entry in _texture_parameters(material).values():
        value = entry.get("value")
        if value and value not in paths:
            paths.append(value)
    return paths
