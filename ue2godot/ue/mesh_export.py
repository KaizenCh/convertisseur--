# -*- coding: utf-8 -*-
"""
Export d'un asset de mesh — trois voies réellement différentes.

POURQUOI PLUSIEURS VOIES
------------------------
`GLTFExporter` est un plugin. Il peut être désactivé dans le projet,
absent d'une installation source, retiré ou renommé dans une version
future du moteur, ou échouer sur un asset précis (Nanite très dense,
matériau au graphe exotique) alors qu'il fonctionne sur tous les autres.
Chacun de ces cas transformait l'export entier en échec sans recours.

LES TROIS VOIES, ET CE QU'ELLES COÛTENT
----------------------------------------
1. **GLTFExporter** (principale) — géométrie + matériaux + textures dans
   un seul GLB. C'est la seule qui préserve l'apparence.

2. **AssetExportTask + exporteur OBJ** — passe par le système d'export
   générique du moteur, indépendant du plugin glTF. Godot 4 lit l'OBJ
   nativement. On perd les matériaux (l'OBJ ne porte qu'un renvoi .mtl
   que Godot n'interprète pas comme un matériau Unreal) : la géométrie
   et les UV survivent, l'apparence non. Qualité déclarée "reduced", et
   le manifeste porte déjà les slots de matériaux pour les réassigner.

3. **Reconstruction manuelle en GLB** — on lit les sections du mesh via
   l'API de géométrie d'Unreal et on écrit le GLB nous-mêmes avec
   `core/glb.py`, le même writer que celui qui produit déjà le terrain.
   Aucune dépendance à un exporteur du moteur. C'est la voie de dernier
   recours, mais elle est entièrement sous notre contrôle : si elle
   échoue, c'est notre code, pas une boîte noire.

CE QUI N'EST PAS UN REPLI ICI
------------------------------
Il n'y a volontairement aucune stratégie qui « génère un cube de
remplacement » ou renvoie un mesh vide en cas d'échec total. Un
placeholder de la bonne taille au bon endroit est exactement le genre de
résultat qui passe inaperçu pendant des semaines. Un mesh qui ne peut
pas être exporté doit rester manquant et compté comme tel.
"""

import os
from typing import Any, Dict, List, Optional, Tuple

from ue2godot.core.strategies import Strategy, StrategyChain
from ue2godot.core.glb import write_glb

try:
    import unreal
except ImportError:
    unreal = None


def _validate_exported_file(path: Optional[str]) -> Tuple[bool, str]:
    """Validation commune à toutes les voies.

    Reprend la leçon de l'exporteur de référence : un fichier de 0 octet
    ou tronqué était compté comme un succès. On vérifie l'existence, une
    taille plancher, et — pour le GLB — la signature binaire réelle.
    """
    if not path:
        return False, "aucun chemin de sortie rendu"
    if not os.path.isfile(path):
        return False, "le fichier n'a pas été créé"

    size = os.path.getsize(path)
    if size < 20:
        return False, f"fichier suspect ({size} octets)"

    if path.lower().endswith(".glb"):
        try:
            with open(path, "rb") as handle:
                magic = handle.read(4)
        except OSError as exc:
            return False, f"relecture impossible : {exc}"
        if magic != b"glTF":
            return False, f"en-tête glTF invalide ({magic!r})"

    if path.lower().endswith(".obj"):
        # Un OBJ sans une seule ligne de sommet est vide quel que soit son
        # poids (il peut ne contenir que des commentaires d'en-tête).
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                head = handle.read(65536)
        except OSError as exc:
            return False, f"relecture impossible : {exc}"
        if "\nv " not in head and not head.startswith("v "):
            return False, "OBJ sans sommet"

    return True, ""


# ----------------------------------------------------------------------
# Voie 1 — GLTFExporter
# ----------------------------------------------------------------------

def _gltf_available() -> bool:
    return unreal is not None and getattr(unreal, "GLTFExporter", None) is not None


def _export_via_gltf(asset: Any, output_base: str) -> Optional[str]:
    output_path = output_base + ".glb"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    options = None
    options_cls = getattr(unreal, "GLTFExportOptions", None)
    if options_cls is not None:
        try:
            options = options_cls()
        except Exception:
            options = None

    result = unreal.GLTFExporter.export_to_gltf(asset, output_path, options, set())
    if result is None:
        return None
    return output_path


# ----------------------------------------------------------------------
# Voie 2 — AssetExportTask (indépendante du plugin glTF)
# ----------------------------------------------------------------------

def _export_task_available() -> bool:
    return (unreal is not None
            and getattr(unreal, "AssetExportTask", None) is not None
            and getattr(unreal, "Exporter", None) is not None)


def _export_via_task(asset: Any, output_base: str, extension: str = "obj") -> Optional[str]:
    """Export par le système générique du moteur.

    `run_assetexport_task` choisit lui-même l'exporteur adapté au couple
    (classe d'asset, extension). On ne lui impose donc pas de classe
    d'exporteur : la laisser décider est ce qui rend cette voie robuste
    aux renommages entre versions du moteur.
    """
    output_path = output_base + "." + extension
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    task = unreal.AssetExportTask()
    task.set_editor_property("object", asset)
    task.set_editor_property("filename", output_path)
    task.set_editor_property("automated", True)
    task.set_editor_property("prompt", False)
    task.set_editor_property("replace_identical", True)
    task.set_editor_property("write_empty_files", False)

    ok = unreal.Exporter.run_asset_export_task(task)
    if not ok:
        return None
    return output_path


# ----------------------------------------------------------------------
# Voie 3 — reconstruction manuelle, sans aucun exporteur du moteur
# ----------------------------------------------------------------------

def _manual_available() -> bool:
    return (unreal is not None
            and getattr(unreal, "ProceduralMeshLibrary", None) is not None)


def _export_via_manual_glb(asset: Any, output_base: str,
                           axis_map=None, lod: int = 0) -> Optional[str]:
    """Lit les sections du mesh et écrit le GLB nous-mêmes.

    `get_section_from_static_mesh` rend les tableaux bruts (positions,
    normales, UV, indices) d'une section, en ESPACE LOCAL du mesh. C'est
    exactement ce qu'attend un asset GLB : le placement dans le monde est
    porté par le manifeste, pas par le fichier de mesh — ne pas confondre
    avec le terrain, dont les sommets sont, eux, déjà en espace monde.

    La conversion d'axes est appliquée ici pour rester cohérent avec le
    reste du pipeline : l'exporteur glTF d'Unreal fait la même conversion
    en interne, donc un mesh produit par cette voie doit être orienté de
    la même façon, sinon les deux voies donneraient des résultats
    différents pour le même asset — le pire cas possible.
    """
    if axis_map is None:
        from ue2godot.core.axis import convert_position_ue_to_godot

        def axis_map(x, y, z):
            return convert_position_ue_to_godot(x, y, z, 0.01)

    try:
        section_count = int(unreal.ProceduralMeshLibrary.get_static_mesh_section_count(asset, lod))
    except Exception:
        section_count = 0
    if section_count <= 0:
        return None

    positions: List[float] = []
    normals: List[float] = []
    uvs: List[float] = []
    indices: List[int] = []

    for section in range(section_count):
        try:
            data = unreal.ProceduralMeshLibrary.get_section_from_static_mesh(
                asset, lod, section)
        except Exception:
            continue
        if not data:
            continue

        sec_vertices, sec_triangles, sec_normals, sec_uvs = data[0], data[1], data[2], data[3]

        # Les indices d'une section repartent de 0 : il faut les décaler du
        # nombre de sommets déjà écrits, sinon toutes les sections after la
        # première référencent les sommets de la première.
        offset = len(positions) // 3

        for index, vertex in enumerate(sec_vertices):
            positions.extend(axis_map(float(vertex.x), float(vertex.y), float(vertex.z)))

            if index < len(sec_normals):
                normal = sec_normals[index]
                gx, gy, gz = axis_map(float(normal.x), float(normal.y), float(normal.z))
                length = (gx * gx + gy * gy + gz * gz) ** 0.5 or 1.0
                normals.extend((gx / length, gy / length, gz / length))
            else:
                normals.extend((0.0, 1.0, 0.0))

            if index < len(sec_uvs):
                uv = sec_uvs[index]
                uvs.extend((float(uv.x), float(uv.y)))
            else:
                uvs.extend((0.0, 0.0))

        # La conversion d'axes a un déterminant négatif : elle inverse la
        # chiralité, donc l'ordre des sommets de chaque triangle doit être
        # inversé. Sans cela, toutes les faces pointent vers l'intérieur et
        # le mesh est invisible de l'extérieur — la même erreur que celle
        # corrigée sur le terrain.
        for i in range(0, len(sec_triangles) - 2, 3):
            a = int(sec_triangles[i]) + offset
            b = int(sec_triangles[i + 1]) + offset
            c = int(sec_triangles[i + 2]) + offset
            indices.extend((a, c, b))

    if not positions or not indices:
        return None

    output_path = output_base + ".glb"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    name = os.path.basename(output_base)
    write_glb(output_path, positions, normals, uvs, indices, None, name=name)
    return output_path


# ----------------------------------------------------------------------
# Chaîne
# ----------------------------------------------------------------------

def build_mesh_export_chain(enable_manual: bool = True,
                            enable_task: bool = True) -> StrategyChain:
    strategies = [
        Strategy(
            name="gltf_exporter",
            run=_export_via_gltf,
            available=_gltf_available,
            quality="full",
        ),
    ]

    if enable_task:
        strategies.append(Strategy(
            name="asset_export_task_obj",
            run=lambda asset, base: _export_via_task(asset, base, "obj"),
            available=_export_task_available,
            quality="reduced",
            caveat=("géométrie et UV conservées, matériaux non exportés — "
                    "les slots du manifeste permettent de les réassigner"),
        ))

    if enable_manual:
        strategies.append(Strategy(
            name="manual_glb_reconstruction",
            run=lambda asset, base: _export_via_manual_glb(asset, base),
            available=_manual_available,
            quality="reduced",
            caveat=("LOD 0 reconstruit à la main : géométrie, normales et UV0 "
                    "seulement, sans matériau ni UV secondaires"),
        ))

    return StrategyChain("mesh_export", strategies, _validate_exported_file)
