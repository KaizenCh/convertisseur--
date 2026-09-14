# Audit & Synthèse de Cohérence globale — `ue2godot_corrections_v10`

> **Document de référence & de synthèse**
> **Objet** : Analyse et audit de la cohérence globale entre la documentation source (`UE2GODOT_DOC_FUSIONNEE.md`) et le code réel du dépôt (`ue2godot_corrections_v10`).
> **Statut** : Version V10 validée, destinée à servir de socle pour toutes les évolutions futures.

---

## Sommaire

1. [Vue d'ensemble & Philosophie du projet](#1-vue-densemble--philosophie-du-projet)
2. [Architecture globale & Contrat de données](#2-architecture-globale--contrat-de-données)
3. [Audit de l'Axe 1 : Plomberie, Orchestration & UI (`main.py`)](#3-audit-de-laxe-1--plomberie-orchestration--ui-mainpy)
4. [Audit de l'Axe 2 : Fidélité du Pipeline d'Export Unreal Engine (Python)](#4-audit-de-laxe-2--fidélité-du-pipeline-dexport-unreal-engine-python)
5. [Audit de l'Axe 3 : Fidélité de la Reconstruction Scène Godot 4 (GDScript)](#5-audit-de-laxe-3--fidélité-de-la-reconstruction-scène-godot-4-gdscript)
6. [Bilan des Tests & Couverture de Validation](#6-bilan-des-tests--couverture-de-validation)
7. [Feuille de Route & Recommandations pour la Suite](#7-feuille-de-route--recommandations-pour-la-suite)

---

## 1. Vue d'ensemble & Philosophie du projet

Le projet **UE2Godot** (ou *Convertisseur UE5 → Godot 4*) a pour objectif de convertir automatiquement une scène/map Unreal Engine 5.5 vers une scène jouable Godot 4 (`.tscn`).

L'approche repose sur un principe fondamental : **Unreal Engine ne fait qu'exporter des données et des assets** (manifeste JSON, fichiers `.glb`, textures PNG), tandis que **Godot lit ces fichiers pour reconstruire la scène en mémoire et la sauvegarder**.

La version **`ue2godot_corrections_v10`** est la version pivot du projet. Elle intègre l'ensemble de l'architecture généralisée sous forme de package Python (`ue2godot/`), un plugin/addon Godot (`godot/addons/ue2godot/`), une interface PySide6 (`main.py`), et une suite de tests automatisés (`tests/`).

### Philosophie de conservation & amélioration
Plutôt que d'abandonner cette version ou de repartir de zéro, le travail mené sur `v10` a consisté à consolider l'existant, à corriger les décalages de plomberie et d'export/reconstruction identifiés dans les audits précédents (§15/§16 du document MD), et à établir une correspondance exacte entre la théorie (`UE2GODOT_DOC_FUSIONNEE.md`) et la pratique du code.

---

## 2. Architecture globale & Contrat de données

### 2.1 Découpage en couches
L'architecture de `ue2godot_corrections_v10` respecte le principe de séparation des responsabilités :

```
[ UE5 Editor (Python) ]  ---> [ Fichiers d'Échange (JSON/GLB/PNG) ]  ---> [ Godot 4 Engine (GDScript) ]
        │                                    │                                    │
  ue2godot.ue.*                        C:/Export/...                        addons/ue2godot/*
  (step0 à step4)                   (Manifest, AssetMap, DecalMap)         (MapBuilder, Headless)
```

1. **`ue2godot.core`** : Python pur, sans dépendance `unreal` ni Godot (gestion des configurations, schémas, conversions d'axes, encodage PNG, écriture GLB maison, contrôle croisé).
2. **`ue2godot.ue`** : Scripts s'exécutant dans l'environnement Python embarqué d'Unreal Engine 5 (détachement, scan du manifeste, export des meshes, bake du terrain et des décals).
3. **`ue2godot.orchestrator`** : Orchestration du flux, gestion des permissions, adaptateur d'exécution distante Unreal (`ueremote`) et adaptateur Godot CLI.
4. **`godot/addons/ue2godot/`** : Code GDScript côté Godot 4 pour l'instanciation de la scène en mode `SceneTree` (Headless) ou `EditorScript`.

### 2.2 Contrat de données central
Le pivot absolu du pipeline est le triptyque de fichiers JSON :
- **`level_manifest_v10.json`** : Spécification complète et déclarative de la scène (transforms, acteurs, composants, hiérarchie LevelInstance, matériaux, décals, VFX, audio, et bloc `reconstruction`).
- **`ue5_godot_asset_map.json`** : Table de correspondance entre chemins d'assets UE (`/Game/...`) et fichiers `.glb` exportés (`res://UEAssets/Meshes/...`).
- **`ue5_godot_decal_map.json`** : Table de correspondance des matériaux de décals, textures masques/normales générées, teintes RGB et marqueurs VFX/Audio.

---

## 3. Audit de l'Axe 1 : Plomberie, Orchestration & UI (`main.py`)

### 3.1 Statut actuel de la plomberie
Le fichier principal `main.py` (5390 lignes, PySide6) sert d'interface utilisateur pour piloter l'ensemble du processus.

| Fonctionnalité | Description dans la Doc MD | Implémentation réelle dans `v10` | Statut de Cohérence |
|---|---|---|---|
| **Exécution des étapes UE** | Les étapes UE nécessitent la session Python d'Unreal | `main.py` génère les commandes via `UERemoteAdapter.build_command()` avec ajout automatique du `sys.path` | **Conforme & Sécurisé** |
| **Contrôle croisé (`crosscheck`)** | Vérification de la cohérence des `run_id` et des sorties JSON | `crosscheck_json_outputs()` exécuté lors de la phase 2 d'orchestration | **Conforme** |
| **Étape 5 (Copie des assets)** | Transfert des JSON et dossiers `Meshes`/`Decals` vers le projet Godot | `copy_step5()` copie et vérifie l'intégrité par hachage SHA-256 | **Conforme** |
| **Exécution Godot Headless** | Lancement de Godot sans GUI via la ligne de commande | `GodotCLIAdapter` prépare les appels `--import` puis `--script entry_headless.gd` | **Conforme** |

### 3.2 Points d'amélioration sur l'UI & l'Orchestration
1. **Granularité d'exécution** : Le bouton principal d'export Unreal génère les commandes pour toutes les étapes activées. L'ajout de boutons individuels par étape (`step0`, `step1`, `step2`, `step3`, `step4`) dans l'IHM permet d'exécuter une étape précise isolément sans relancer les autres.
2. **Visibilité des modules optionnels** : Les interrupteurs d'options (Landscape, Decals, VFX) dans la configuration mettent correctement à jour le dictionnaire `ResolvedConfig` qui est ensuite injecté dans le manifeste sous la clé `manifest.pipeline.resolved_config`.

---

## 4. Audit de l'Axe 2 : Fidélité du Pipeline d'Export Unreal Engine (Python)

### 4.1 Analyse Étape par Étape

#### Étape 0 : Pré-traitement (`ue2godot/ue/steps/step0_preprocess.py`)
- **Rôle** : Détacher tous les acteurs parents/enfants pour simplifier la chaîne de transforms.
- **Fidélité** : Utilise explicitement `unreal.DetachmentRule.KEEP_WORLD` sur les 3 axes (location, rotation, scale). Inclut une option `dry_run` sécurisée.
- **Verdict** : **100% Conforme**.

#### Étape 1 : Manifeste (`ue2godot/ue/steps/step1_manifest.py`)
- **Rôle** : Scanner l'intégralité du niveau et écrire `level_manifest_v10.json`.
- **Analyse des régressions historiques / Corrections apportées** :
  - *Scan des acteurs* : Utilise l'énumération récursive via `ObjectIterator` et `EditorActorSubsystem` avec déduplication.
  - *Registre LevelInstance* : Reconstruit la hiérarchie récursive, gère la profondeur (jusqu'à 64) et alimente `level_instances.registry`, `placements` et `hierarchy`.
  - *Composition de transforms* : Implémente `compose_chain_transform()` en respectant l'ordre Unreal (`child * parent`) et en évitant le double comptage des ancêtres.
  - *Composants Instanciés (ISM / HISM)* : Lit chaque instance via `get_instance_transform(i, world_space=True)` et produit les tableaux `instance_final_world_transforms[]`.
  - *Détails Blueprints* : Capture la structure interne des composants de Blueprints (`blueprints.actors[]`).
  - *Contrat de Readiness* : Le bloc `reconstruction` évalue dynamiquement `ready_for_godot_geometry` et `ready_for_godot_fx` en fonction des échecs réels de transform et de références.
- **Verdict** : **Fidèle à la source de vérité V10-8**.

#### Étape 2 : Export des Meshes (`ue2godot/ue/steps/step2_meshes.py`)
- **Rôle** : Exporter chaque StaticMesh unique en GLB via `unreal.GLTFExporter`.
- **Fidélité** :
  - Génère un nom de fichier unique incorporant un hash SHA1 court du chemin UE (`unique_filename_for_path`) pour éviter les collisions d'assets homonymes dans des dossiers différents.
  - Valide les fichiers GLB générés (taille minimale et présence de l'entête magique `b"glTF"`).
  - Respecte l'idempotence (`skip_existing`).
- **Verdict** : **100% Conforme**.

#### Étape 3 : Landscape (`ue2godot/ue/steps/step3_landscape.py`)
- **Rôle** : Reconstruire la géométrie du terrain par raycasting et baker la texture albedo BaseColor.
- **Analyse d'architecture** :
  - **Découplage réussi** : Expose deux points d'entrée distincts :
    1. `configure_landscape_material(cfg)` : Permet d'assigner ou de vérifier le matériau du terrain indépendamment (répondant exactement aux attentes d'exécution autonome).
    2. `run(cfg)` : Exécute le pipeline complet (matériau -> raycasting -> bake orthographique SceneCapture2D -> writer GLB maison -> patch du manifeste et de l'asset map).
- **Verdict** : **Conforme & Modularisé**.

#### Étape 4 : Décals & VFX (`ue2godot/ue/steps/step4_decals_vfx.py`)
- **Rôle** : Traiter les matériaux de décals, baker leurs textures RGBA et générer les marqueurs VFX/Audio.
- **Fidélité** :
  - Implémente la cascade de résolution de paramètres de texture (override sur l'instance -> valeur par défaut du parent -> balayage des noms de paramètres).
  - Effectue le bake via un matériau temporaire unlit et une Render Target.
  - Utilise `png_codec.py` pour composer l'image RGBA finale teinté.
  - Génère les marqueurs pour Niagara et les composants Audio.
- **Verdict** : **Fidèle à la source de vérité**.

---

## 5. Audit de l'Axe 3 : Fidélité de la Reconstruction Scène Godot 4 (GDScript)

### 5.1 Structure du Plugin GDScript (`godot/addons/ue2godot/`)

Le reconstructeur Godot a été extrait de son mode `EditorScript` monolithique vers une architecture modulaire exécutable en Headless via `SceneTree` (`entry_headless.gd`) tout en conservant le point d'entrée éditeur (`entry_editor.gd`).

```
godot/addons/ue2godot/
├── entry_headless.gd   # Point d'entrée SceneTree CLI (--script)
├── entry_editor.gd     # Point d'entrée EditorScript (clic droit Run)
└── core/
    ├── transform.gd    # LA source de vérité mathématique (axes & quaternions)
    ├── map_builder.gd  # Orchestrateur central de construction de scène
    ├── geometry_builder.gd # Construction des StaticMesh et ISM/HISM
    ├── decal_builder.gd    # Construction des Decal3D (taille, orientation, tint)
    └── vfx_builder.gd      # Marqueurs VFX et systèmes de substitution de particules
```

### 5.2 Analyse des Composants GDScript

#### 1. Conversion de Repère (`core/transform.gd`)
- **Rôle** : Convertir les rotateurs/quaternions et positions Unreal (centimètres, Z-up) vers Godot (mètres, Y-up).
- **Fidélité Mathématique** :
  - Formule quaternion UE : `qy := -cr * sp * cy - sr * cp * sy` (signe négatif sur `qy` validé en V10.7 pour éliminer les effets miroir sur Pitch/Roll).
  - Matrice de changement de base `C` à déterminant **-1** (`Gx=Ux, Gy=Uz, Gz=Uy`).
  - Conversion de position : `Vector3(ue.x, ue.z, ue.y) * 0.01`.
- **Verdict** : **100% Conforme à la source de vérité**.

#### 2. Instanciation Géométrique (`core/geometry_builder.gd`)
- **Fidélité** :
  - Gère les 5 flags de tolérance (`FAIL_ON_MISSING_ASSET_MAPPING`, `FAIL_ON_MISSING_GLB`, `FAIL_ON_TRANSFORM_FAILURE`, etc.).
  - Distingue les objets statiques uniques et les instances multiples (ISM/HISM) en parcourant `instance_final_world_transforms[]`.
- **Verdict** : **Conforme**.

#### 3. Décals (`core/decal_builder.gd`)
- **Fidélité** :
  - Application du réarrangement d'axes pour la boîte de projection Decal3D : `Vector3(width, thickness, height)`.
  - Application de la couleur/teinte (`modulate`) extraite de la carte des décals.
  - Attribution de l'orientation via `_transform_from_v10()` orthonormalisée.
- **Verdict** : **Conforme**.

#### 4. VFX & Particules (`core/vfx_builder.gd`)
- **Fidélité & Flexibilité** :
  - Prend en charge les modes de configuration `none`, `markers` (création de `Marker3D` sous le nœud `VFX_MARKERS_NOT_CONVERTED`), et `substitutes` (reconstruction de systèmes de particules GPUParticles3D avec régulation du budget de lumières temps réel).
- **Verdict** : **Conforme**.

---

## 6. Bilan des Tests & Couverture de Validation

Le dépôt `ue2godot_corrections_v10` contient une suite de tests unitaire automatisée sous PyTest (`tests/`).

### Résultats d'exécution (`PYTHONPATH=. pytest`)
```
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.2, pluggy-1.6.0
rootdir: /app
collected 49 items

tests/test_core.py ......                                                [ 12%]
tests/test_glb.py .....                                                  [ 22%]
tests/test_landscape_clustering.py ........                              [ 38%]
tests/test_landscape_material_discovery.py .......                       [ 53%]
tests/test_permissions.py .......................                        [100%]

============================== 49 passed in 0.19s ==============================
```

- **`test_core.py`** : Valide le hachage d'identifiants stables, les schémas, les conversions d'axes et la gestion des résultats `Result`.
- **`test_glb.py`** : Valide la création de maillages en grille et l'écriture du binaire GLB maison.
- **`test_landscape_clustering.py`** : Valide l'algorithme de regroupement spatial des dalles de terrain.
- **`test_landscape_material_discovery.py`** : Valide la détection autonome et l'assignation des matériaux de Landscape.
- **`test_permissions.py`** : Valide la sécurité des exécutions distantes.

---

## 7. Feuille de Route & Recommandations pour la Suite

Grâce au travail de structuration réalisé sur `ue2godot_corrections_v10`, le codebase est désormais parfaitement cohérent avec la documentation `UE2GODOT_DOC_FUSIONNEE.md`.

Voici les recommandations stratégiques pour maintenir et faire évoluer ce projet sans régression :

1. **Conservation de l'architecture V10** : Conserver `ue2godot_corrections_v10` comme socle de référence incontournable.
2. **Exécution des commandes Unreal** : Lors de l'utilisation de l'UI (`main.py`), exécuter les snippets générés dans la console Python d'Unreal Engine en s'assurant que le chemin du module est bien présent dans `sys.path`.
3. **Poursuite du développement piloté par les tests** : Pour toute nouvelle fonctionnalité (ex: gestion enrichie des Skeletal Meshes ou animations), ajouter systématiquement le test unitaire correspondant dans `tests/` afin de maintenir le taux de réussite à 100%.

---
*Document produit pour le projet UE2Godot — Version V10.*
