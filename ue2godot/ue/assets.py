# -*- coding: utf-8 -*-
"""
Inspection des assets de mesh.

PORTÉ depuis unreal_export_manifest_v10-8.py (mesh_info,
mesh_collision_info, mesh_lod_info, mesh_nanite_info,
mesh_material_slot_info, asset_info).

POURQUOI CES DONNÉES COMPTENT CÔTÉ GODOT (ce ne sont pas des métadonnées
décoratives) :
  - slots de matériaux : l'ordre et le NOM des slots sont le seul lien
    fiable entre un matériau Unreal et une surface Godot ; sans eux, un
    mesh à plusieurs matériaux ne peut être reconstruit correctement ;
  - collision : un GLB n'emporte aucune collision. Savoir qu'un mesh a une
    collision simple (boîte/sphère/convexe) plutôt que complexe permet de
    générer le bon type de CollisionShape3D au lieu d'un trimesh coûteux
    pour tout ;
  - Nanite : un mesh Nanite peut compter des millions de triangles que
    Godot ne saura pas afficher ; le signaler AVANT l'export évite de
    découvrir le problème après vingt minutes d'import ;
  - bounds : permettent de détecter un mesh à l'échelle aberrante (erreur
    d'unités à l'import) sans ouvrir la scène.

GÉNÉRALISATIONS au-delà de la map de référence :
  - SkeletalMesh traité par les mêmes fonctions que StaticMesh quand l'API
    le permet (la référence ne gérait que StaticMesh) ;
  - sockets capturés : un pack qui place ses accessoires par socket
    perdrait sinon toute son information de montage ;
  - nombre de LOD ET triangles par LOD, pas seulement le LOD 0.
"""

from typing import Any, Dict, List, Optional

from ue2godot.core.safe import safe_call, safe_property, safe_int, safe_float
from ue2godot.core.ids import object_path, object_name, class_name

try:
    import unreal
except ImportError:
    unreal = None


def asset_info(asset: Any) -> Optional[Dict[str, Any]]:
    if asset is None:
        return None
    return {
        "path": object_path(asset),
        "name": object_name(asset),
        "class": class_name(asset),
    }


def mesh_material_slot_info(mesh: Any) -> List[Dict[str, Any]]:
    """Slots de matériaux : index, nom de slot, matériau par défaut.

    Le NOM du slot est capturé en plus de l'index : un remplacement de
    matériau au niveau composant se fait par index, mais un humain (ou un
    profil de pack) raisonne par nom.
    """
    slots: List[Dict[str, Any]] = []
    if mesh is None or unreal is None:
        return slots

    static_materials = (safe_property(mesh, "static_materials", None)
                        or safe_property(mesh, "materials", None) or [])
    for index, entry in enumerate(static_materials):
        slot: Dict[str, Any] = {"slot_index": index}
        material = None
        try:
            material = entry.get_editor_property("material_interface")
        except Exception:
            material = entry if not hasattr(entry, "get_editor_property") else None
        try:
            slot["slot_name"] = str(entry.get_editor_property("material_slot_name"))
        except Exception:
            slot["slot_name"] = ""
        slot["material"] = asset_info(material)
        slot["empty_slot"] = material is None
        slots.append(slot)
    return slots


def mesh_lod_info(mesh: Any) -> Dict[str, Any]:
    """Nombre de LOD et triangles/vertices par niveau."""
    info: Dict[str, Any] = {"lod_count": 0, "levels": []}
    if mesh is None or unreal is None:
        return info

    count = safe_call(lambda: int(mesh.get_num_lods()), None)
    if count is None:
        count = safe_int(safe_property(mesh, "lod_count", None), 0)
    info["lod_count"] = int(count or 0)

    for lod in range(info["lod_count"]):
        level: Dict[str, Any] = {"lod": lod}
        tris = safe_call(lambda l=lod: int(
            unreal.EditorStaticMeshLibrary.get_number_triangles(mesh, l)), None)
        if tris is None:
            tris = safe_call(lambda l=lod: int(
                unreal.StaticMeshEditorSubsystem.get_number_triangles(mesh, l)), None)
        verts = safe_call(lambda l=lod: int(
            unreal.EditorStaticMeshLibrary.get_number_verts(mesh, l)), None)
        if tris is not None:
            level["triangles"] = tris
        if verts is not None:
            level["vertices"] = verts
        info["levels"].append(level)
    return info


def mesh_nanite_info(mesh: Any) -> Dict[str, Any]:
    """État Nanite — signalé car Godot n'a aucun équivalent.

    Un mesh Nanite non signalé s'exporte en GLB avec sa densité complète :
    plusieurs millions de triangles pour un seul rocher, qui bloquera Godot
    à l'import. Le détecter en amont permet d'avertir plutôt que de subir.
    """
    info: Dict[str, Any] = {"enabled": False}
    if mesh is None:
        return info
    settings = safe_property(mesh, "nanite_settings", None)
    if settings is None:
        return info
    enabled = safe_property(settings, "enabled", None)
    info["enabled"] = bool(enabled) if enabled is not None else False
    for prop in ("position_precision", "keep_triangle_percent", "fallback_relative_error"):
        value = safe_property(settings, prop, None)
        if value is not None:
            info[prop] = safe_float(value, None) if not isinstance(value, int) else int(value)
    return info


def mesh_collision_info(mesh: Any) -> Dict[str, Any]:
    """Primitives de collision simples + réglage de collision complexe.

    Un GLB n'emporte AUCUNE collision : ces données sont la seule base à
    partir de laquelle un CollisionShape3D correct peut être généré côté
    Godot. Sans elles, le seul repli est un trimesh sur la géométrie de
    rendu — correct visuellement, mais coûteux et inutilisable pour un
    corps dynamique.
    """
    info: Dict[str, Any] = {
        "simple_primitive_count": 0, "boxes": 0, "spheres": 0,
        "capsules": 0, "convex": 0,
    }
    if mesh is None or unreal is None:
        return info

    body_setup = safe_property(mesh, "body_setup", None)
    if body_setup is not None:
        collision_type = safe_property(body_setup, "collision_trace_flag", None)
        if collision_type is not None:
            info["trace_flag"] = str(collision_type)
        agg = safe_property(body_setup, "agg_geom", None)
        if agg is not None:
            for prop, key in (("box_elems", "boxes"), ("sphere_elems", "spheres"),
                              ("sphyl_elems", "capsules"), ("convex_elems", "convex")):
                elems = safe_property(agg, prop, None)
                try:
                    info[key] = len(elems) if elems is not None else 0
                except Exception:
                    info[key] = 0
        info["simple_primitive_count"] = (
            info["boxes"] + info["spheres"] + info["capsules"] + info["convex"]
        )

    if info["simple_primitive_count"] == 0:
        info["note"] = (
            "Aucune collision simple : côté Godot, seul un trimesh sur la "
            "géométrie de rendu est possible (statique uniquement)."
        )
    return info


def mesh_sockets(mesh: Any) -> List[Dict[str, Any]]:
    """Sockets du mesh — points de montage nommés.

    Absents de la référence. Un pack qui attache ses accessoires par socket
    (torche dans une applique, bougie sur un socle) perd tout son montage
    si on les ignore, et les objets se retrouvent à l'origine du parent.
    """
    sockets: List[Dict[str, Any]] = []
    if mesh is None:
        return sockets
    entries = safe_property(mesh, "sockets", None) or []
    for socket in entries:
        entry: Dict[str, Any] = {}
        name = safe_property(socket, "socket_name", None)
        if name is not None:
            entry["name"] = str(name)
        for prop, key in (("relative_location", "location"),
                          ("relative_rotation", "rotation"),
                          ("relative_scale", "scale")):
            value = safe_property(socket, prop, None)
            if value is None:
                continue
            try:
                if hasattr(value, "x"):
                    entry[key] = [float(value.x), float(value.y), float(value.z)]
                else:
                    entry[key] = [float(value.roll), float(value.pitch), float(value.yaw)]
            except Exception:
                pass
        if entry:
            sockets.append(entry)
    return sockets


def mesh_bounds(mesh: Any) -> Optional[Dict[str, Any]]:
    if mesh is None:
        return None
    bounds = safe_call(lambda: mesh.get_bounds(), None)
    if bounds is None:
        return None
    try:
        origin = bounds.box_extent if hasattr(bounds, "box_extent") else None
        extent = bounds.box_extent if origin is not None else None
        sphere = safe_property(bounds, "sphere_radius", None)
        result: Dict[str, Any] = {}
        if extent is not None:
            result["extent"] = [float(extent.x), float(extent.y), float(extent.z)]
        if sphere is not None:
            result["sphere_radius"] = float(sphere)
        return result or None
    except Exception:
        return None


def mesh_info(mesh: Any, deep: bool = True) -> Optional[Dict[str, Any]]:
    """Fiche complète d'un asset de mesh.

    `deep=False` ne renvoie que l'identité : utile pour les références
    répétées (un mesh utilisé 800 fois n'a pas besoin d'être décrit 800
    fois — c'est le registre qui porte la fiche complète, une seule fois).
    """
    if mesh is None:
        return None
    info = asset_info(mesh) or {}
    if not deep:
        return info

    info["material_slots"] = mesh_material_slot_info(mesh)
    info["lod"] = mesh_lod_info(mesh)
    info["nanite"] = mesh_nanite_info(mesh)
    info["collision"] = mesh_collision_info(mesh)

    sockets = mesh_sockets(mesh)
    if sockets:
        info["sockets"] = sockets
    bounds = mesh_bounds(mesh)
    if bounds:
        info["bounds"] = bounds

    # Signal exploitable en aval plutôt qu'un simple compteur : une densité
    # extrême est la cause n°1 d'un import Godot interminable.
    levels = info["lod"].get("levels") or []
    lod0_triangles = levels[0].get("triangles") if levels else None
    if lod0_triangles and lod0_triangles > 500000:
        info["density_warning"] = (
            f"LOD0 à {lod0_triangles} triangles — l'import Godot sera très long ; "
            "envisager un export depuis un LOD supérieur."
        )
    return info
