# -*- coding: utf-8 -*-
"""
Registre des opérations — chaque capacité du framework, exposée à l'unité.

POURQUOI CE MODULE
------------------
L'UI ne proposait qu'UNE chaîne linéaire : « générer les exports » (bloc de 5
étapes indissociables), puis « copier + ouvrir Godot ». Tout ce que le
framework sait faire par ailleurs était inatteignable depuis l'interface :
configurer le matériau du Landscape seul, relancer uniquement les décals
après avoir corrigé un matériau, revérifier la cohérence des 3 JSON sans rien
réexporter, déployer le runtime Godot sans reconstruire, reconstruire la
scène sans recopier les assets…

Ce fichier est la source de vérité de CE QUE le framework sait faire. L'UI en
est un rendu : ajouter une opération ici la fait apparaître dans l'interface
sans toucher au code de l'UI, et il devient impossible qu'une capacité existe
dans le framework sans être atteignable (c'était le cas de
configure_landscape_material, atteignable seulement noyée dans l'étape 3).

CONTRAT
-------
Chaque opération déclare où elle s'exécute (`side`), ce qu'elle exige en
entrée (`requires`), ce qu'elle produit (`produces`), et si elle modifie
l'état vivant de l'éditeur (`mutates_editor`). Les dépendances sont
déclaratives, jamais implicites dans l'ordre d'une liste : l'UI peut donc
prévenir « cette opération a besoin du manifeste, qui n'existe pas encore »
au lieu de laisser l'utilisateur découvrir l'échec après coup.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

# Côtés d'exécution — déterminent le transport, pas l'ordre.
SIDE_UNREAL = "unreal_editor"   # nécessite `import unreal` → session de l'éditeur
SIDE_LOCAL = "local"            # tourne dans le process de l'UI
SIDE_GODOT = "godot_cli"        # sous-processus Godot headless
SIDE_UI = "ui"                  # action purement locale (ouvrir, diagnostiquer)

# Artefacts manipulés, utilisés pour exprimer requires/produces.
ART_PREPROCESS = "preprocess_done"
ART_MANIFEST = "manifest"
ART_ASSET_MAP = "asset_map"
ART_MESHES = "mesh_files"
ART_DECAL_MAP = "decal_map"
ART_LANDSCAPE = "landscape_glb"
ART_LS_MATERIAL = "landscape_material"
ART_COPIED = "assets_copied"
ART_RUNTIME = "godot_runtime"
ART_IMPORTED = "godot_imported"
ART_SCENE = "rebuilt_scene"


@dataclass
class Operation:
    key: str
    label: str
    description: str
    category: str
    side: str
    requires: List[str] = field(default_factory=list)
    produces: List[str] = field(default_factory=list)
    # Étape du framework à invoquer côté Unreal (clé de ue2godot.ue.entry.STEPS).
    # None pour les opérations qui ne sont pas une étape standard.
    step_name: Optional[str] = None
    # Modifie l'état vivant de l'éditeur Unreal (détachement d'acteurs,
    # assignation de matériau…). L'UI demande confirmation pour celles-ci.
    mutates_editor: bool = False
    # Réécrit son fichier de sortie en entier plutôt que d'y ajouter. Relancer
    # une telle opération après une opération qui n'ajoute (append) efface le
    # travail de cette dernière — c'est la contrainte d'ordre historique du
    # pipeline, rendue explicite ici au lieu d'être un avertissement écrit
    # dans un en-tête de fichier.
    rewrites_output: bool = False
    long_running: bool = False


CATEGORY_UNREAL = "Export Unreal"
CATEGORY_LANDSCAPE = "Terrain"
CATEGORY_TRANSFER = "Transfert"
CATEGORY_GODOT = "Reconstruction Godot"
CATEGORY_VERIFY = "Vérification"
CATEGORY_ACCESS = "Accès & autorisations"


OPERATIONS: List[Operation] = [
    # ---------------------------------------------------------------- Unreal
    Operation(
        key="preprocess",
        label="Détacher les acteurs (prétraitement)",
        description=(
            "Détache les acteurs attachés en préservant leur transform monde "
            "(KEEP_WORLD). Les LevelInstances restantes doivent être cassées à "
            "la main dans l'éditeur. Modifie la map ouverte."
        ),
        category=CATEGORY_UNREAL, side=SIDE_UNREAL, step_name="preprocess",
        produces=[ART_PREPROCESS], mutates_editor=True,
    ),
    Operation(
        key="manifest",
        label="Scanner la map (manifeste)",
        description=(
            "Parcourt tous les acteurs et produit level_manifest_v10.json : "
            "placements, LevelInstances, ISM/HISM par instance, décals, "
            "Niagara, lumières, audio. Réécrit le fichier en entier."
        ),
        category=CATEGORY_UNREAL, side=SIDE_UNREAL, step_name="manifest",
        produces=[ART_MANIFEST], rewrites_output=True, long_running=True,
    ),
    Operation(
        key="meshes",
        label="Exporter les meshes en GLB",
        description=(
            "Exporte chaque StaticMesh (et SkeletalMesh en bind pose) unique "
            "référencé par le manifeste, et produit l'asset map. Réécrit "
            "l'asset map en entier."
        ),
        category=CATEGORY_UNREAL, side=SIDE_UNREAL, step_name="meshes",
        requires=[ART_MANIFEST], produces=[ART_ASSET_MAP, ART_MESHES],
        rewrites_output=True, long_running=True,
    ),
    Operation(
        key="decals_vfx",
        label="Exporter les décals, VFX et audio",
        description=(
            "Résout les textures de décal (masque, teinte, normale), les bake "
            "en PNG, et écrit la decal map avec les marqueurs VFX/audio. "
            "Ajoute aux fichiers existants."
        ),
        category=CATEGORY_UNREAL, side=SIDE_UNREAL, step_name="decals_vfx",
        requires=[ART_MANIFEST], produces=[ART_DECAL_MAP], long_running=True,
    ),

    # -------------------------------------------------------------- Terrain
    Operation(
        key="landscape_material",
        label="Configurer le matériau du Landscape (seul)",
        description=(
            "Trouve et assigne le matériau du Landscape sans rien exporter : "
            "essaie le dossier configuré, puis cherche un dossier au nom "
            "évocateur en remontant et descendant l'arborescence depuis la "
            "map source. Rapide, relançable à volonté pendant qu'on itère sur "
            "le matériau dans Unreal."
        ),
        category=CATEGORY_LANDSCAPE, side=SIDE_UNREAL,
        step_name="landscape_material", produces=[ART_LS_MATERIAL],
        mutates_editor=True,
    ),
    Operation(
        key="landscape",
        label="Exporter le terrain (géométrie + texture)",
        description=(
            "Configure le matériau, puis reconstruit la géométrie du terrain "
            "par raycast en grille et bake sa texture en vue de dessus. "
            "Ajoute au manifeste et à l'asset map — à lancer APRÈS eux."
        ),
        category=CATEGORY_LANDSCAPE, side=SIDE_UNREAL, step_name="landscape",
        requires=[ART_MANIFEST, ART_ASSET_MAP], produces=[ART_LANDSCAPE],
        mutates_editor=True, long_running=True,
    ),

    # ------------------------------------------------------------ Transfert
    Operation(
        key="copy_assets",
        label="Copier les assets vers le projet Godot",
        description=(
            "Copie les meshes, décals et les 3 JSON vers le projet Godot "
            "cible, en vérifiant l'empreinte de chaque fichier après copie."
        ),
        category=CATEGORY_TRANSFER, side=SIDE_LOCAL,
        requires=[ART_MANIFEST, ART_ASSET_MAP], produces=[ART_COPIED],
    ),
    Operation(
        key="deploy_runtime",
        label="Déployer l'addon ue2godot dans le projet Godot",
        description=(
            "Copie les scripts de reconstruction (addons/ue2godot) dans le "
            "projet Godot. Nécessaire avant toute construction, et à relancer "
            "après chaque mise à jour du framework."
        ),
        category=CATEGORY_TRANSFER, side=SIDE_LOCAL, produces=[ART_RUNTIME],
    ),

    # ----------------------------------------------------- Reconstruction
    Operation(
        key="godot_import",
        label="Importer les assets dans Godot (headless)",
        description=(
            "Lance Godot en mode headless pour importer les GLB et PNG copiés. "
            "Obligatoire avant la construction : sans import, les meshes ne "
            "peuvent pas être chargés. Long sur un gros lot d'assets."
        ),
        category=CATEGORY_GODOT, side=SIDE_GODOT,
        requires=[ART_COPIED], produces=[ART_IMPORTED], long_running=True,
    ),
    Operation(
        key="godot_build",
        label="Construire la scène (headless)",
        description=(
            "Exécute entry_headless.gd : instancie chaque placement, applique "
            "les décals et les VFX, et sauvegarde le .tscn. Rend un rapport "
            "comparant le déclaré au construit."
        ),
        category=CATEGORY_GODOT, side=SIDE_GODOT,
        requires=[ART_IMPORTED, ART_RUNTIME], produces=[ART_SCENE],
        long_running=True,
    ),
    Operation(
        key="open_godot",
        label="Ouvrir le projet dans l'éditeur Godot",
        description=(
            "Ouvre l'éditeur Godot sur le projet cible. Utile pour inspecter "
            "le résultat, ou pour lancer le constructeur à la main via "
            "l'EditorScript si l'on préfère le mode manuel."
        ),
        category=CATEGORY_GODOT, side=SIDE_UI,
    ),
    Operation(
        key="open_scene",
        label="Ouvrir la scène reconstruite",
        description="Ouvre directement le .tscn produit dans l'éditeur Godot.",
        category=CATEGORY_GODOT, side=SIDE_UI, requires=[ART_SCENE],
    ),

    # --------------------------------------------------------- Vérification
    Operation(
        key="crosscheck",
        label="Vérifier la cohérence des 3 JSON",
        description=(
            "Valide le schéma de chaque fichier et compare leurs run_id : "
            "détecte qu'une étape a été relancée après une autre et a effacé "
            "son travail. Ne modifie rien, relançable à tout moment."
        ),
        category=CATEGORY_VERIFY, side=SIDE_LOCAL, requires=[ART_MANIFEST],
    ),
    Operation(
        key="inspect_source",
        label="Inspecter les fichiers produits",
        description=(
            "Relit le dossier source et rapporte ce qui existe réellement sur "
            "disque : manifeste, asset map, decal map, nombre de GLB, "
            "compteurs déclarés. Aucune écriture."
        ),
        category=CATEGORY_VERIFY, side=SIDE_LOCAL,
    ),
    Operation(
        key="read_reports",
        label="Relire les rapports d'étapes",
        description=(
            "Affiche les rapports JSON écrits par chaque étape (statut, "
            "compteurs, erreurs, avertissements), y compris ceux produits par "
            "une exécution manuelle dans la console Python d'Unreal."
        ),
        category=CATEGORY_VERIFY, side=SIDE_LOCAL,
    ),

    # ------------------------------------------------------------ Accès
    Operation(
        key="check_auth",
        label="Vérifier les autorisations Unreal",
        description=(
            "Contrôle les quatre autorisations nécessaires au pilotage "
            "externe : plugin Python, exécution distante, visibilité du "
            "module, éditeur joignable."
        ),
        category=CATEGORY_ACCESS, side=SIDE_UI,
    ),
    Operation(
        key="grant_auth",
        label="Accorder les autorisations manquantes",
        description=(
            "Écrit dans le .uproject et DefaultEngine.ini (sauvegarde créée). "
            "Exige que l'éditeur soit fermé, et un redémarrage ensuite."
        ),
        category=CATEGORY_ACCESS, side=SIDE_UI,
    ),
    Operation(
        key="test_connection",
        label="Tester la connexion à l'éditeur",
        description="Ouvre réellement un canal et exécute une instruction triviale.",
        category=CATEGORY_ACCESS, side=SIDE_UI,
    ),
    Operation(
        key="diagnose_network",
        label="Diagnostic réseau",
        description=(
            "Rapport pas-à-pas de la découverte multicast : interfaces, "
            "adhésion au groupe, pings émis, réponses."
        ),
        category=CATEGORY_ACCESS, side=SIDE_UI,
    ),
]


BY_KEY: Dict[str, Operation] = {op.key: op for op in OPERATIONS}


def categories() -> List[str]:
    """Catégories dans l'ordre de déclaration (pas alphabétique : l'ordre
    reflète la progression naturelle du travail)."""
    seen: List[str] = []
    for op in OPERATIONS:
        if op.category not in seen:
            seen.append(op.category)
    return seen


def operations_in(category: str) -> List[Operation]:
    return [op for op in OPERATIONS if op.category == category]


# ----------------------------------------------------------------------
# Workflows nommés — des compositions SUGGÉRÉES, pas le seul chemin.
# ----------------------------------------------------------------------

WORKFLOWS: Dict[str, Dict[str, Any]] = {
    "full_reconstruction": {
        "label": "Conversion complète",
        "description": "De la map Unreal à la scène Godot reconstruite.",
        "operations": ["manifest", "meshes", "landscape", "decals_vfx",
                       "crosscheck", "copy_assets", "deploy_runtime",
                       "godot_import", "godot_build"],
    },
    "unreal_only": {
        "label": "Exports Unreal seulement",
        "description": "Produit les 3 JSON et les fichiers, sans rien envoyer à Godot.",
        "operations": ["manifest", "meshes", "landscape", "decals_vfx", "crosscheck"],
    },
    "geometry_only": {
        "label": "Géométrie seule",
        "description": "Sans terrain, sans décals ni VFX — le plus rapide pour tester.",
        "operations": ["manifest", "meshes", "crosscheck", "copy_assets",
                       "deploy_runtime", "godot_import", "godot_build"],
    },
    "refresh_decals": {
        "label": "Rafraîchir les décals",
        "description": (
            "Réexporte uniquement les décals puis reconstruit, sans retoucher "
            "au manifeste ni aux meshes — utile après avoir corrigé un matériau."
        ),
        "operations": ["decals_vfx", "copy_assets", "godot_import", "godot_build"],
    },
    "rebuild_only": {
        "label": "Reconstruire seulement",
        "description": (
            "Repart des fichiers déjà exportés : ni Unreal ni réexport, juste "
            "la copie, l'import et la construction Godot."
        ),
        "operations": ["copy_assets", "deploy_runtime", "godot_import", "godot_build"],
    },
    "terrain_only": {
        "label": "Terrain seulement",
        "description": "Matériau + géométrie + texture du terrain, rien d'autre.",
        "operations": ["landscape_material", "landscape", "crosscheck"],
    },
}


def validate_selection(keys: List[str], available: List[str]) -> List[str]:
    """Avertissements sur une sélection libre d'opérations.

    Ne bloque JAMAIS : l'utilisateur a le droit de lancer une opération dont
    les entrées viennent d'une exécution précédente que nous n'avons pas
    observée (typiquement un export lancé à la main dans la console Python
    d'Unreal). On signale, on n'interdit pas.
    """
    warnings: List[str] = []
    produced = set(available)
    selected = [BY_KEY[k] for k in keys if k in BY_KEY]

    for op in selected:
        for need in op.requires:
            if need not in produced:
                producer = next(
                    (o.label for o in OPERATIONS if need in o.produces), need
                )
                warnings.append(
                    f"« {op.label} » a besoin de « {producer} », qui n'est ni "
                    "déjà présent ni sélectionné avant elle."
                )
        produced.update(op.produces)

    # Contrainte d'ordre historique : une opération qui réécrit son fichier
    # en entier, placée après une opération qui n'y ajoute qu'un complément,
    # efface silencieusement ce complément.
    appended: List[Operation] = []
    for op in selected:
        if op.rewrites_output and appended:
            erased = ", ".join(o.label for o in appended)
            warnings.append(
                f"« {op.label} » réécrit ses fichiers en entier et effacera le "
                f"travail de : {erased}. Placez-la avant, ou relancez-les après."
            )
        elif not op.rewrites_output and op.produces:
            appended.append(op)

    return warnings
