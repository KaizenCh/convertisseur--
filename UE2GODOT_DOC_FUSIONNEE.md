# Documentation fusionnée — UE5 → Godot Constructor

> Fusion de 3 documents en un seul, dans l'ordre de lecture recommandé :
> 1. Source de vérité du pipeline de référence (les 9 scripts originaux)
> 2. §14 — Décisions de conception du framework `ue2godot/` (session archi)
> 3. §15 — Diagnostic profond du dépôt (session audit) + §16 ajouté ici,
>    qui documente l'avancement réel de la session de correctifs en cours.

---

# Pipeline UE5 → Godot : Source de vérité pour la généralisation en framework

> Document produit à partir d'une lecture exhaustive, ligne par ligne, des 8 fichiers
> actuellement dans la zone `project` (aucune troncature, aucun saut).
> Objectif : servir de base à la conception du **framework général** et du
> **fichier orchestrateur**, sans avoir à relire les scripts sources.

---

## Sommaire

1. [Vue d'ensemble et ordre d'exécution réel](#1-vue-densemble-et-ordre-dexécution-réel)
2. [Le reconstructeur `.gd` — désormais lu intégralement](#2-le-reconstructeur-gd--désormais-lu-intégralement)
3. [Fichier par fichier](#3-fichier-par-fichier)
4. [Le contrat de données central : `level_manifest_v10.json`](#4-le-contrat-de-données-central--level_manifest_v10json)
5. [Les deux autres fichiers d'échange](#5-les-deux-autres-fichiers-déchange)
6. [Patterns et algorithmes transversaux](#6-patterns-et-algorithmes-transversaux)
7. [Chronologie des bugs — pourquoi chaque règle existe](#7-chronologie-des-bugs--pourquoi-chaque-règle-existe)
8. [Déjà généralisable vs encore lié au projet X (Necropolis)](#8-déjà-généralisable-vs-encore-lié-au-projet-x-necropolis)
9. [Ce qu'il faudra généraliser, point par point](#9-ce-quil-faudra-généraliser-point-par-point)
10. [Contraintes et pièges qui peuvent tout casser](#10-contraintes-et-pièges-qui-peuvent-tout-casser)
11. [Ce dont l'orchestrateur a besoin](#11-ce-dont-lorchestrateur-a-besoin)
12. [Incohérences et bugs latents repérés pendant cette lecture](#12-incohérences-et-bugs-latents-repérés-pendant-cette-lecture)
13. [Leçons tirées de l'historique des conversations — ce qu'il ne faut plus refaire](#13-leçons-tirées-de-lhistorique-des-conversations--ce-quil-ne-faut-plus-refaire)

---

## 1. Vue d'ensemble et ordre d'exécution réel

Le pipeline convertit une map Unreal Engine 5.5 en scène Godot 4 en 6 étapes,
réparties entre deux moteurs. Unreal ne fait qu'**écrire des fichiers**
(JSON + GLB + PNG) ; Godot **lit ces fichiers** et reconstruit la scène. Il n'y a
aucune communication directe entre les deux moteurs — le manifeste JSON est
l'unique pivot.

```
CÔTÉ UNREAL (Python, dans l'éditeur)                    CÔTÉ GODOT (GDScript)
──────────────────────────────────────────────────────  ──────────────────────
0. ue5_preprocess_detach_all.py        (manuel, 1x)
   └─ casse les LevelInstances (UI) + détache tous
      les acteurs enfants en gardant leur transform
      monde (KEEP_WORLD)

1. unreal_export_manifest_v10-8.py
   └─ scanne TOUT le niveau (acteurs, composants,
      matériaux, textures, LevelInstances, FX...)
   └─ ÉCRIT (from scratch) :
        C:/Export/level_manifest_v10.json
        C:/Export/level_manifest_v10.txt

2. unreal_export_godot_assets_PATCHED.py
   └─ LIT  level_manifest_v10.json (geometry.unique_meshes)
   └─ exporte chaque StaticMesh en GLB (GLTFExporter UE natif)
   └─ ÉCRIT (from scratch) :
        C:/Export/GodotAssets/Meshes/*.glb
        C:/Export/GodotAssets/ue5_godot_asset_map.json

3. unreal_export_landscape.py
   └─ LIT  level_manifest_v10.json (pour le patcher)
   └─ LIT  ue5_godot_asset_map.json (pour l'enrichir)
   └─ reconstruit la géométrie du Landscape par raycasts
      + bake la texture BaseColor via SceneCapture2D
   └─ ÉCRIT un GLB "à la main" (writer glTF binaire maison)
   └─ PATCHE (append, ne réécrit pas from scratch) :
        level_manifest_v10.json      (ajoute 1 placement identité)
        ue5_godot_asset_map.json     (ajoute 1 entrée asset)

4. unreal_export_decals_vfx.py  (utilise png_codec.py)
   └─ LIT  level_manifest_v10.json (effects.decals, effects.niagara)
   └─ résout textures/tint depuis les matériaux de décal
   └─ compose les PNG RGBA (algo pur Python, sans PIL)
   └─ ÉCRIT :
        C:/Export/GodotAssets/Decals/*.png
        C:/Export/GodotAssets/ue5_godot_decal_map.json
                                                          5. [COPIE MANUELLE]
                                                             les 2 dossiers +
                                                             3 JSON dans le
                                                             projet Godot
                                                          ─────────────────────
                                                          6. ue5_godot_map_constructor_v10_PATCHED.gd
                                                             (EditorScript @tool,
                                                             lancé depuis
                                                             FileSystem → clic
                                                             droit → Run)
                                                             LIT les 3 JSON
                                                             CONSTRUIT le .tscn
                                                             et l'ÉCRIT via
                                                             ResourceSaver
```

**Statut mis à jour** : ce fichier a été fourni et lu intégralement (2283
lignes, version interne V10.12). Il n'est plus manquant — voir §2 et §3.9
pour l'analyse complète. Le patch `godot_decals_vfx_addition.gd` documenté
au §3.8 est **déjà fusionné** dedans (`_build_decals()` y est identique à
l'addition documentée) : à lire désormais comme une archive historique de
la façon dont le patch a été appliqué, pas comme un fichier encore à coller.

**Ordre strict et sa raison** : le manifeste (étape 1) et l'export d'assets
(étape 2) réécrivent leur JSON **intégralement à chaque exécution**
(`json.dump` sur un dict reconstruit de zéro). Le Landscape (étape 3) et les
décals (étape 4) ne font que **lire-modifier-réécrire en append** ces mêmes
JSON. Si on relance l'étape 1 ou 2 après l'étape 3/4, tout le travail du
Landscape et des décals est perdu silencieusement — c'est écrit noir sur blanc
dans les headers de fichiers ("Landscape EN DERNIER").

Contrôle de cohérence documenté (mais **manuel**, jamais vérifié par du code) :
le nombre d'entrées de `ue5_godot_asset_map.json["assets"]` doit égaler le
nombre de `unique_meshes` du manifeste. **L'orchestrateur devrait automatiser
cette vérification.**

---

## 2. Le reconstructeur `.gd` — désormais lu intégralement

**Le fichier `ue5_godot_map_constructor_v10_PATCHED.gd` a été fourni et lu
en entier (2283 lignes, version interne V10.12).** C'était la seule pièce
manquante de tout le pipeline lors de la première lecture ; l'analyse
complète — rôle, fonctions, connexions, ce qui est déjà générique vs encore
lié au projet X, ce qui doit être généralisé — est maintenant au §3.9, au
même niveau de détail que les 8 autres fichiers. Ce fichier est de très loin
le plus important côté reconstruction : c'est un **`@tool extends
EditorScript`** de Godot 4, lancé manuellement depuis le panneau
FileSystem de l'éditeur (clic droit → *Run*) — pas un script exécuté au
runtime du jeu, et pas trivialement pilotable depuis l'extérieur de
l'éditeur (implication directe pour l'orchestrateur, voir §10 et §11).

Il charge les 3 JSON produits côté Unreal, construit la scène entière en
mémoire (géométrie, décals, marqueurs VFX, métadonnées de LevelInstance),
puis l'empaquette en `PackedScene` et l'écrit sur disque via
`ResourceSaver.save()` — `res://Map--_REBUILT.tscn`. Aucune scène n'existe
tant que ce script n'a pas tourné jusqu'au bout ; en cas d'échec systémique
(asset map ou GLB réellement absent), il libère la racine (`root.free()`)
et n'écrit rien, plutôt que de sauvegarder une scène partielle.

Le module décals/VFX documenté au §3.8 (`godot_decals_vfx_addition.gd`)
est **déjà fusionné mot pour mot** dans ce fichier — `_build_decals()` y
est identique à l'addition. Le patch a bien été appliqué à un moment de
l'historique ; il ne reste qu'une trace archivée de la méthode utilisée.

---

## 3. Fichier par fichier

### 3.1 `ue5_preprocess_detach_all.py` — Prétraitement (étape 0, manuel)

**Pourquoi il existe.** Le manifeste (étape 1) doit remonter, pour chaque
composant, la chaîne `attach_parent` afin de reconstruire un transform monde
quand `get_component_transform()` échoue (voir V10.3 en §7). Plus cette
chaîne est longue, plus il y a de points de défaillance. Ce script **supprime
le besoin de composer plus d'un maillon** en aplatissant tous les
attachements acteur→acteur *avant* le scan.

**Ce qu'il fait.** Pour chaque acteur du niveau : lit `get_attach_parent_actor()` ;
si un parent existe, appelle
`detach_from_actor(KEEP_WORLD, KEEP_WORLD, KEEP_WORLD)` explicitement sur les
3 règles (location/rotation/scale) — le défaut de l'API
(`KEEP_RELATIVE`) ferait sauter les acteurs à un autre endroit, donc **ne
jamais laisser les valeurs par défaut**.

**Ce qu'il NE fait PAS (volontairement)** :
- ne casse pas les LevelInstances (le containment LI n'est pas un attachement
  acteur — `detach_from_actor()` n'a aucun effet dessus). Cela doit être fait
  **manuellement via l'UI** (World Outliner → filtrer classe "LevelInstance" →
  sélectionner tout → clic droit → Level → Break → *répéter* car casser un LI
  peut en révéler d'autres imbriqués) ;
- ne touche pas aux Groupes d'acteurs (Ctrl+G, métadonnée d'éditeur pure,
  aucun effet sur la composition de transform) ;
- ne corrige pas les meshes mal orientés à l'origine (problème d'asset,
  pas de hiérarchie).

**Sécurité intégrée** : `DRY_RUN` (bool en tête de fichier) — première passe
= rapport seul, aucune modification. Recommandation explicite de travailler
sur une copie du niveau.

**Entrées/Sorties** : aucune donnée fichier — modifie l'état live de l'éditeur
Unreal. **Ce que ce script utilise** : uniquement l'API `unreal` native
(`EditorActorSubsystem`, `DetachmentRule`). **Ce qui l'utilise** : personne en
aval directement — c'est un prérequis manuel qui change l'état de la scène
que le manifeste scannera ensuite.

**Généralisable tel quel.** Complètement neutre vis-à-vis du contenu — aucune
référence au projet Necropolis. Candidat direct pour le framework, sans
modification. Seul point à généraliser : automatiser le "casser tous les LI"
qui reste actuellement 100% manuel (voir §9).

---

### 3.2 `unreal_export_manifest_v10-8.py` — Le scanner (étape 1, le cœur du système)

7609 lignes, aucun historique commenté (contrairement à une version antérieure
à 30318 lignes citée en mémoire — celle-ci est déjà "propre"). C'est de très
loin le fichier le plus important : toutes les autres étapes ne font que
*lire* ou *patcher* ce qu'il produit.

#### 3.2.1 Rôle et sortie

Scanne l'intégralité du niveau UE actuellement chargé/accessible et produit
`level_manifest_v10.json` — une **spécification complète et déclarative** de
la scène (transforms, hiérarchie, matériaux, textures, composants) mais
**sans aucun payload d'asset** (`asset_payload_included: false` dans le champ
`reconstruction` du manifeste — documenté explicitement). Aucune opération
destructive n'est effectuée sur la scène.

#### 3.2.2 Découverte des acteurs — la méthode "V4/V5/V6 éprouvée"

Un commentaire bloc marque explicitement **NE PAS REMPLACER** cette méthode :

```python
for actor in unreal.ObjectIterator(unreal.Actor):
    if actor.get_level() == level:
        result.append(actor)
```

au lieu de `world.get_current_level()`. Cette contrainte a survécu à 10
versions majeures — un signal fort qu'une approche plus "propre" en apparence
a été essayée et a échoué. **À ne jamais reconsidérer sans preuve nouvelle.**

Deux sources d'acteurs sont fusionnées et dédupliquées par `object_path()` :
- **acteurs directs** : `EditorActorSubsystem.get_all_level_actors()` (avec
  repli sur `EditorLevelLibrary` si le subsystem échoue) ;
- **contenu récursif des LevelInstances** : `collect_level_contents()`
  descend récursivement dans chaque `LevelInstance.get_loaded_level()`,
  jusqu'à `MAX_LEVEL_INSTANCE_DEPTH = 64`.

#### 3.2.3 Enregistrement canonique des LevelInstances

`register_level_instance()` maintient un registre unique **par actor_path**
(`level_instance_registry`), même si un LI est rencontré plusieurs fois
(occurrences vs. `unique_actor_paths` — les deux comptes sont exposés
séparément dans le diagnostic). Chaque entrée porte :
- `instance_chain` : liste des actor_paths depuis la racine jusqu'à ce LI
  inclus — **c'est cette chaîne, portée par CHAQUE placement/acteur/composant
  descendant, qui permettra plus tard de recomposer le transform monde final** ;
- `children_actor_paths` : liens parent→enfants pour reconstruire l'arbre ;
- `world_asset` : la sous-map source (permet de dédupliquer les LI qui
  pointent vers la même sous-scène) ;
- `encounter_count` / `encounter_sources` : traçabilité des doublons.

Le résultat alimente 3 structures dérivées mais synchronisées :
`level_instances.placements[]` (liste plate), `level_instances.hierarchy[]`
(arbre récursif via `build_hierarchy_node`), `level_instances.registry{}`
(dict canonique indexé par actor_path — **la source de vérité**, les deux
autres n'en sont que des projections).

#### 3.2.4 Classification (le point le plus fragile du système)

Deux fonctions classifient tout par **sous-chaîne de nom de classe** :

```python
def actor_category(actor):      # "level_instance" si "LevelInstance" in class_name
def component_kind(component):  # "decal" si "Decal" in class_name, etc.
```

C'est une heuristique par `in` sur des chaînes — fonctionne bien pour les
classes natives d'Unreal (`StaticMeshActor`, `PointLightComponent`,
`NiagaraComponent`, `DecalComponent`, `AudioComponent`...) mais **c'est le
point le plus fragile en cas de renommage de classe custom ou de nouvelle
version d'UE**. `is_blueprint_generated_actor()` détecte les Blueprints via
le suffixe `_C` du class_path + préfixe `/Game/` ou `/Plugin(s)/`.

#### 3.2.5 Récupération de transform — la partie la plus retravaillée

C'est le sous-système qui a subi le plus de correctifs (V10.1 → V10.8, voir
§7 pour la chronologie complète). Fonctions clés, dans l'ordre d'appel :

1. **`actor_transform(actor)`** — `get_actor_transform()`, **taux d'échec
   mesuré : 0 sur toute la scène**. C'est la fondation la plus fiable.
2. **`component_transform(component)`** — `get_component_transform()` brut,
   utilisé tel quel pour les composants Blueprint (`blueprint_component_detail`)
   et pour Audio/Particle (jamais passé au diagnostic — incohérence, voir §12).
3. **`component_transform_diagnostic(component, actor)`** — version "qui ne
   ment jamais" : retourne toujours `(valeur, erreur)` au lieu d'avaler
   l'exception. Utilisée pour StaticMesh/Decal/Niagara/Light/Audio. Logique :
   - si `get_component_transform()` réussit → utilisé directement ;
   - si `AttributeError` → tente un re-fetch de l'objet (staleness
     ObjectIterator, cas Blueprint dont le Construction Script régénère ses
     composants pendant le scan) via `_try_refetch_stale_component()` (essaie
     `unreal.find_object` puis `unreal.load_object` sur le path stable) ;
   - si le re-fetch échoue aussi → **repli sur les propriétés**
     (`component_transform_via_properties`), jamais sur la méthode.
4. **`component_transform_via_properties(component, actor, max_depth=32)`**
   — le repli final (V10.8, le plus récent). Remonte la chaîne
   `attach_parent` en lisant `relative_location/relative_rotation/
   relative_scale3d` via `get_editor_property()` (jamais via une méthode —
   ces classes n'exposent pas la méthode transform en Python, mais les
   UPROPERTY passent). **Point clé de la version finale** : la base n'est
   *jamais* le composant racine lui-même (son relative est l'identité sur un
   Blueprint — la position vit sur l'ACTEUR), mais toujours
   `actor_transform(actor)`, sur laquelle on compose *seulement* les
   relatives des composants non-racines rencontrés en remontant.

Chaque type de composant a sa propre fonction d'extraction
(`static_mesh_component_info`, `decal_component_info`,
`niagara_component_info`, `light_component_info`, `audio_component_info`),
mais toutes appellent `component_transform_diagnostic()` en interne et
stockent systématiquement `transform` + `transform_extraction_error` +
`actor_transform` (le dernier sert de filet de sécurité côté Godot).

**Cas des ISM/HISM** (Instanced/Hierarchical Instanced Static Mesh) :
`static_mesh_component_info` lit **chaque instance individuellement** via
`get_instance_transform(i, world_space=True)` (avec 3 signatures d'appel
essayées pour compatibilité inter-versions d'API), pas seulement le transform
du composant — sinon un ISM de 165 occurrences ne produirait qu'1 placement
Godot (bug V10.4 corrigé, voir §7).

#### 3.2.6 Composition des transforms monde — `finalize_v10_transforms()`

La fonction la plus critique du fichier. Composition Unreal **child \* parent**
(jamais l'inverse — voir V10.5 en §7) via l'API native `unreal.Transform` /
`unreal.KismetMathLibrary.compose_transforms` (repli sur l'opérateur `*`) —
**jamais de dérivation manuelle des signes de rotation**. Étapes, dans l'ordre :

1. Transforms finaux de chaque LevelInstance (`final_li_transforms`), en
   composant récursivement le long de `parent_level_instance` — **seule la
   LI la plus interne compte pour la composition de la chaîne** (voir V10.7,
   §7 — piège du double comptage) ;
2. Report de ces transforms sur `level_instances.placements[]` ;
3. Report récursif sur `level_instances.hierarchy[]` ;
4. Transform de reconstruction pour chaque acteur (`actors.records[]`) ;
5. **Le plus important** : `geometry.placements[]` — pour chaque placement,
   compose `level_instance_chain` + `transform` (composant) **et**
   séparément + `actor_transform` (`final_actor_world_transform`, filet de
   sécurité), puis **compose aussi individuellement chaque
   `instance_transforms[i]`** en `instance_final_world_transforms[]` (V10.4) ;
6. Systèmes spatiaux secondaires (lights/audio/niagara/decals) — même
   traitement générique via `compose_chain_transform()` ;
7. Composants de Blueprints (`blueprints.actors[].component_details[]`) ;
8. Vérification des références parent cassées ;
9. Diagnostics FX séparés (`ready_for_godot_fx`, par catégorie
   decals/niagara/lights/audio) — **volontairement distinct** de
   `ready_for_godot_geometry`, pour qu'un échec silencieux sur les décals ne
   soit jamais masqué par un statut global "ready" (voir V10.2, §7).

`compose_chain_transform(chain, source_transform, li_registry, diagnostics, context)`
est la fonction generic réutilisée partout : compose la chaîne de LI (du plus
externe au plus interne, via `LI_root * ... * LI_leaf`) puis compose
`source_transform` par-dessus (`source * chaîne_LI`). Utilise le
**transform local (`source_transform`) de chaque LI dans la chaîne, jamais
son `final_world_transform` déjà composé** — sinon double comptage garanti.

#### 3.2.7 Registres dédupliqués (mesh / matériau / texture)

Trois dicts globaux, clés par `object_path()` UE (identifiant stable) :
- `mesh_registry` : `mesh_info()` + `usage_count` + `instance_total`
  (somme des instances ISM/HISM, pas juste le nb de placements) +
  `materials[]` (paths référencés) ;
- `material_registry` : `material_full_info()` (inclut
  `material_instance_parameters()` — scalar/vector/texture params, parent
  chain) + `usage_count` + `mesh_references[]` ;
- `texture_registry` : `texture_info()` (dimensions, srgb, format,
  compression) + `material_references[]` + `parameter_references[]`
  (`{material, parameter}`).

`register_material_textures()` résout automatiquement les textures
référencées par un matériau (via `material_instance_parameters()`), y
compris quand la texture n'est pas chargeable (`unreal.load_object` échoue) —
dans ce cas, l'entrée de registre est construite depuis le dict déjà extrait
plutôt que d'être perdue.

#### 3.2.8 Diagnostics et contrat de "readiness"

Le fichier maintient une **doctrine de diagnostic** très déliberée : chaque
échec est catégorisé (échec réel vs. cas légitime). Exemple emblématique :
`empty_mesh_slot_count` (un StaticMeshComponent ancre/socket sans mesh
assigné — normal) est **explicitement séparé** de
`missing_mesh_references` (un mesh assigné mais introuvable dans le
registre — un vrai bug). Cette distinction évite les faux positifs qui
avaient pollué les comptes de bugs avant V10.1.

Le bloc `reconstruction` en fin de manifeste est le **contrat formel** que
consomme (ou devrait consommer) le reconstructeur : `ready_for_godot_geometry`
n'est vrai que si zéro échec de transform géométrie + zéro mesh manquant +
zéro échec LI + **zéro échec d'instance ISM/HISM** (V10.4 — avant cela, un
ISM à 0/458 instances placées pouvait quand même passer "ready").

**Constat après lecture du reconstructeur (§3.9)** : le `.gd` ne lit en
réalité **jamais** ce bloc `reconstruction` — ni `ready_for_godot_geometry`
ni `ready_for_godot_fx` n'apparaissent dans son code. Il refait sa propre
validation intégrale, indépendante, placement par placement
(`_build_geometry_placement`). Les deux côtés du pipeline calculent donc
chacun leur propre verdict de "prêt", sans jamais se référencer l'un
l'autre — une duplication qui pourrait diverger silencieusement si l'un des
deux évolue sans l'autre (voir §12).

**Ce que le fichier utilise** : uniquement l'API `unreal` (aucune dépendance
aux autres scripts du pipeline). **Ce qui l'utilise** : les 3 scripts
suivants (asset exporter lit `geometry.unique_meshes`, landscape et décals
lisent/patchent le manifeste entier), et en bout de chaîne, le
reconstructeur `.gd` (§3.9).

**Spécifique au projet vs généralisable** : la logique de scan, de
classification, de composition de transform et de diagnostic est **100%
générique** — aucune référence à Necropolis, aucun chemin d'asset en dur
dans ce fichier précis (contrairement aux 2 suivants). Seuls
`OUTPUT_PATH`/`TXT_OUTPUT_PATH` (`C:/Export/...`) sont en dur et à
externaliser en configuration.

---

### 3.3 `unreal_export_godot_assets_PATCHED.py` — Export des meshes (étape 2)

**Rôle.** Lit `geometry.unique_meshes` du manifeste et exporte chaque
StaticMesh unique en GLB via l'API native `unreal.GLTFExporter`
(`export_to_gltf`) — nécessite le plugin GLTFExporter activé côté UE. Produit
`ue5_godot_asset_map.json`, la **table de correspondance UE-path → fichier
Godot** que le reconstructeur résout pour charger chaque mesh.

**Pourquoi il ne réutilise pas simplement le nom de l'asset comme nom de
fichier** : deux assets de même nom court dans des dossiers différents
(`/Game/Env/Rocks/SM_Rock01` vs `/Game/Props/Misc/SM_Rock01`) collisionnaient
sur le même `.glb`, silencieusement marqués `EXISTING` alors que c'était le
mauvais mesh (bug corrigé : `unique_filename_for_path()` ajoute un hash SHA1
de 8 caractères du path UE complet au nom de fichier).

**Validation stricte du GLB produit** (pas seulement "le fichier existe") :
taille minimale (20 octets) **et** vérification du header magique `b"glTF"` —
un export tronqué à 0 octet était auparavant compté comme un succès. Les
messages d'avertissement de l'exporteur natif (`get_error_messages`, etc.,
API variable selon version) sont récupérés et propagés dans le JSON plutôt
que perdus.

**Idempotence** : si le fichier GLB existe déjà avec une taille non nulle, le
statut est `EXISTING` (pas de ré-export) — utile pour relancer le pipeline
sans tout regénérer, mais **attention** : cela ne détecte pas un mesh source
modifié depuis (pas de hash de contenu, seulement présence/taille).

**Ce qu'il utilise** : `level_manifest_v10.json` (lecture seule, vérifie
`manifest_version == "10.0"` et avertit sinon — mais continue quand même).
**Ce qui l'utilise** : le reconstructeur `.gd` (résout chaque mesh par
`ue_path`). **Portée volontairement limitée** : uniquement les StaticMesh —
**les SkeletalMesh (capturés dans le manifeste depuis V10.1,
`geometry.unique_skeletal_meshes`) ne sont PAS exportés par ce script**, gap
à combler pour la généralisation (§9).

**Généralisable tel quel**, à l'exception des chemins en dur
(`MANIFEST_PATH`, `OUTPUT_ROOT`, `GODOT_ROOT`) — aucune référence au projet X.

---

### 3.4 `unreal_export_landscape.py` — Le Landscape (étape 3, "script 4" selon son propre header)

**Pourquoi il existe** (le "problème nu", cité explicitement dans le
fichier) : le manifeste décrit le Landscape avec transform + bounds mais
**aucune géométrie ni texture** — c'est marqué HIGH risk. Or
`ALandscapeProxy::ExportToRawMesh()` existe en C++ mais **n'est pas exposé à
l'API Python**, et l'export GLB natif via le menu UE sur un Landscape est
connu pour produire un résultat cassé. La géométrie est donc reconstruite
**de l'extérieur**, par une méthode indépendante de toute API non exposée.

**Méthode géométrie — raycasting sur grille** :
`sample_heightfield()` trace verticalement `(GRID_RESOLUTION+1)²` rayons
(256×256 par défaut → 66049 tirs, ~20-60s) à travers toute la bounding box du
Landscape (+ ses `LandscapeStreamingProxy`). **Réutilise la même technique
que `auto_terrain_generator_ue55.py`** (`sample_landscape_heights`) —
**y compris sa liste d'ignorés** : un impact sur un prop posé sur le terrain
ajoute cet acteur à une ignore-list et retire le rayon, jusqu'à
`MAX_TRACE_RETRIES` (12). Une case non touchée par aucun rayon reste un
"trou" — le maillage n'émet un quad que si ses 4 coins ont été touchés
(`build_grid_mesh`), donc **les trous restent des trous plutôt que d'être
comblés par une hauteur inventée**.

**Méthode texture — bake orthographique** : un `SceneCapture2D` (spawné
temporairement, toujours détruit dans un bloc `finally` même en cas
d'échec) capture le Landscape **seul** (`PRM_USE_SHOW_ONLY_LIST`, avec 2
replis en cascade si cette propriété refuse l'écriture — jusqu'à cacher
manuellement tous les autres acteurs) en `SCS_BASE_COLOR` (albédo **non
éclairé**, choix délibéré : les props exportés ailleurs sont rééclairés par
Godot, baker l'éclairage UE ferait diverger les deux rendus). Format de
render target `RTF_RGBA8_SRGB` (avec repli sur linéaire + avertissement si
absent — sinon le PNG ressortirait délavé dans Godot qui l'interprète en
sRGB).

**Packaging — writer glTF binaire écrit à la main** (`write_glb()`) : buffer
unique, 5 bufferViews (positions/normales/UV/indices/PNG embarqué), 1
matériau PBR. **Aucune dépendance à une bibliothèque glTF externe** — juste
`struct` + `json`. Le PNG est **embarqué dans le binaire**, pas référencé en
fichier séparé.

**Convention d'axe — le point le plus sensible du fichier** :

```python
def AXIS_MAP(x, y, z):
    return (x * UE_CM_TO_GODOT_M, z * UE_CM_TO_GODOT_M, y * UE_CM_TO_GODOT_M)
# godot = (ue.x, ue.z, ue.y) * 0.01 — déterminant -1
```

Ce mapping **doit être bit-à-bit identique** à celui utilisé par
`_transform_from_v10()` dans le `.gd` (absent, voir §2). Le fichier documente
sa propre histoire : une V1 avec `(x, z, -y)` (déterminant +1) donnait un
rendu correct pour les objets symétriques (piliers, murs, tombes) mais
**faux pour les escaliers** — parce que les GLB produits par l'exporteur
glTF natif d'Unreal ont **déjà** la handedness inversée par cet exporteur.
Déterminant -1 → **le winding des triangles est inversé en conséquence**
dans `build_grid_mesh()` (`(a,b,c)` puis `(b,d,c)` au lieu de l'ordre
naturel) pour que les faces restent visibles depuis le dessus.
`AXIS_MAP_LABEL` (`"(ue.x, ue.z, ue.y) * 0.01"`) est **stocké dans le JSON**
mais **jamais vérifié automatiquement** contre la convention réellement
utilisée côté `.gd` — un futur désaccord serait à nouveau silencieux (voir
§10).

**Registration — patch, pas réécriture from scratch** : `register_in_asset_map()`
et `register_in_manifest()` lisent le JSON existant, retirent toute entrée
préexistante pour le path synthétique `/AutoTerrain/Landscape.BakedLandscape`
(idempotence en cas de relance), puis ajoutent la nouvelle. Le placement
manifeste injecté a un **transform identité** — cohérent puisque les sommets
du GLB sont **déjà** pré-convertis en espace Godot (contrairement à tous les
autres placements, dont la conversion se fait au niveau du `Transform3D` du
nœud, pas des sommets). Le script met aussi à jour le risque `LANDSCAPE`
existant dans `conversion_risks[]` (passe de `HIGH` à `INFO`).

**Ce qu'il utilise** : `level_manifest_v10.json` (lecture + réécriture),
`ue5_godot_asset_map.json` (lecture + réécriture), et implicitement le
matériau construit par `auto_terrain_generator_ue55.py` (c'est ce matériau
que la caméra de capture rend). **Ce qui l'utilise** : le reconstructeur
`.gd`, exactement comme n'importe quel autre mesh de l'asset map (aucune
branche spéciale requise côté `.gd` — conçu explicitement pour ça).

**Généralisable avec adaptation** : l'algorithme (raycast + bake + writer
GLB maison) est **totalement indépendant du projet X**. Seuls
`GRID_RESOLUTION`, `TEXTURE_RESOLUTION`, les chemins, et la convention d'axe
sont à externaliser. Limite structurelle actuelle : **un seul Landscape
"primaire" traité** (`find_landscape_actors()` prend `landscapes[0]` avec un
avertissement si plusieurs Landscape existent — les proxies de streaming
sont bien tous inclus, mais pas des Landscapes multiples indépendants).

---

### 3.5 `auto_terrain_generator_ue55.py` — Générateur de matériau de terrain (HORS pipeline d'export)

**Ce fichier n'est PAS une étape de conversion.** C'est un outil
d'**authoring côté Unreal** : il construit procéduralement, nœud par nœud
via `MaterialEditingLibrary`, le graphe de matériau du Landscape
(`M_AutoTerrain` / instance `MI_AutoTerrain`) — mélange de textures Necropolis
et Quixel selon l'altitude, la pente, du bruit, des couches de peinture
manuelle (`PAINT_LAYERS`), un look "rocher" biplanaire, un remplissage de
boue, etc. v9 au moment de la lecture (historique v4→v9 documenté en
commentaire de tête, contrairement au manifeste qui a purgé son historique).

**Son seul point de contact avec le pipeline de conversion** : c'est *ce*
matériau (assigné au Landscape via `assign_material()`) que
`unreal_export_landscape.py` **photographie** avec son SceneCapture2D pour
produire `BakedLandscape_BaseColor.png`. Sans ce script (ou un équivalent),
le Landscape UE afficherait son matériau par défaut et le bake serait vide
de sens visuellement — mais le script d'export fonctionnerait quand même
techniquement avec n'importe quel matériau assigné.

**Pattern partagé notable** : `sample_landscape_heights()` /
`levels_from_heights()` utilise **exactement** la même technique de
raycasting en grille avec ignore-list que `unreal_export_landscape.py`
(implémentée deux fois indépendamment — candidat de factorisation, §6.5),
pour **mesurer** les niveaux d'altitude (plateau bas / piedmont / bande
rocheuse) par analyse statistique de la distribution des hauteurs
(histogramme lissé, recherche du mode dans la fraction basse — "surface la
plus peuplée", pas juste le point le plus bas).

**Entièrement spécifique au projet X** : références en dur au pack
Necropolis (`SETS`, chemins `/Game/Necropolis/...`), aux assets Quixel
(`/Game/AutoTerrain/...`), aux noms de layers de peinture
(`Paint_RockySand`, etc.), aux réglages esthétiques (teintes, seuils).
**Aucune part de ce fichier ne devrait entrer dans le framework général** —
sinon, au mieux, comme *exemple* de plugin de génération de matériau que
l'utilisateur pourrait fournir/remplacer pour sa propre map. Le point à
retenir pour le framework n'est pas le contenu, mais le **contrat** : "avant
d'exporter le Landscape, le Landscape doit avoir un matériau qui produit le
rendu voulu — la génération de ce matériau est hors du scope de l'export."

---

### 3.6 `unreal_export_decals_vfx.py` — Décals et VFX (étape 4)

**Pourquoi il existe** (problème nu) : le manifeste décrit déjà 1423 décals
et 422 composants Niagara avec transform/taille/matériau/système corrects,
mais **rien ne matérialisait cette description** — aucune texture écrite,
aucun nœud créé côté Godot. Ce script ferme la moitié "décal" de ce trou et
donne aux VFX un atterrissage honnête (placement seul, jamais converti).

**Décals — résolution de texture** : sur ce projet, seulement 5 matériaux de
décal distincts couvrent les 1423 placements, et les instances **n'overrident
aucune texture** — tout vient des valeurs par défaut du matériau parent
partagé (`M_Decals_01`). `resolve_texture_parameter()` cherche dans l'ordre :
(1) override explicite sur l'instance, (2)
`get_material_default_texture_parameter_value()` sur le parent pour une
liste de noms candidats (`MASK_PARAMETER_NAMES`, `NORMAL_PARAMETER_NAMES` —
**noms de paramètres en dur, dépendants de la convention de nommage du
pack**), (3) scan de tous les noms de paramètres texture du parent en
cherchant une sous-chaîne correspondante. Le tint vient de
`TINT_PARAMETER_NAMES` (`"Tint 02"`, `"Tint 01"`, etc.) avec repli sur blanc
neutre si rien n'est trouvé (delibérément visible plutôt que silencieusement
sombre).

**Extraction des pixels — même contrainte que le bake du Landscape** :
aucune API Python UE ne donne accès aux pixels bruts d'une texture. Solution
identique en substance à `bake_base_color()` : matériau temporaire "unlit"
(`MSM_UNLIT`) échantillonnant la texture cible, branché en Emissive,
`draw_material_to_render_target()`, puis `export_render_target()` en PNG.
Le matériau temporaire est **toujours supprimé** après usage
(`unreal.EditorAssetLibrary.delete_asset`), y compris en cas d'échec.

**Composition RGBA — le rôle de `png_codec.py`** : le masque exporté est un
PNG en niveaux de gris (couverture). Un Decal Godot veut une texture RGBA où
la **couleur** vient du Tint du matériau et l'**alpha** de la couverture du
masque — ni l'API UE ni PIL (indisponible dans l'éditeur) ne composent deux
images, d'où le module `png_codec.py` chargé dynamiquement
(`exec(compile(...))`, **doit être physiquement dans le même dossier** que ce
script — chargement par chemin relatif, pas par import de package).

**Détection de masque plat** : `compose_tinted_rgba()` retourne
`(largeur, hauteur, alpha_moyen, alpha_min, alpha_max)`. Si `min == max`, le
décal serait un **rectangle plein** plutôt qu'une tache — signalé bruyamment
(`entry["warning"]`) plutôt que silencieusement livré comme si c'était
normal.

**VFX (Niagara) — position claire et documentée** : *"un système Niagara
n'est pas convertible"* — comportement dans des modules propriétaires sans
équivalent Godot. Seul le **placement** est exporté (`effects.niagara[]` du
manifeste, déjà présent), groupé par nom de système dans le JSON de sortie
(`vfx.systems{name: count}`) pour que les 341 `NS_candle_flame` (sur 422
systèmes) puissent être reconstruits en masse plus tard côté Godot.

**Ce qu'il utilise** : `level_manifest_v10.json` (lecture seule —
`effects.decals`, `effects.niagara`), `png_codec.py` (chargement dynamique).
**Ce qui l'utilise** : le reconstructeur `.gd`, via `godot_decals_vfx_addition.gd`.

**Spécifique au projet X** : les listes de noms de paramètres
(`MASK_PARAMETER_NAMES`, `TINT_PARAMETER_NAMES`, `NORMAL_PARAMETER_NAMES`)
sont des heuristiques de convention de nommage propres au pack utilisé —
**premier vrai point de configuration par pack/projet** à exposer dans le
framework (une autre map avec un autre pack de décals aura d'autres noms de
paramètres). Le reste (résolution en cascade, bake par render target,
composition RGBA, détection de masque plat) est générique.

---

### 3.7 `png_codec.py` — Codec PNG minimal pur Python

**Pourquoi il existe** : ni l'API UE ni PIL ne permettent de composer deux
images ensemble depuis l'éditeur. Ce module lit/écrit du PNG 8-bit non
entrelacé, types de couleur 0/2/4/6 (gris / RGB / gris+alpha / RGBA) — **tout
ce que produit `export_render_target()`**. Toute autre variante lève une
exception explicite plutôt que de retourner une image fausse silencieusement.

**Contenu** : lecteur PNG complet avec dé-filtrage des 5 types de filtre PNG
(None/Sub/Up/Average/Paeth, algorithme `_paeth()` standard), writer RGBA
simple (filtre None uniquement — image petites, déjà bien compressées par
zlib), `luminance_at()` (formule de luminance perceptuelle standard
0.299/0.587/0.114), et la fonction métier `compose_tinted_rgba()` qui teinte
un masque de couverture avec une couleur RGB.

**Ce qu'il utilise** : seulement `struct` + `zlib` (stdlib pure, zéro
dépendance externe). **Ce qui l'utilise** : `unreal_export_decals_vfx.py`
exclusivement, actuellement — mais **c'est le module le plus indépendant et
le plus directement réutilisable de tout le pipeline**, sans aucune
modification, pour n'importe quel besoin futur de composition d'image côté
Unreal (aucune référence à Unreal elle-même à l'intérieur : pas d'`import
unreal`, testable en dehors de l'éditeur).

**100% générique, aucune trace de projet X.** Premier candidat pour devenir
un utilitaire du framework, tel quel.

---

### 3.8 `godot_decals_vfx_addition.gd` — Patch d'addition côté Godot (archive historique)

**Statut** : ce patch a depuis été **appliqué et fusionné** dans
`ue5_godot_map_constructor_v10_PATCHED.gd` (§3.9) — `_build_decals()` y est
identique, au caractère près, à ce qui est décrit ici. Cette section reste
utile pour comprendre **la méthode** employée, pas pour retrouver du code
qui resterait à intégrer.

**Ce n'est pas un script autonome** — c'était un bloc de code **à coller
manuellement** dans le fichier principal, avec des instructions étape par
étape en commentaire (const à ajouter, compteurs `stats` à ajouter, appels à
insérer dans `_run()` et `_print_report()`, fonctions à coller en fin de
fichier). Choix délibéré documenté : *"rien à supprimer : ton correctif de
quaternion et ta conversion d'axes restent intacts, ce bloc réutilise
`_transform_from_v10()` telle quelle"* — écrit pour **ne jamais toucher** à
la logique de conversion déjà validée et fragile du fichier principal.

**`_build_decals()`** : charge `ue5_godot_decal_map.json`, précharge **une
seule fois par matériau** chaque texture (partagée par les 781 placements
`MI_decal_leak_01`, par exemple, au lieu de 781 chargements identiques),
crée un nœud `Decal` par placement avec `decal.transform` recalculé via
`_transform_from_v10()` (orthonormalisé — `transform.basis.orthonormalized()`
— pour ne jamais accumuler de scale involontaire dans la base).

**Point le plus subtil du fichier — la boîte de décal** : Unreal projette
un décal le long de son axe X propre, avec une échelle `[épaisseur, largeur,
hauteur]` sur un cube de base 256 unités ; Godot projette le long de `-Y`
avec `size = (largeur, hauteur, profondeur)`. Comme `_transform_from_v10()`
a déjà tourné le nœud selon la convention d'axe globale, **seul l'ordre des
composantes de taille doit être réarrangé ici** — pas une deuxième rotation :

```gdscript
decal.size = Vector3(width, thickness, height)  # noter l'ordre : (w, thickness, h)
```

**`_build_vfx_markers()`** : crée un `Marker3D` par composant Niagara,
groupé par nom de système sous un nœud racine nommé de façon très explicite
`VFX_MARKERS_NOT_CONVERTED` — l'intention (ne jamais faire croire qu'un VFX
a été "converti") est portée jusque dans le nommage du nœud runtime, pas
seulement dans la documentation.

**Métadonnées** (`set_meta`) systématiquement attachées si
`KEEP_PLACEMENT_METADATA` : `ue_actor_path`, `ue_component_path`,
`ue_material_path` / `ue_niagara_system`, permettant une traçabilité
complète Godot → Unreal après reconstruction (utile pour du debug ou une
édition manuelle post-import).

**Ce qu'il utilise (dépendances vers le fichier principal)** :
`_transform_from_v10()`, `UE_CM_TO_GODOT_M`, `_safe_node_name()`,
`KEEP_PLACEMENT_METADATA`, `manifest` (variable), `stats` (dict) — toutes
confirmées présentes exactement sous ces noms dans `ue5_godot_map_constructor_
v10_PATCHED.gd` (§3.9). **Ce qui l'utilise** : rien d'autre — c'est une
feuille du graphe de dépendances.

**Généralisable** : le pattern (précharger les textures uniques, un nœud
par placement, group-by pour les FX non convertis, métadonnées de
traçabilité) est générique. Le détail de réarrangement des axes de la boîte
de décal est spécifique à la sémantique "Decal" d'Unreal vs. Godot — mais
c'est une conversion **de format**, pas de projet, donc généralisable une
fois isolée dans une fonction dédiée.

---

### 3.9 `ue5_godot_map_constructor_v10_PATCHED.gd` — Le reconstructeur Godot (le cœur de la moitié "destination")

2283 lignes, `@tool extends EditorScript`, version interne **V10.12**. C'est
la pièce qui manquait à la première lecture — désormais lue intégralement.
C'est le seul point du pipeline qui **écrit réellement une scène Godot** ;
tout ce qui précède (les 8 fichiers UE) ne produit que des données et des
assets en attente d'être consommés.

#### 3.9.1 Mode d'exécution — une contrainte structurante

`extends EditorScript` signifie que ce fichier ne s'exécute **que dans le
contexte de l'éditeur Godot**, lancé manuellement (panneau FileSystem, clic
droit sur le script → *Run*). Ce n'est ni un autoload, ni un script de jeu,
ni — par défaut — quelque chose qu'un orchestrateur externe peut déclencher
par une simple invocation en ligne de commande sans passer par l'éditeur
(voir §10 et §11 pour les implications concrètes sur la conception de
l'orchestrateur).

`_run()` orchestre tout le fichier dans l'ordre suivant : `_load_inputs()`
→ `_validate_manifest_and_asset_map()` → boucle sur
`geometry.placements[]` (`_build_geometry_placement()`) → `_build_decals()`
→ `_build_vfx_markers()` → `_build_li_metadata_nodes()` → `PackedScene.pack()`
→ `ResourceSaver.save()`. Si le pack ou la sauvegarde échoue, la racine est
libérée (`root.free()`) et rien n'est écrit — pas de scène partielle
silencieuse.

#### 3.9.2 Validation d'entrée — indépendante de celle du manifeste

`_validate_manifest_and_asset_map()` vérifie que `manifest_version` et
`asset_map["manifest_version"]` commencent tous les deux par `"10"`, puis
que **chaque** `ue_path` de `geometry.unique_meshes` a une entrée dans
`asset_map["assets"]` — jamais de repli par nom de fichier ou de devinette,
uniquement une résolution exacte. Un `exported_count` déclaré mais
incohérent avec la taille réelle de `assets` déclenche un `push_warning`,
pas un blocage.

**Constat important** : ce script **ne lit jamais**
`manifest["reconstruction"]["ready_for_godot_geometry"]` ni
`ready_for_godot_fx` — le contrat formel calculé côté Python n'est jamais
consulté côté Godot. Le reconstructeur refait sa propre validation
complète, indépendamment, placement par placement. Les deux moitiés du
pipeline ont chacune leur propre notion de "prêt", qui pourraient diverger
sans qu'aucun code ne le détecte (voir §12).

#### 3.9.3 `_build_geometry_placement()` — la boucle centrale, avec échec catégorisé

Reprend exactement la philosophie de diagnostic du manifeste (§3.2.8,
§6.3) côté Godot : un `enum Outcome { OK, SKIPPED, FATAL }` et **5 flags de
tolérance par catégorie d'échec**, pas un seul flag générique :

```gdscript
const FAIL_ON_MISSING_ASSET_MAPPING := true   # problème SYSTÉMIQUE -> abort
const FAIL_ON_MISSING_GLB := true             # problème SYSTÉMIQUE -> abort
const FAIL_ON_TRANSFORM_FAILURE := false      # problème ISOLÉ -> skip + compte
const FAIL_ON_INSTANTIATE_FAILURE := false    # problème ISOLÉ -> skip + compte
const FAIL_ON_DUPLICATE_PLACEMENT_ID := false # problème ISOLÉ -> skip + compte
```

Commentaire de tête explicite sur le bug que ce découpage corrige (V10.4) :
un seul échec connu et déjà documenté par le manifeste (un des 14 cas de
composant périmé, par exemple) **avortait la reconstruction entière avec
zéro `.tscn` produit**, parce qu'un unique couple de flags catch-all était
vérifié pour *toute* raison d'échec, pas seulement les deux vraiment
systémiques. Depuis, seuls asset-map/GLB réellement absents abortent par
défaut ; le reste est ignoré avec un avertissement compté — **99,8 % de
bonnes données ne sont plus jetées à cause d'une poignée de cas limites
connus**.

Pour chaque placement, la fonction résout d'abord `mesh.path` (slot vide →
skip comptabilisé, cohérent avec `empty_mesh_slot_count` du manifeste),
puis distingue **statique vs instancié** :
- `static_mesh` → un seul transform (`reconstruction_transform`, repli sur
  `final_world_transform`) ;
- `instanced_mesh` / `hierarchical_instanced_mesh` → lit
  `instance_final_world_transforms[]` (le tableau par-instance ajouté par
  le manifeste en V10.4) et **spawne un nœud par instance réelle**, avec
  repli gracieux sur le transform unique du composant si ce tableau est
  absent (manifeste pré-V10.4) — dégradation, pas échec.
- Un ISM/HISM à `instance_count <= 0` est **skippé explicitement**
  (`zero_instance_ism_skipped`), pour ne jamais spawner un objet fantôme à
  la transform du composant sur une liste d'instances réellement vide.

Chaque instance spawnée reçoit une clé de placement unique
(`base_placement_id` ou `..._INST_<i>` si multi-instance), vérifiée contre
`seen_placement_keys` pour détecter les doublons.

#### 3.9.4 La fonction pivot : `_transform_from_v10()` + conversion de repère

Code exact (le cœur de tout le pipeline de reconstruction) :

```gdscript
func _transform_from_v10(data: Dictionary):
    # ... location/rotation/scale extraits du dict UE ...
    var ue_basis := _unreal_rotator_to_basis(pitch, yaw, roll)
    var converted_basis := _convert_basis_ue_to_godot(ue_basis)
    converted_basis = converted_basis.scaled(ue_scale)
    var godot_pos := Vector3(ue_pos.x, ue_pos.z, ue_pos.y) * UE_CM_TO_GODOT_M
    return Transform3D(converted_basis, godot_pos)
```

Trois fonctions s'y articulent :

- **`_unreal_rotator_to_basis(pitch, yaw, roll) -> Basis`** — reconstruit le
  quaternion d'Unreal à partir de pitch/yaw/roll **avec les signes exacts de
  `FRotator::Quaternion()`** :
  ```gdscript
  var qx := cr * sp * sy - sr * cp * cy
  var qy := -cr * sp * cy - sr * cp * sy   # signe négatif : le correctif V10.7
  var qz := cr * cp * sy - sr * sp * cy
  var qw := cr * cp * cy + sr * sp * sy
  ```
  Commentaire de tête : *"pour une rotation yaw-seul, l'inversion s'annule —
  c'est pourquoi les bâtiments avaient l'air corrects ; tout ce qui avait du
  pitch ou du roll ressortait en miroir."* Exactement la signature du bug
  décrit en §7 (V10.7) — confirmée ici dans le code final.
- **`_convert_basis_ue_to_godot(ue_basis) -> Basis`** — construit la base de
  conversion `C` explicitement (`Vector3(1,0,0), Vector3(0,0,1),
  Vector3(0,1,0)` — Gx=Ux, Gy=Uz, Gz=Uy, déterminant -1) et calcule
  `C * ue_basis * C.inverse()`, **jamais** une permutation d'angles d'Euler —
  exactement la méthode que la personne avait exigée dès le départ
  (§13.A.4) : *"pas en dérivant les signes à la main"*. Commentaire de tête :
  *"matching Unreal's own glTF exporter convention — verified against the
  actual mesh exports, not just reasoned about in the abstract"* — la trace
  écrite, dans le code final, de la leçon la plus chère du projet (§13.A.5).
- **`godot_pos = (ue.x, ue.z, ue.y) * 0.01`** — identique bit à bit à
  `AXIS_MAP` dans `unreal_export_landscape.py` (§3.4, §6.4). Les deux côtés
  du pipeline sont bien synchronisés à l'heure de cette lecture — mais
  toujours sans vérification croisée automatique (le risque documenté au
  §10 reste valable pour toute évolution future).

Un commentaire de code confirme littéralement le piège d'inférence de type
documenté en §13.B.8 : *"`_transform_from_v10()` returns Transform3D OR
null, so it has no single declared return type - `:=` cannot infer one.
Plain `var` keeps it a Variant"* — la fonction est délibérément déclarée
sans type de retour et appelée avec `var` (jamais `:=`) à chaque site
d'appel, en connaissance de cause.

#### 3.9.5 Décals — voir §3.8 (fusionné mot pour mot)

`_build_decals()`, `UE_DECAL_BASE_SIZE`, `DECAL_MAP_PATH`, `BUILD_DECALS`
sont présents ici exactement comme documenté au §3.8. Une constante
supplémentaire existe, **`DECAL_SIZE_SCALE := 0.9`**, documentée comme
*"multiplicateur global sur l'empreinte du décal... 1.0 = taille UE
exacte"* — mais **jamais référencée nulle part ailleurs dans le fichier**
(voir §12, incohérence latente).

#### 3.9.6 VFX — la partie la plus retravaillée du fichier (V10.9 → V10.12)

De très loin la section la plus longue (environ 1000 lignes). Contrairement
aux décals (conversion fidèle d'une donnée qui existe), les VFX Niagara
**n'ont pas d'équivalent Godot direct** — cette section construit un
**système de substitution visuelle**, explicitement assumé comme tel, pas
comme une conversion.

**Catégorisation par mot-clé** (`_vfx_category_for_name`) : 12 catégories
(candle, torch, fire, smoke, steam, spark, swarm, blood, magic, dust,
water, leaves, snow) + un repli `generic` délibérément terne. Le code
porte lui-même le commentaire de la leçon documentée en §13.B.16 : *"Order
matters: 'candle' is tested before 'flame' so NS_candle_flame does not get
the bonfire preset."*

**Physique réduite pour les catégories qui en ont besoin** (fire/smoke/
steam/candle/torch) : 8 fonctions de loi (`_plume_height_law` en `t^1.5`,
`_plume_width_law`, `_plume_density_law` en dilution `(z-z0)^(-5/3)`,
`_smoke_scale_law`, `_fire_scale_law`, `_candle_scale_law`,
`_fire_density_law`, `_turbulence_influence_law`) — un modèle de panache
flottant à ordre réduit, pas un solveur CFD, référencé en commentaire à un
document externe (`VFX_Physics_Reference_Godot_V10_8.md`, **non fourni
parmi les fichiers lus**). `_curve_from_law()` échantillonne chaque loi en
`CurveTexture` (12 points par défaut) — **et corrige explicitement le
piège documenté en §13.B.9** : `curve.max_value` est recalculé depuis
l'amplitude réelle de la loi (`maxf(1.0, highest)`) au lieu de rester au
défaut 1.0, avec le commentaire *"a law reaching 2.75 (the smoke scale)
would be silently flattened at the default 1.0"* — preuve que la leçon a
bien été encodée en garde-fou, pas seulement racontée.

**Ressources partagées par catégorie** (`_vfx_resources()`, mise en cache
dans `_vfx_cache`) : `ParticleProcessMaterial`, `QuadMesh`, matériau de
dessin — construits **une fois par catégorie**, jamais par instance. C'est
la correction directe du problème des ~2500 ressources/shaders documenté
en §13.B.11.

**Chemin de rendu par défaut sans shader** : `_falloff_texture()` génère un
disque radial via `GradientTexture2D` (opaque au centre, transparent au
bord), combiné à `StandardMaterial3D` avec `vertex_color_use_as_albedo` et
`billboard_mode = BILLBOARD_PARTICLES`. **Rien ici ne peut échouer à
compiler** — la correction directe du bug "carrés blancs" de §13.B.12. Un
shader de déformation plus riche (`_parcel_shader()`, ~400 lignes de GLSL
construisant une flamme à partir de 5 "lobes" elliptiques qui convergent
vers une pointe, avec turbulence FBM) existe et reste disponible, mais est
**explicitement désactivé par défaut** (`VFX_USE_PARCEL_SHADER := false`) —
l'historique de conversation (§13.B.12) documentait déjà cette constante
comme "censée être à false" ; à cette version, **elle l'est effectivement**
(un désaccord entre intention documentée et valeur réelle avait existé
temporairement — voir l'entrée "V10.11" dans l'en-tête du fichier lui-même,
qui décrit et corrige exactement ce décalage).

**Budget de lumières temps réel** (`_vfx_may_add_light` / `VFX_MAX_LIGHTS
:= 24` / `VFX_LIGHT_MIN_SPACING := 6.0`) : empêche les 341 flammes de
bougies de produire 341 `OmniLight3D` — correction directe de §13.B.15.

**Nouveauté non documentée dans les conversations lues (V10.12)** : une
**surcouche d'"embers" (braises)**. Les catégories `fire`/`torch` reçoivent
un **second émetteur séparé**, réutilisant la physique déjà correcte de la
catégorie `spark`, posé par-dessus le corps de flamme désormais
délibérément quasi-statique à la source. Raison documentée en tête de
fichier : à l'ancien réglage (vitesse jusqu'à 2,25 m/s, flottabilité 0,70,
durée de vie 1,15 s) une flamme unique parcourait ~3 m avant de s'éteindre —
un problème visuel concret ("une colonne de feu qui atteint la canopée
au-dessus d'un brasier"). La solution retenue n'est pas de réduire
uniformément le mouvement (ce qui tuerait la sensation de chaleur montante)
mais de **séparer les responsabilités entre deux émetteurs** : le corps de
flamme reste ancré, les braises/cendres portent seule la sensation de
montée. `candle` est explicitement exclu de cette surcouche (une flamme à
l'échelle d'une bougie ne projette pas de braises visibles).

**Calibration de hauteur de flamme** (`_vfx_flame_height_offset`) : le
transform d'un marqueur Niagara est l'origine acteur/composant exportée
depuis Unreal — pour un torch/candle, presque toujours la **base** du mesh
(le pivot est au sol/au socket, pas à la flamme). Sans correction, chaque
flamme apparaîtrait au pied du prop. Un décalage le long de l'axe "haut"
local du marqueur (`transform.basis.y`, **non normalisé** exprès, pour
porter l'échelle propre de l'instance) est appliqué avant toute création de
particule/lumière. Valeurs de référence (`VFX_TORCH_ASSUMED_HEIGHT_M
:= 1.40`, ratio 0.78 ; `VFX_CANDLE_ASSUMED_HEIGHT_M := 0.05`, ratio 1.0) sont
explicitement documentées comme des **estimations, pas des mesures** —
faute d'accès aux bounds réels du GLB par instance depuis cette passe VFX :
*"if the flame still isn't at the right height after a rebuild, that's a
calibration problem, not a direction problem — measure the real torch/
candle mesh height in Unreal (in cm) and set ASSUMED_HEIGHT_M to that
number / 100."*

**`_try_set()`** : utilisé systématiquement pour toute propriété de
particule dont le nom a varié entre versions mineures de Godot 4
(`velocity_pivot`, `turbulence_*`, `use_scale_3d`, `rotation_3d_*`,
`transform_align`, `radial_velocity_*`) — une écriture directe non protégée
casserait tout le constructeur sur un éditeur plus ancien qui n'expose pas
encore la propriété.

#### 3.9.7 Ce que ce fichier utilise / ce qui l'utilise

**Utilise** : les 3 JSON produits côté Unreal (`level_manifest_v10.json`,
`ue5_godot_asset_map.json`, `ue5_godot_decal_map.json`), les GLB sous
`res://UEAssets/Meshes/`. Aucune dépendance à un addon Godot tiers — tout
repose sur l'API native (`ParticleProcessMaterial`, `GradientTexture2D`,
`Decal`, `GPUParticles3D`, `PackedScene`, `ResourceSaver`).
**Ce qui l'utilise** : personne — c'est la feuille terminale de tout le
pipeline, le point où les données deviennent une scène jouable.

#### 3.9.8 Généralisable vs spécifique au projet X

**Générique et réutilisable tel quel** : toute l'architecture de
`_build_geometry_placement` (gestion ISM/HISM, flags de tolérance par
catégorie, dédoublonnage par placement_id), `_transform_from_v10` +
les 3 fonctions de conversion d'axe/quaternion (le cœur mathématique du
fichier), le mécanisme de ressources VFX partagées par catégorie, le
pattern `_try_set`, le budget de lumières, la séparation "corps de flamme
statique + surcouche embers".

**Spécifique au projet X (Necropolis)** : les valeurs numériques précises
des 12 presets VFX (couleurs, durées de vie, vitesses — réglées pour ce
cimetière), les mots-clés de catégorisation eux-mêmes (adaptés au
vocabulaire de nommage `NS_*` de ce projet), les constantes de calibration
`VFX_TORCH_ASSUMED_HEIGHT_M` / `VFX_CANDLE_ASSUMED_HEIGHT_M` (mesurées sur
les meshes de ce pack), le nom de scène de sortie `Map--_REBUILT.tscn`.

---

## 4. Le contrat de données central : `level_manifest_v10.json`

C'est LE schéma à figer/versionner explicitement pour le framework — tout
le reste du pipeline (et le futur reconstructeur généralisé) en dépend.
Clés de premier niveau, avec ce qu'elles contiennent :

| Clé | Contenu | Écrit par | Patché par |
|---|---|---|---|
| `manifest_version` | `"10.0"` (jamais incrémenté malgré V10.1→V10.8, voir §12) | manifest | — |
| `exporter` | name/version/engine/world_resolution | manifest | — |
| `world` | path/name/class du niveau UE | manifest | — |
| `actors` | `records[]` (tous les acteurs, `actor_basic_info`), compteurs par catégorie/origine | manifest | — |
| `level_instances` | `placements[]`, `hierarchy[]` (arbre), `registry{}` (canonique, clé=actor_path), `unique_world_assets{}` | manifest | — |
| `geometry` | `unique_meshes{}`, `placements[]` (LE plus gros volume), `unique_skeletal_meshes{}`, `skeletal_mesh_placements[]` | manifest | landscape (append 1 placement + 1 mesh) |
| `materials` | `unique_materials{}` (params scalar/vector/texture, parent chain) | manifest | — |
| `textures` | `unique_textures{}` (dimensions, format, refs) | manifest | — |
| `blueprints` | `actors[]` (détail composant par composant), compteurs | manifest | — |
| `effects` | `niagara[]`, `decals[]`, `particles[]` | manifest | — |
| `world_features` | `landscape[]`, `lights[]`, `foliage[]`, `audio[]`, `environment[]` | manifest | — |
| `world_partition` | detected/system_actor_count | manifest | — |
| `asset_inventory` | comptes uniques par type d'asset | manifest | — |
| `conversion_diagnostic` | ~30 compteurs + `v10_transform_diagnostic` (détails d'échec) | manifest | — |
| `conversion_risks[]` | par catégorie (LEVEL_INSTANCES/LANDSCAPE/NIAGARA/BLUEPRINTS/STATIC_MESH/DECALS/LIGHTING/TEXTURE_EXTRACTION), severity HIGH/MEDIUM/INFO | manifest | landscape (repasse LANDSCAPE en INFO) |
| `warnings[]` | WORLD_PARTITION/LEVEL_INSTANCE_DUPLICATES/TRANSFORM_CONTEXT/ASSET_DEDUPLICATION | manifest | — |
| `reconstruction` | **le contrat formel de "prêt pour Godot"**, géométrie vs FX séparés | manifest | — |

**Chaque placement de géométrie** (`geometry.placements[]`) porte, après
finalisation : `kind`, `actor{}`, `component{}`, `source_level`,
`level_instance_chain[]`, `mesh` (bounds/lod/collision/nanite/material_slots),
`transform` (source), `transform_extraction_error`, `actor_transform`,
`materials[]`, `mobility`, `collision{}`, `instance_count`,
`instance_transforms[]`, `instance_transform_errors[]`, puis après
`finalize_v10_transforms()` : `placement_id` (hash stable), `source_transform`,
`final_world_transform`, `reconstruction_transform` (= final_world_transform,
redondant intentionnellement — nom "métier" vs nom "technique"),
`transform_space` (`"world"` ou `"composed_world"`), `final_actor_world_transform`,
`instance_final_world_transforms[]`.

**Chaque entrée du registre LevelInstance** porte : identité complète +
`world_asset`, `loaded`/`loaded_level`, `transform`, `parent_level_instance`,
`parent_level_instance_chain[]`, `instance_chain[]`, `depth`,
`encounter_count`/`encounter_sources[]`, `children_actor_paths[]`, `status`
(`LOADED`/`NOT_LOADED`), puis après finalisation : `placement_id`,
`parent_placement_id`, `source_transform`, `final_world_transform`,
`reconstruction_transform`, `transform_space`.

**`transform` (le dict de base, partout dans le fichier)** a toujours la
forme :
```json
{"location": [x, y, z], "rotation": {"pitch": p, "yaw": y, "roll": r}, "scale": [sx, sy, sz]}
```
en **unités et convention Unreal natives** (centimètres, FRotator
pitch/yaw/roll). La conversion vers Godot (cm→m, axes, quaternion) n'a
**jamais lieu côté Unreal** — elle est **entièrement déléguée au
reconstructeur `.gd`**, via `_transform_from_v10()`. C'est un choix
d'architecture explicite : le manifeste reste une trace fidèle de la scène
source, indépendante du moteur cible.

---

## 5. Les deux autres fichiers d'échange

### `ue5_godot_asset_map.json`
```json
{
  "exporter_version": "1.0", "manifest_version": "10.0", "format": "glb",
  "godot_asset_root": "res://UEAssets", "godot_mesh_root": "res://UEAssets/Meshes",
  "source_manifest": "...", "unique_mesh_count": N, "exported_count": N,
  "existing_count": N, "failure_count": N,
  "assets": {
    "<ue_path>": {
      "ue_path": "...", "ue_name": "...", "godot_path": "res://UEAssets/Meshes/Name_HASH.glb",
      "disk_path": "...", "format": "glb", "status": "EXPORTED|EXISTING",
      "export_warnings": "..."  // optionnel
    }
  },
  "failures": [{"ue_path": "...", "reason": "..."}],
  "notes": ["..."]
}
```
Le Landscape y ajoute une entrée synthétique sous la clé
`/AutoTerrain/Landscape.BakedLandscape`, avec des champs additionnels
(`source`, `axis_map`, `vertex_count`, `triangle_count`, `note`) absents des
entrées normales — **le reconstructeur doit tolérer des champs optionnels
par asset**, pas assumer un schéma rigide identique pour toutes les entrées.

### `ue5_godot_decal_map.json`
```json
{
  "exporter_version": "1.0", "manifest_version": "10.0",
  "godot_decal_root": "res://UEAssets/Decals",
  "decal_materials": {
    "<ue_material_path>": {
      "ue_material_path": "...", "name": "...", "godot_path": "...", "disk_path": "...",
      "placement_count": N, "tint": [r,g,b], "tint_parameter": "...",
      "mask_texture": "...", "mask_parameter": "...", "resolution": [w,h],
      "alpha_mean": f, "alpha_min": i, "alpha_max": i,
      "warning": "...",              // optionnel : masque plat détecté
      "normal_godot_path": "..."     // optionnel
    }
  },
  "decal_placement_total": N,
  "vfx": {"converted": false, "reason": "...", "systems": {"<name>": count}, "placement_total": N},
  "notes": ["..."]
}
```
**Clé de conception à retenir** : le mapping est **par matériau de décal**,
pas par placement — un placement individuel (dans `effects.decals[]` du
manifeste) référence son matériau par path, et le reconstructeur doit
**joindre les deux fichiers** pour obtenir la texture réelle. Le nombre de
textures physiques (5 sur ce projet) est donc bien inférieur au nombre de
placements (1423) — pattern de déduplication identique à celui des meshes.

---

## 6. Patterns et algorithmes transversaux

### 6.1 Le triptyque "safe_*" — ne jamais laisser une exception casser le scan
`safe_call(function, default)`, `safe_property(obj, name, default)`,
`safe_int/float/bool(value, default)`. Omniprésent dans le manifeste : une
scène de 5929 acteurs avec des Blueprints custom et des versions d'API
variables **va** avoir des accès qui échouent ponctuellement ; le principe
est de **continuer le scan et signaler**, jamais de planter. C'est ce qui
permet au manifeste de rester exploitable même à 100% d'échec sur une
catégorie entière (cas réel : Decal/Niagara/Light/Audio avant V10.3).

### 6.2 Identité stable des objets UE
`object_path()` (préféré, fallback sur `get_name()`), `object_name()`,
`class_name()`, `class_path()` — utilisés systématiquement comme **clé de
dédoublonnage** dans tous les registres (mesh/matériau/texture/LI) et comme
**identifiant traçable** dans toutes les métadonnées Godot. `stable_id(prefix,
path)` (SHA1 tronqué à 16 caractères) génère les `placement_id` — **ce sont
ces IDs, pas les chemins UE bruts, qui devraient servir de clé primaire dans
un futur schéma de framework**, car ils sont compacts et stables.

### 6.3 Le pattern "retourne (valeur, erreur), n'avale rien"
Introduit en V10.1 (`component_transform_diagnostic`), généralisé à travers
tout le fichier de composition de transform. **C'est le pattern qui a permis
de découvrir et corriger 8 versions de bugs successifs** (§7) — sans lui,
chaque bug se serait manifesté comme un simple "ça ne marche pas" sans piste.
**À imposer comme convention dans tout code du futur framework qui touche à
l'extraction de données UE.**

### 6.4 Conversion d'axe UE → Godot (la convention finale, validée)
```
godot.x =  ue.x
godot.y =  ue.z
godot.z =  ue.y
godot   *= 0.01   (cm → m)
```
Déterminant **-1** (inversion de handedness), **choisi pour matcher la
convention de l'exporteur glTF natif d'Unreal**, pas une convention
"sémantique" dérivée à la main (avant/droite/haut) — piège vécu deux fois
(le `.gd` d'Oumi et une tentative indépendante de correctif ont chacun essayé
une variante différente avant de converger sur celle-ci, voir §7). Deux
conséquences mécaniques à ne jamais oublier lors d'une réimplémentation :
- **le winding des triangles doit être inversé** partout où une géométrie
  est écrite à la main (voir `build_grid_mesh()`) ;
- **la conversion de rotation ne peut pas être une simple permutation de
  composantes d'angle** — elle doit passer par un quaternion/une matrice de
  base, avec un ou deux signes inversés déterminés empiriquement (le fichier
  `.gd`, absent, contient le correctif exact : `qx`/`qy` inversés par
  rapport à `FRotator::Quaternion()`).

### 6.5 Raycasting en grille contre le terrain (implémenté 2 fois indépendamment)
`sample_landscape_heights()` (dans le générateur de matériau) et
`sample_heightfield()` (dans l'export Landscape) partagent le même
algorithme : grille régulière, tir vertical `line_trace_single`, ignore-list
cumulative pour transpercer les props. **Candidat évident de factorisation**
dans le framework — une seule fonction utilitaire `raycast_grid_heights()`
paramétrée par (bounds, résolution, canal de trace, prédicat "est-ce le
terrain").

### 6.6 Écriture de GLB "à la main" (sans bibliothèque)
`write_glb()` (Landscape) construit un glTF binaire complet en pur Python :
un buffer, des bufferViews avec offsets calculés/paddés manuellement à 4
octets, des accessors avec `min`/`max` requis par la spec pour `POSITION`,
JSON paddé à l'espace. **Un pattern réutilisable** pour tout export de
géométrie procédurale future (ex. : un autre système que le Landscape qui
n'aurait pas d'équivalent exportable nativement).

### 6.7 Registres dédupliqués avec back-references
Le pattern `{path: {...info..., usage_count, xxx_references[]}}` revient
identiquement pour meshes, matériaux, textures, et décals-par-matériau. À
formaliser comme une structure générique (`AssetRegistry<T>`) dans le
framework plutôt que 4 implémentations parallèles quasi-identiques.

### 6.8 Configuration en constantes de module, jamais en paramètres
**Chaque script est un `main()` autonome** lisant des constantes définies en
haut de fichier (`OUTPUT_PATH`, `GRID_RESOLUTION`, `MASK_PARAMETER_NAMES`,
etc.) — **aucun des 8 fichiers n'expose une fonction paramétrable ou une
interface CLI/API**. C'est le changement structurel n°1 requis pour qu'un
orchestrateur puisse piloter ces étapes plutôt que les invoquer une par une,
à la main, dans l'éditeur (§9, §11).

---

## 7. Chronologie des bugs — pourquoi chaque règle existe

Cette chronologie **doit être conservée** dans la documentation du futur
framework : chaque règle ci-dessous encode un bug réel, mesuré, sur une
scène de production (5929 acteurs). Les reperdre pendant la généralisation
reproduirait les mêmes symptômes.

1. **V10.1** — Introduction du pattern `(valeur, erreur)` partout au lieu
   d'avaler les exceptions ; séparation `empty_mesh_slot_count` vs
   `missing_mesh_references` (faux positifs) ; capture des SkeletalMesh
   (comptés avant, jamais enregistrés) ; diagnostic FX séparé de la
   géométrie ; correction d'un bug où `conversion_diagnostic` entier
   écrasait `v10_transform_diagnostic` déjà calculé (un dict litéral
   remplaçait tout au lieu de fusionner) ; collision de noms de fichiers
   GLB entre assets homonymes de dossiers différents.
2. **V10.2** — Le même diagnostic staleness/re-fetch appliqué aux
   composants Decal (pas seulement StaticMesh).
3. **V10.3 — Cause racine, le bug le plus important du fichier** : sur
   Decal/Niagara/Light/Audio, `get_component_transform()` lève
   `AttributeError` à 100% alors que `get_editor_property()` sur les mêmes
   objets fonctionne parfaitement (matériau, taille, intensité, couleur,
   volume tous lisibles). Ce n'est **pas** un objet mort — seule la méthode
   Python n'est pas bindée pour ces classes. Correctif : reconstruire le
   transform depuis `relative_location/relative_rotation/relative_scale3d`
   en remontant `attach_parent`.
4. **V10.4** — Les ISM/HISM ne stockaient que le transform du composant
   (donc 1 instance rendue au lieu de N, jusqu'à 165 perdues pour un seul
   foliage) : lecture de `get_instance_transform(i, world_space=True)` pour
   *chaque* instance, avec 3 signatures d'appel essayées, et composition
   individuelle de chaque instance à travers la chaîne LI.
5. **V10.5 — Ordre de composition des transforms** : Unreal compose
   `child * parent` (`NewTransform = RelativeTransform * ParentToWorld`),
   le code composait `parent * child` (inversé). Invisible tant qu'aucun LI
   de la chaîne n'avait de rotation non-identité (translation pure =
   commutative) ; dès qu'une rotation intervenait, tout ce qui en dépendait
   se retrouvait dispersé de façon apparemment aléatoire dans la scène.
6. **V10.6** — `unreal.Rotator` a la signature Python `(roll, pitch, yaw)`,
   **pas** `(pitch, yaw, roll)` comme le constructeur C++ `FRotator`. Passer
   les arguments positionnellement dans l'ordre C++ permutait silencieusement
   chaque rotation. Correctif : toujours des arguments nommés.
7. **V10.7 — Double comptage sur les chaînes profondes** : le transform
   d'une LevelInstance stocké par `level_instance_info()` vient de
   `get_actor_transform()`, donc **déjà en espace monde**, pas local à son
   parent. Composer toute la chaîne appliquait chaque ancêtre deux fois.
   Seuls les objets à profondeur ≥2 étaient touchés (580 sur 7872, jusqu'à
   ~230m d'écart). Correctif : ne composer que le maillon le plus interne.
   Mesuré : écart médian composant↔acteur 48m → 3m.
8. **V10.8 (dernière version présente)** — Repli V10.3 remontait jusqu'au
   composant racine et utilisait SA relative comme transform monde — valide
   pour un `StaticMeshActor` classique, faux pour un Blueprint (la relative
   du root component y vaut l'identité ; la position monde vit sur
   l'ACTEUR). 327 placements (94 petits piliers, 50 grands piliers, 39
   torches, plus grilles/statues/pots) atterrissaient tous à l'origine.
   Correctif définitif : partir de `actor_transform()` (fiabilité mesurée :
   0 échec sur toute la scène) et composer **seulement** les relatives des
   composants **non-racines**.

**Côté `.gd` (fichier maintenant lu intégralement, §3.9 — correctifs
confirmés dans le code final)** :
9. **V10.7** — signes de `qx`/`qy` inversés dans `_unreal_rotator_to_basis()`
   par rapport à `FRotator::Quaternion()`. Une rotation yaw-seul annulait
   l'erreur (d'où des bâtiments qui semblaient corrects) ; tout ce qui avait
   du pitch ou du roll ressortait en miroir. Correctif visible dans le code
   final : `qy := -cr * sp * cy - sr * cp * sy` (signe négatif explicite).
10. **V10.8** — le déterminant de la conversion d'axe passe de +1 à -1
    (`_convert_basis_ue_to_godot`, base `Gx=Ux, Gy=Uz, Gz=Uy`) pour matcher
    la handedness déjà inversée par l'exporteur glTF natif d'Unreal. Une
    tentative antérieure de changer le mapping vers un preset "sémantique"
    (y,z,-x) avait **empiré le rendu** et été annulée avant d'arriver à
    cette version — la bonne convention ne se déduit jamais par
    raisonnement géométrique seul (voir §13.A.5).
11. **V10.9 → V10.10** — première passe décals + VFX, puis réécriture
    complète du module VFX après qu'un shader personnalisé (`parcel
    shader`) ait produit des carrés blancs opaques sur les rendus de test
    (Godot retombe sur le matériau par défaut d'un `QuadMesh` quand un
    shader échoue à recevoir `COLOR`/`INSTANCE_CUSTOM` correctement — voir
    §13.B.12). Base par défaut reconstruite sans aucun shader
    (`GradientTexture2D` + `vertex_color_use_as_albedo` + `BILLBOARD_
    PARTICLES`).
12. **V10.11** — `VFX_USE_PARCEL_SHADER` était **documentée** comme
    "désactivée par défaut" mais la constante elle-même était restée à
    `true` : chaque catégorie marquée `"parcel": true` (candle, fire,
    smoke, steam) heurtait donc encore le chemin shader non vérifié. La
    constante correspond maintenant réellement à son intention documentée
    (`false`). `torch` a aussi cessé de partager le preset "bonfire" de
    `fire` (trop grand pour une torche murale/tenue en main) : preset
    dédié, dimensionné entre `candle` et `fire`.
13. **V10.12** — `fire` parcourait encore ~3 m avant de s'éteindre (un
    brasier dont la flamme atteignait la canopée d'un arbre au-dessus).
    Portée ramenée à ~0,8 m, et le corps de flamme rendu délibérément
    quasi-statique à la source ; un **second émetteur séparé**, réutilisant
    la physique déjà correcte de la catégorie `spark`, est superposé pour
    `fire`/`torch` afin que la sensation de montée vienne des braises, pas
    de la flamme elle-même qui s'étire.

**Leçon transversale** : presque tous ces bugs partagent la même signature —
*"ça marche pour le cas simple (translation pure / StaticMeshActor / racine
peu profonde) et casse silencieusement dès qu'un cas plus complexe apparaît
(rotation / Blueprint / profondeur ≥2)"*. Le framework généralisé doit être
**testé systématiquement contre ces 4 axes de complexité** (rotation
non-identité, origine Blueprint, LevelInstances imbriqués, ISM multi-instance)
et pas seulement contre le cas trivial.

---

## 8. Déjà généralisable vs encore lié au projet X (Necropolis)

### Généralisable tel quel (aucune référence au projet)
- `ue5_preprocess_detach_all.py` — entièrement neutre.
- `png_codec.py` — entièrement neutre, zéro dépendance UE.
- `unreal_export_manifest_v10-8.py` — logique de scan/classification/
  composition/diagnostic 100% générique ; seuls les chemins de sortie sont
  en dur.
- `unreal_export_godot_assets_PATCHED.py` — générique, chemins en dur
  seulement.
- L'algorithme de `unreal_export_landscape.py` (raycast + bake + writer GLB
  maison) — générique ; chemins, résolutions et convention d'axe à
  paramétrer.
- La logique de `unreal_export_decals_vfx.py` (cascade de résolution de
  texture, bake par render target, composition RGBA, détection de masque
  plat) — générique ; les *listes de noms de paramètres* ne le sont pas.
- Le pattern de `godot_decals_vfx_addition.gd` (préchargement dédupliqué,
  group-by pour marqueurs non convertis, métadonnées de traçabilité).

### Encore lié au projet X
- **`auto_terrain_generator_ue55.py` en entier** — pack Necropolis, assets
  Quixel spécifiques, réglages esthétiques. Hors scope de la conversion.
- `MASK_PARAMETER_NAMES` / `TINT_PARAMETER_NAMES` / `NORMAL_PARAMETER_NAMES`
  dans `unreal_export_decals_vfx.py` — conventions de nommage du pack
  Necropolis (`M_Decals_01`, `Tint 01/02`).
- Tous les chemins `C:/Export/...` et `res://UEAssets/...` codés en dur dans
  les 4 scripts d'export.
- `LANDSCAPE_UE_PATH = "/AutoTerrain/Landscape.BakedLandscape"` — nom
  synthétique arbitraire, sans conséquence fonctionnelle mais à rendre
  configurable.

---

## 9. Ce qu'il faudra généraliser, point par point

1. **Paramétrer chaque script** : remplacer les constantes de module par une
   fonction `run(config: dict)` (ou dataclass), pour que l'orchestrateur
   puisse les invoquer avec des chemins/résolutions différents sans éditer
   le code source à chaque map.
2. **Recevoir la config depuis l'orchestrateur** — puisque ces scripts
   s'exécutent **dans le contexte Python embarqué de l'éditeur Unreal**
   (`import unreal`), l'orchestrateur devra soit (a) les piloter via
   l'API distante d'Unreal (remote execution / Python bridge), soit
   (b) générer un script de config temporaire lu au démarrage, soit
   (c) les exposer comme un plugin/commandlet UE invocable en ligne de
   commande. À trancher explicitement dans la conception de l'orchestrateur.
3. **Externaliser les conventions de nommage de matériau** (décals) dans un
   fichier de config **par pack d'assets**, pas en dur dans le code —
   potentiellement un système de "profils" sélectionnables (Necropolis,
   Quixel générique, etc.), avec une résolution en cascade générique déjà
   présente comme fallback ultime.
4. **Exporter les SkeletalMesh** — capturés dans le manifeste
   (`geometry.unique_skeletal_meshes`) depuis V10.1 mais jamais exportés en
   GLB par `unreal_export_godot_assets_PATCHED.py`. Trou à combler.
5. **Généraliser la classification par substring** (`actor_category`,
   `component_kind`) vers un système extensible (table de correspondance
   externe classe→catégorie, avec règles par défaut + surcharge par
   projet), pour supporter des classes d'acteur custom sans toucher au code
   du scanner.
6. **Multi-Landscape** — le script actuel ne traite qu'un seul Landscape
   "primaire" avec avertissement s'il y en a plusieurs. À généraliser si le
   framework doit supporter des maps multi-terrain.
7. **Automatiser le pré-traitement manuel** — casser les LevelInstances est
   actuellement 100% manuel via l'UI. Si l'API `unreal.LevelInstanceEditor
   Subsystem` (ou équivalent) expose une opération de "break" scriptable,
   l'intégrer à `ue5_preprocess_detach_all.py` supprimerait une étape
   humaine source d'erreur.
8. **Vérification croisée automatique** entre les 3 JSON (au lieu de la
   note manuelle "Asset-map entries doit égaler Manifest unique meshes") —
   un contrôle de cohérence formel, exécutable, avant de lancer le
   reconstructeur.
9. **Versionner réellement le schéma** — `manifest_version` est resté figé à
   `"10.0"` à travers 8 sous-versions de bugfix (V10.1→V10.8). Le framework
   devrait soit versionner à chaque évolution de schéma réelle, soit
   documenter explicitement que le numéro de version ne reflète que des
   changements de *forme* du JSON, pas de comportement interne.
10. **Le reconstructeur `.gd` (§3.9)** — maintenant lu intégralement ; ce
    qui reste à faire n'est plus "l'obtenir" mais **l'extraire de son mode
    d'exécution `EditorScript`**. Pour un framework pilotable, il devra
    devenir soit un script Godot headless (`--headless --script ...`, via
    `SceneTree` plutôt que `EditorScript`, si l'API utilisée le permet en
    dehors de l'éditeur), soit un plugin/commande exposée que
    l'orchestrateur peut déclencher sans intervention humaine dans
    l'éditeur. Externaliser aussi les ~15 presets/constantes VFX
    (catégories, mots-clés, couleurs, budget de lumières, hauteurs de
    calibration torch/candle) et les 5 `FAIL_ON_*` en configuration,
    exactement comme pour les scripts côté Unreal (§9.1).
10b. **Donner au reconstructeur accès aux bounds réels du GLB par
    instance** — `_vfx_flame_height_offset` calibre la hauteur de flamme
    sur des constantes estimées (`VFX_TORCH_ASSUMED_HEIGHT_M`, etc.) faute
    d'accès aux dimensions réelles du mesh depuis la passe VFX. Si le
    framework expose les bounds du GLB (déjà capturés côté manifeste,
    `mesh.bounds`) à cette étape, la calibration peut devenir automatique
    plutôt qu'estimée à la main par projet.
10c. **Faire consommer au reconstructeur le contrat `reconstruction` du
    manifeste** — actuellement, `ready_for_godot_geometry` /
    `ready_for_godot_fx` sont calculés côté Python mais jamais lus côté
    `.gd`, qui refait sa propre validation complète indépendamment. Un
    framework devrait faire porter la vérité par un seul côté (probablement
    le manifeste, puisqu'il a la vue la plus complète) et faire du
    reconstructeur un simple consommateur de ce verdict, pour éliminer le
    risque de divergence entre deux validations qui ne se parlent pas.
11. **Stratégie Blueprint** — explicitement hors scope
    (`blueprint_logic_included: false`) : le manifeste capture la
    *structure* des composants d'un Blueprint (transform, mesh, matériaux)
    mais jamais son comportement scripté. Le framework doit documenter cette
    limite clairement comme un choix assumé, pas un oubli — et éventuellement
    exposer un point d'extension pour qu'un utilisateur mappe certains
    Blueprints connus (ex. portes, leviers) vers des scènes Godot
    équivalentes fournies à la main.
12. **Table de correspondance axe UE→Godot vérifiable automatiquement** —
    actuellement stockée comme simple *label* texte (`AXIS_MAP_LABEL`,
    `axis_map` dans le JSON) sans aucune vérification croisée avec ce que le
    `.gd` utilise réellement. Un framework robuste devrait faire porter
    cette convention par **une seule source de vérité partagée** (un fichier
    de config lu par les deux côtés, Python et GDScript) plutôt que deux
    implémentations synchronisées à la main.

---

## 10. Contraintes et pièges qui peuvent tout casser

- **Ordre d'exécution strict** : manifeste → assets → **Landscape en
  dernier** parmi les scripts qui réécrivent from-scratch (les scripts 3 et
  4 ne font qu'ajouter, mais scripts 1 et 2 réécrivent tout — les relancer
  après 3/4 efface leur travail).
- **`png_codec.py` doit être physiquement à côté** de
  `unreal_export_decals_vfx.py` (chargement par chemin relatif au fichier,
  pas par import de package Python standard).
- **KEEP_WORLD explicite obligatoire** sur les 3 règles de
  `detach_from_actor()` — le défaut de l'API (`KEEP_RELATIVE`) déplacerait
  silencieusement les acteurs.
- **`unreal.Rotator` en Python attend `(roll, pitch, yaw)`**, jamais l'ordre
  C++ `(pitch, yaw, roll)` — toujours utiliser des kwargs, jamais des
  positionnels, pour tout code qui construit un `Rotator`.
- **La composition de transform Unreal est `child * parent`**, jamais
  l'inverse — vrai pour `compose_chain_transform`, pour les LI imbriquées,
  et implicitement pour toute logique de composition future.
- **Le transform stocké pour une LevelInstance est déjà en espace monde**
  (vient de `get_actor_transform()`) — ne jamais le recomposer une deuxième
  fois avec ses propres ancêtres déjà inclus.
- **`get_component_transform()` n'est PAS fiable sur
  Decal/Niagara/Light/Audio** (et sur certains composants de Blueprint) —
  toujours passer par le diagnostic + repli sur les propriétés, jamais
  utiliser cette méthode nue pour ces classes.
- **La base du repli par propriétés doit être l'acteur, jamais le composant
  racine** — sinon tout Blueprint dont le root a une relative identité
  atterrit à l'origine.
- **La convention d'axe (`ue.x, ue.z, ue.y`, déterminant -1) doit être
  identique des deux côtés du pipeline** (script d'export landscape ET
  reconstructeur `.gd`) — aucune vérification automatique n'existe
  actuellement ; un futur refactor de l'un sans l'autre romprait tout
  silencieusement (déjà arrivé une fois, corrigé, annulé, re-corrigé).
- **Le winding des triangles dépend du déterminant de la conversion d'axe**
  — un déterminant -1 (handedness inversée) exige d'inverser l'ordre des
  indices, sous peine de géométrie invisible (faces retournées).
- **World Partition limite la garantie du scan au contenu chargé/accessible**
  — le manifeste le documente explicitement comme un warning permanent, pas
  une erreur ; un futur orchestrateur devrait s'assurer que toutes les
  cellules pertinentes sont chargées avant de lancer le scan.
- **`GLTFExporter` doit être activé comme plugin UE** pour que l'export de
  StaticMesh fonctionne — sinon `unreal.GLTFExporter` est `None` et l'export
  échoue proprement avec un message explicite, mais c'est un prérequis
  d'environnement à documenter/vérifier dans l'orchestrateur.
- **Le SceneCapture2D doit toujours être détruit**, y compris en cas
  d'échec (pattern `finally` déjà en place dans le code existant) — sinon
  des relances répétées polluent le niveau d'acteurs temporaires.
- **Le reconstructeur est un `EditorScript` : il ne s'exécute que dans le
  contexte de l'éditeur Godot, déclenché manuellement.** Ce n'est pas un
  détail cosmétique — tant qu'il reste sous cette forme, aucun
  orchestrateur externe ne peut l'invoquer sans qu'un humain clique sur
  *Run* dans le panneau FileSystem (ou sans passer par l'automatisation
  d'éditeur de Godot, si elle est disponible et fiable pour ce cas d'usage).
  C'est la contrainte la plus structurante pour la conception de
  l'orchestrateur (voir §9 point 10 et §11).
- **Un échec de `PackedScene.pack()` ou de `ResourceSaver.save()` libère
  toute la racine (`root.free()`) sans écrire de fichier** — bonne
  propriété (jamais de `.tscn` à moitié construit), mais signifie qu'un
  orchestrateur qui attend un fichier de sortie doit vérifier sa présence
  réelle après l'exécution, pas seulement l'absence de code de retour
  d'erreur (il n'y en a pas, puisque l'exécution se fait dans l'éditeur).
- **Les deux côtés du pipeline valident chacun indépendamment** — le
  reconstructeur ne lit jamais le bloc `reconstruction` du manifeste et
  refait sa propre passe de validation. Un futur consommateur ne doit pas
  supposer que "le manifeste dit prêt" garantit que "le `.gd` accepte tout"
  ou inversement.

---

## 11. Ce dont l'orchestrateur a besoin

D'après le contexte donné en plus par Oumi (l'orchestrateur prend en entrée
**la map Unreal complète comme source de vérité** + **le projet Godot de
destination** + **une configuration avancée**, et gère tout à partir de là),
voici ce que cette lecture exhaustive permet de préciser :

**Étapes à orchestrer, dans l'ordre, avec leurs dépendances de données**
(reprend exactement §1, condensé pour référence rapide) :
```
0. detach_all         (état éditeur uniquement, aucune I/O fichier)
1. manifest           (écrit : manifest.json)                    [from scratch]
2. assets             (lit : manifest.json  | écrit : asset_map.json + *.glb)  [from scratch]
3. landscape          (lit+écrit : manifest.json, asset_map.json | écrit : landscape.glb) [patch]
4. decals_vfx         (lit : manifest.json | écrit : decal_map.json + *.png)   [patch sur rien d'autre]
5. copie              (déplace les 2 dossiers + 3 JSON vers res:// du projet Godot cible)
6. reconstruction .gd (lit les 3 JSON | écrit : la scène .tscn)
```

**Points de configuration à exposer** (actuellement tous en dur, dispersés
dans les 6 fichiers) :
- chemins d'export UE (`C:/Export/...`) et racine Godot (`res://UEAssets`) ;
- chemin du projet Godot de destination (jamais géré par les scripts actuels
  — la "copie" à l'étape 5 est **entièrement manuelle** aujourd'hui) ;
- `GRID_RESOLUTION` / `TEXTURE_RESOLUTION` du Landscape ;
- listes de noms de paramètres matériau pour les décals (par pack d'assets) ;
- convention d'axe (actuellement une seule constante `AXIS_MAP`, mais à
  synchroniser avec le `.gd`) ;
- activer/désactiver chaque étape optionnelle (Landscape, décals/VFX) —
  toutes les maps n'ont pas forcément un Landscape ou des décals.

**Ce que l'orchestrateur doit vérifier/garantir (actuellement manuel)** :
- que le pré-traitement (étape 0 + break LI manuel) a bien été fait avant le
  scan — aucun signal actuel dans le manifeste ne prouve que
  `ue5_preprocess_detach_all.py` a tourné ;
- cohérence `asset_map.json["assets"]` ↔ `manifest.json["geometry"]
  ["unique_meshes"]` (compte égal) ;
- `reconstruction.ready_for_godot_geometry` et `ready_for_godot_fx` à `true`
  avant de lancer la reconstruction Godot — actuellement seulement visible
  dans les logs console, jamais vérifié programmatiquement en aval ;
- que `GLTFExporter` est bien disponible avant de lancer l'étape 2 ;
- ordre d'exécution correct (ne jamais relancer manifest/assets après
  landscape/decals sans tout refaire depuis le début).

**Interface d'invocation à concevoir** : puisque les scripts actuels
tournent **dans** l'éditeur Unreal (session Python embarquée, `import
unreal`), l'orchestrateur — s'il vit en dehors d'Unreal — doit choisir entre
piloter Unreal à distance (remote Python execution, souvent via un socket
ou un plugin d'exécution de commande), ou être lui-même un outil qui
s'exécute *dans* Unreal (menu éditeur / commandlet) et appelle
optionnellement des utilitaires externes pour la partie Godot. C'est une
décision d'architecture à prendre tôt, car elle détermine si les 6 scripts
deviennent des modules important par un orchestrateur unique (nécessite de
les rendre importables/paramétrables, §9.1) ou restent des scripts déclenchés
séquentiellement par un mécanisme externe.

---

## 12. Incohérences et bugs latents repérés pendant cette lecture

Ces points n'étaient pas nécessairement connus d'Oumi — repérés par lecture
attentive et croisée du code, à vérifier/corriger pendant la généralisation :

1. **`asset_inventory.skeletal_mesh_assets` reste toujours codé à `0`** dans
   `unreal_export_manifest_v10-8.py`, alors que
   `geometry.unique_skeletal_meshes{}` est bien peuplé depuis V10.1. Le
   compteur récapitulatif n'a jamais été branché sur le registre réel — un
   consommateur du manifeste qui ne lirait que `asset_inventory` croirait
   qu'il n'y a aucun SkeletalMesh dans la scène.
2. **`component_transform()` brut (sans diagnostic) reste utilisé** pour les
   composants de type `particle` (bloc `elif kind == "particle"` dans la
   boucle principale) et pour tous les `blueprint_component_detail()` (tous
   types confondus, y compris Decal/Niagara/Light/Audio à l'intérieur d'un
   Blueprint) — alors que le diagnostic robuste
   (`component_transform_diagnostic` + repli propriétés) n'est appliqué
   qu'aux composants **directement attachés à un acteur non-Blueprint**. Un
   Blueprint contenant un Decal ou un Niagara pourrait donc silencieusement
   récupérer `transform: null` sans jamais passer par le correctif V10.3/V10.8.
3. **Le numéro de version du schéma (`manifest_version: "10.0"`) n'a jamais
   bougé** malgré 8 sous-versions de correctifs de comportement (V10.1 à
   V10.8) - seul du texte en commentaire de fichier documente la version
   réelle. Un consommateur externe versionnant strictement sur ce champ ne
   verrait aucune différence entre un manifeste V10.1 (bugué) et V10.8 (corrigé).
4. **`AXIS_MAP_LABEL` est stocké mais jamais vérifié** — aucun code ne
   compare la valeur déclarée dans le JSON à la convention réellement
   implémentée côté `.gd`. C'est une trace de documentation, pas un
   contrôle de cohérence actif.
5. **Léger flottement de numérotation des étapes entre les headers de
   fichiers** : `unreal_export_landscape.py` se déclare "script 4" et liste
   4 étapes totales (sans les décals) ; `unreal_export_decals_vfx.py` se
   déclare "script 5 de 6" et inclut le Landscape comme étape 4 — cohérent
   entre eux, mais le premier a manifestement été écrit avant que
   l'étape décals n'existe et n'a pas été mis à jour. Sans conséquence
   fonctionnelle, mais à corriger dans la documentation du framework pour
   éviter toute confusion future.
6. **`unreal_export_godot_assets_PATCHED.py` n'exporte que les
   StaticMesh** (`geometry.unique_meshes`) — les SkeletalMesh n'ont donc
   aujourd'hui **aucun chemin d'export GLB implémenté nulle part** dans le
   pipeline, malgré leur capture au niveau du manifeste.
7. **Le reconstructeur ne lit jamais le contrat `reconstruction` du
   manifeste** (`ready_for_godot_geometry`, `ready_for_godot_fx`) — les deux
   moitiés du pipeline calculent chacune leur propre verdict de "prêt",
   indépendamment, sans jamais se référencer. Repéré en lisant
   `ue5_godot_map_constructor_v10_PATCHED.gd` en entier (§3.9.2) : aucune
   occurrence de `ready_for_godot` dans tout le fichier.
8. **`DECAL_SIZE_SCALE := 0.9`** est déclarée dans le reconstructeur,
   documentée comme *"multiplicateur global sur l'empreinte du décal"*,
   mais **n'est référencée nulle part ailleurs dans le fichier** — le calcul
   de `decal.size` (`_build_decals()`) n'y fait jamais appel. Un réglage
   qui a l'air actif dans le code (et dans tout audit rapide du fichier) est
   en réalité inerte.
9. **`stats["recomputed_li_transforms"]`** est déclaré, imprimé en fin de
   rapport (`_print_report()`), mais **jamais incrémenté** nulle part dans
   le fichier — un compteur systématiquement affiché à 0, qui laisse croire
   à un mécanisme de recalcul de transform LI côté Godot qui n'existe pas
   (cohérent avec l'architecture documentée : les LI ne portent
   délibérément aucun transform appliqué côté `.gd`, la géométrie porte
   déjà son transform monde final — voir §3.9.1/§3.9.7 — mais le compteur
   mort suggère malgré tout une intention non finalisée).

---

## 13. Leçons tirées de l'historique des conversations — ce qu'il ne faut plus refaire

Cette section vient d'une fouille des conversations passées (recherche par mot-clé,
plusieurs dizaines de sessions couvrant le développement du pipeline depuis sa V1
jusqu'à la V10.10 côté `.gd`, plus le générateur de matériau de terrain depuis sa v1
jusqu'à sa v9), **volontairement indépendante du code actuellement présent** dans les
fichiers. Le code montre l'état final validé ; les conversations montrent **le chemin
pour y arriver**, y compris toutes les versions intermédiaires fausses, les
hypothèses rejetées, et les erreurs de Claude lui-même. C'est cette partie-là qui
constitue la vraie liste de "ce qu'il ne faut pas refaire" — le code seul ne la
révèle jamais, puisqu'un fichier final ne montre que ce qui a fini par marcher.

Deux fils de discussion étroitement liés au projet ont fourni l'essentiel de cette
matière : *"Appliquer des textures au landscape"* (le fil le plus long, qui couvre
tout le pipeline d'export V10→V10.10 ainsi que le générateur de matériau v1→v7.1) et
*"Couches peignables sur matériau procédural Landscape"* (l'épisode des couches de
peinture manuelle et du terrain qui rendait noir).

### A. Épistémologie du debug — comment ne plus se tromper de piste

1. **Ne jamais diagnostiquer un symptôme visuel seul.** Face à une capture d'écran
   ("un tas d'objets à l'origine", "des carrés blancs", "un damier gris"), la
   première question n'est jamais "qu'est-ce que ça pourrait être ?" mais "qu'est-ce
   que je peux **mesurer** dans les données réelles (le manifeste JSON) pour le
   confirmer ?". Principe explicitement formulé et systématiquement appliqué dans
   tout le fil : *"la méthode qui a marché à chaque fois reste la mesure."* Chaque
   bug corrigé (327 placements à l'origine, écart médian composant↔acteur, etc.) a
   été localisé par comptage/mesure sur le JSON, jamais par inspection visuelle seule
   de la scène rendue.
2. **Une hypothèse "objet mort / référence périmée" doit être testée en vérifiant si
   D'AUTRES propriétés du même objet sont lisibles.** Si le matériau, la taille,
   l'intensité, la couleur, le volume se lisent tous parfaitement via
   `get_editor_property()` mais qu'une seule méthode précise
   (`get_component_transform()`) échoue à 100%, ce n'est **pas** un objet invalide —
   c'est un binding Python manquant pour cette méthode sur cette classe. La piste
   "référence périmée + re-fetch par chemin" a été explicitement formulée, testée, et
   **rejetée** ("j'avais tort sur mes deux hypothèses, et tes données le prouvaient
   déjà") avant de trouver la vraie cause. Le test décisif : comparer le taux
   d'échec d'une méthode d'ACTEUR (0 sur 5864) à celui de la méthode de COMPOSANT sur
   les mêmes classes d'objets (100% sur 2106) — une asymétrie aussi nette entre deux
   API sur le même objet pointe vers un problème de binding, pas vers un objet mort.
3. **Un désaccord de chiralité (handedness) sur une conversion d'axe est invisible
   sur les objets symétriques.** Piliers, murs, tombes ne révèlent rien ; seuls les
   objets directionnels/asymétriques (escaliers) trahissent le problème. **Toute
   validation future d'une convention d'axe ou de rotation doit se faire sur un objet
   explicitement asymétrique**, jamais sur les premiers objets qui sautent aux yeux
   dans le rendu.
4. **Ne jamais dériver une formule de rotation/quaternion "à la main" par
   raisonnement sur les signes.** Systématiquement source d'erreur silencieuse,
   difficile à repérer à l'œil. Toujours passer soit par l'API native du moteur
   source (`unreal.Quat`, `FRotator::Quaternion()`), soit par un **test empirique
   concret et défini à l'avance** : placer un prop directionnel, le faire tourner de
   90° sur chaque axe séparément dans le moteur source, vérifier qu'il atterrit dans
   la bonne orientation côté destination — jamais une déduction "sémantique"
   (avant/droite/haut) qui a produit **deux fois** une convention finalement fausse
   dans ce projet.
5. **Une conviction "recoupée par deux méthodes indépendantes" n'est toujours qu'une
   hypothèse tant qu'elle n'a pas été testée sur la vraie scène.** La formule d'axe
   `godot.x=ue.y, godot.y=ue.z, godot.z=-ue.x` (déterminant +1) a été présentée à un
   moment comme *"validée par deux méthodes indépendantes (raisonnement sur les axes
   + confirmation croisée par l'outil externe trouvé)"* — et s'est pourtant révélée
   fausse à l'usage : la bonne convention a déterminant **-1**, parce que les GLB
   produits par l'exporteur glTF natif d'Unreal ont déjà leur main inversée par cet
   exporteur, ce qu'aucun raisonnement géométrique abstrait ne pouvait deviner sans
   regarder ce que l'exporteur fait réellement. **La leçon la plus chère de tout ce
   projet : aucun degré de recoupement théorique ne remplace un test empirique sur la
   sortie réelle du pipeline.**
6. **Toujours séparer "structure vérifiée" de "comportement runtime vérifié".**
   Claude ne peut exécuter ni Unreal ni Godot. Chaque livraison de script GDScript a
   été explicitement qualifiée : contrôles passés = absence de séquences `\t`/`\n`
   littérales, tous les appels résolus, toutes les fonctions avec type de retour
   déclaré, aucune duplication — mais jamais "testé et ça marche". Formule à
   retenir : *"je n'exécute pas GDScript ici, donc la structure est vérifiée mais pas
   le comportement au runtime."* Ne jamais laisser un livrable futur sous-entendre
   plus de certitude que ce qui a réellement été contrôlable depuis le sandbox.

### B. Pièges techniques précis, génériques (à encoder en garde-fous du framework)

7. **Jamais de `\t`/`\n` littéraux dans un fichier de code assemblé par
   concaténation de chaînes Python.** Un patch GDScript entier est devenu
   imparsable parce que sa section VFX contenait des séquences d'échappement
   textuelles au lieu de vrais caractères de tabulation/saut de ligne — invisible à
   la relecture, fatal à l'exécution (GDScript est sensible à l'indentation). Tout
   générateur de code du framework doit écrire de vrais caractères de contrôle, et
   un contrôle d'intégrité automatique doit vérifier leur absence après génération.
8. **`:=` en GDScript exige que la fonction appelée ait un type de retour
   déclaré.** Ce bug de type d'inférence est réapparu une deuxième fois après avoir
   déjà été corrigé une première fois, simplement parce qu'il était présent dans le
   fichier de base utilisé comme point de départ d'un rebuild ultérieur. **Leçon
   générale** : un patch chirurgical qui ne touche qu'une section doit quand même
   auditer l'ensemble des fonctions custom utilisées avec `:=` dans tout le fichier,
   pas seulement celles qu'on vient d'écrire — un vieux bug non corrigé dans une
   section "stable" peut réapparaître silencieusement dès qu'on repart de cette base.
9. **La ressource `Curve` de Godot clippe silencieusement à `max_value` (1.0 par
   défaut).** Une courbe censée monter à 2.75 aurait été écrasée sans erreur. Toujours
   calculer/fixer les bornes de `Curve` depuis l'amplitude réelle des données
   utilisées, ne jamais laisser la valeur par défaut si le domaine dépasse [0,1].
10. **`.owner` doit être assigné APRÈS `add_child()`, jamais avant.** Un nœud dont
    `.owner` est fixé avant d'être ajouté à l'arbre n'est pas sauvegardé dans la
    `.tscn` — perte silencieuse, aucune erreur, aucun warning.
11. **Ne jamais générer une ressource par instance quand un pattern par catégorie
    suffit.** Sur 422 systèmes VFX, générer une courbe/un gradient/un matériau/un
    shader par marqueur aurait produit environ 2500 ressources et autant de
    compilations de shader. Construire une fois par catégorie (candle, fire, smoke,
    swarm...) et partager systématiquement entre toutes les instances de cette
    catégorie.
12. **Ne pas faire dépendre le rendu par défaut d'un shader personnalisé qui doit
    compiler ET recevoir exactement les bons varyings.** Godot ne dégrade pas
    gracieusement un shader cassé — il retombe sur le matériau blanc opaque par
    défaut du mesh. Un chemin par défaut doit reposer sur des mécanismes qui ne
    peuvent pas échouer à compiler (texture procédurale type `GradientTexture2D`,
    `vertex_color_use_as_albedo`, `BILLBOARD_PARTICLES`) ; un shader plus riche reste
    possible mais **désactivé par défaut** (flag explicite), à activer et valider un
    seul émetteur à la fois.
13. **Deux mécanismes qui contrôlent la même chose entrent en conflit silencieux.**
    `transform_align` du nœud ET le mode billboard du matériau contrôlaient tous
    deux l'orientation en même temps → quads vus par la tranche. Un seul propriétaire
    par axe de contrôle, jamais deux.
14. **Désactiver l'écriture de profondeur pour tout élément censé s'accumuler**
    visuellement (panaches de particules superposés) — sinon les éléments se
    découpent mutuellement au lieu de s'additionner.
15. **Toujours plafonner les ressources runtime dérivées d'un comptage de scène
    source.** 341 flammes de bougies ne doivent jamais produire 341 lumières
    temps réel dans la scène finale — imposer un budget explicite (ex. `VFX_MAX_LIGHTS`)
    avec un espacement minimal entre lumières retenues.
16. **Ordonner les tests de sous-chaîne du plus spécifique au plus général quand
    les noms peuvent se chevaucher.** `NS_candle_flame` contient à la fois `"candle"`
    et `"flame"` — tester `"candle"` avant `"flame"`, sinon la catégorie générale
    absorbe silencieusement les cas spécifiques prévus pour une catégorie dédiée.
17. **Calibrer tout seuil de diagnostic sur la sémantique exacte du compteur
    sous-jacent, jamais sur une intuition.** Un seuil "nombre de samplers > 13"
    s'est révélé faux parce que le mode d'échantillonnage partagé
    (`SSM_WRAP_WORLD_GROUP_SHARED`) change ce que le compteur mesure réellement
    (des opérations d'échantillonnage, pas des objets sampler uniques) — vérifier
    la sémantique précise d'un compteur d'API avant de fixer un seuil d'alerte dessus.
18. **`recompile_material()` d'Unreal ne renvoie AUCUNE erreur en cas d'échec de
    compilation shader** — un matériau qui échoue à compiler produit un rendu noir
    silencieux côté éditeur, jamais une exception Python côté script. Tout code qui
    manipule un graphe de matériau doit prévoir son propre diagnostic de repli (ex. un
    flag `debug_flat_normal` pour isoler la chaîne fautive par élimination), puisque
    l'API ne signalera jamais l'échec elle-même.
19. **Vérifier l'existence d'une propriété avant de l'écrire, dès que son nom peut
    varier entre versions de moteur.** Le pattern `_try_set()` (déjà appliqué aux
    propriétés de particules Godot comme `velocity_pivot`/`turbulence_*`) doit être
    la norme pour toute écriture de propriété nommée en dur, côté Unreal comme côté
    Godot — jamais une écriture directe non protégée sur un nom d'API susceptible
    d'avoir changé.
20. **Un import de texture externe (glTF/Quixel) peut hasher les noms de fichiers**,
    rendant tout matching par mot-clé sur ces noms illusoire — le vécu réel :
    `'rocky_sand' (gravel) not found` alors que la texture existait bel et bien,
    juste sous un nom haché dans un dossier différent. Toujours préférer la
    résolution par référence d'asset **exacte** (chemin complet stocké en config) à
    une heuristique de nom, et ne garder le scan par mot-clé qu'en dernier recours
    explicitement documenté comme tel.
21. **Ne jamais dimensionner un tableau/pipeline sur un nombre de slots supposé
    fixe sans vérifier le nombre réel d'éléments disponibles.** Un blend écrit pour
    4 slots a crashé en `IndexError: list index out of range` dès qu'un projet n'en
    fournissait qu'un seul — dimensionner dynamiquement sur `len(donnée_réelle)`,
    jamais sur une constante supposée.

### C. Décisions de collaboration/produit à respecter dans le futur

22. **Un outil externe complet a été activement recherché puis explicitement
    écarté.** UnrealToGodot (vortechU) a été identifié, son code lu, ses solutions
    aux deux points bloquants (décals, axes) comparées en détail — puis la personne a
    choisi de garder son pipeline maison plutôt que d'y basculer. **Ne pas
    re-proposer un remplacement complet par un outil tiers** sans qu'il soit
    redemandé ; il reste légitime d'aller y chercher une référence ponctuelle (une
    formule, une idée d'implémentation), jamais comme automatisme "remplaçons tout
    par cet outil".
23. **Toute extension du reconstructeur `.gd` doit être additive et isolée, jamais
    une réécriture qui repasse sur du code déjà validé empiriquement.** Le module
    décals/VFX a été livré comme un bloc séparé à coller (`godot_decals_vfx_addition.gd`)
    précisément pour ne jamais risquer d'écraser le correctif de quaternion et la
    conversion d'axes déjà validés à ce moment-là — un principe de conception à
    maintenir dans le framework : les nouvelles capacités s'ajoutent à côté du cœur
    de conversion déjà éprouvé, elles ne le retouchent pas en passant.
24. **Ne jamais produire un résultat qui a l'air converti quand il ne l'est pas.**
    Le choix "Niagara non convertible, placement seul" a été assumé et communiqué
    explicitement (jusque dans le nom du nœud runtime,
    `VFX_MARKERS_NOT_CONVERTED`) plutôt que de simuler une fausse conversion qui
    aurait l'air correcte de loin. Ce principe de transparence doit s'appliquer à
    toute future catégorie non convertible que le framework rencontrera.
25. **L'audio reste un point mort non tranché, à ne pas laisser filer par défaut
    dans le framework.** Contrairement à Niagara (choix "non convertible" assumé et
    documenté) et aux décals (finalement traités), l'`effects`/`world_features.audio`
    du manifeste n'a **jamais reçu de traitement dédié à aucun stade** de
    l'historique du projet — ni export de fichiers son, ni même une passe de
    marqueurs à la `VFX_MARKERS_NOT_CONVERTED`. Ce n'est pas un choix assumé comme
    Niagara, c'est un oubli qui a simplement toujours été repoussé. Le framework
    doit trancher explicitement ce cas plutôt que de reproduire le même silence.

---

## Résumé exécutif (une page)

Le pipeline convertit fidèlement une map UE5.5 vers Godot 4 en 6 étapes
strictement ordonnées, pivotant intégralement autour d'un manifeste JSON
déclaratif qui sépare **description de la scène** (4 scripts Python UE) de
**reconstruction visuelle** (1 `EditorScript` GDScript de 2283 lignes,
maintenant lu en entier — §3.9). La quasi-totalité de la logique de scan,
classification, composition de transform, export de mesh, bake de Landscape,
export de décal/VFX **et** reconstruction Godot (géométrie, décals, marqueurs
VFX) est **déjà générique** — elle ne contient aucune dépendance forte au
projet Necropolis, à l'exception (a) des chemins codés en dur, (b) des
listes de noms de paramètres de matériau de décal, (c) des ~15
presets/constantes VFX et des mots-clés de catégorisation Niagara côté
reconstructeur, et (d) du générateur de matériau de terrain
(`auto_terrain_generator_ue55.py`), qui est un outil d'authoring totalement
hors du scope de conversion. La généralisation en framework consiste donc
principalement à **paramétrer** ce qui existe déjà des deux côtés
(remplacer les constantes de module par de la configuration, côté Python
comme côté GDScript), **combler 3 trous connus** (export SkeletalMesh,
multi-Landscape, contrat de "readiness" jamais consommé par le
reconstructeur alors qu'il existe côté manifeste), **factoriser 1
duplication** (raycast en grille), et **extraire le reconstructeur de son
mode d'exécution `EditorScript`** pour le rendre pilotable par un
orchestrateur sans clic manuel dans l'éditeur Godot — la contrainte
structurante la plus importante découverte à cette lecture.

Au-delà du code, l'historique des conversations (§13) apporte une deuxième
couche de contraintes tout aussi importante : des règles de méthode
(mesurer plutôt que deviner, ne jamais valider une convention d'axe sur un
objet symétrique, séparer structure vérifiée et comportement runtime
vérifié) et des pièges techniques précis (échappement littéral cassant le
parsing GDScript, clipping silencieux de `Curve`, `.owner` assigné trop
tôt, dépendance à un shader qui ne dégrade pas gracieusement, tableaux
dimensionnés sur un nombre de slots supposé fixe) qui ne sont visibles nulle
part dans le code final, puisque celui-ci ne montre que ce qui a fini par
marcher.

---

## Annexe — Comment utiliser ce document

Ce fichier est conçu pour être **collé intégralement en contexte** (upload
projet, ou début de conversation) d'une future session consacrée à la
généralisation. Il n'y a rien à relire en amont : tout ce qui a été lu (9
fichiers + l'historique de conversation) est déjà digéré dedans.

Ordre de lecture recommandé pour qui reprend le travail : §4-5 (les 3
schémas JSON, le contrat de données réel) → §6 (patterns transversaux) →
§8-9 (ce qui est déjà générique vs à généraliser) → §10-12 (pièges,
besoins de l'orchestrateur, incohérences) → §13 (ce qu'il ne faut plus
refaire). Les §2-3 (fichier par fichier) servent de référence à consulter
au besoin, pas à lire linéairement.

Prochaine étape logique une fois la généralisation entamée : tenir ce même
document à jour au fil des décisions de conception du framework (nouvelle
section "§14 Décisions de conception du framework", par exemple), plutôt
que de repartir d'une lecture exhaustive à chaque session future.


---

# §14 — Décisions de conception du framework (session 1 : architecture)

> À annexer à `UE5_TO_GODOT_PIPELINE_SOURCE_OF_TRUTH.md`.
> Chaque décision référence la section du document source qui la motive.
> **Statut d'honnêteté (§13.A.6)** : ce document est une conception *structurelle*.
> Je n'exécute ni Unreal ni Godot ici. Deux hypothèses de comportement runtime sont
> marquées `[À VÉRIFIER EMPIRIQUEMENT]` et ne doivent pas être traitées comme acquises.

---

## 14.0 Décision n°0 — sortir le reconstructeur du mode `EditorScript`

### Problème nu

`ue5_godot_map_constructor_v10_PATCHED.gd` est un `@tool extends EditorScript` lancé
par clic droit → *Run* dans le panneau FileSystem (§3.9.1). Conséquences directes,
toutes listées en §10 :

- aucun orchestrateur externe ne peut le déclencher sans main humaine ;
- il ne rend **aucun code de retour** — « il n'y en a pas, puisque l'exécution se fait
  dans l'éditeur » (§10) ;
- il ne reçoit **aucun argument** : ses ~15 presets VFX, ses 5 `FAIL_ON_*`, ses chemins
  et son nom de scène de sortie sont des constantes de fichier (§3.9.5, §3.9.8, §6.8).

Tant que ce point n'est pas tranché, les §14.1 à §14.3 n'ont pas d'objet : un pipeline
dont la dernière étape exige un clic n'est pas orchestrable.

### Ce qui rend le choix possible

§3.9.7 est le fait décisif : **ce fichier n'utilise aucune API réservée à l'éditeur.**
Il repose entièrement sur `ParticleProcessMaterial`, `GradientTexture2D`, `Decal`,
`GPUParticles3D`, `PackedScene`, `ResourceSaver`, `load()` — toutes disponibles hors
éditeur. La seule chose qui l'attache à l'éditeur, c'est `extends EditorScript` et
son point d'entrée `_run()`. Ce n'est donc **pas** une réécriture fonctionnelle, c'est
un changement de point d'entrée.

### Tranche retenue : script headless `SceneTree`, obtenu par extraction, pas par réécriture

```
addons/ue2godot/
  core/
    map_builder.gd        # class_name MapBuilder extends RefCounted
                          #   func build(cfg: Dictionary) -> Dictionary   (le rapport)
    transform.gd          # _transform_from_v10 / _unreal_rotator_to_basis /
                          #   _convert_basis_ue_to_godot  — LE CŒUR INTOUCHABLE
    geometry_builder.gd   # _build_geometry_placement + ISM/HISM
    decal_builder.gd      # _build_decals  (déjà fusionné, §3.8/§3.9.5)
    vfx_builder.gd        # presets, lois, budget de lumières, embers
    manifest.gd           # chargement + validation des 3 JSON
  entry_headless.gd       # extends SceneTree   <- l'orchestrateur appelle CELUI-CI
  entry_editor.gd         # extends EditorScript <- conservé, appelle MapBuilder
```

**Pourquoi cette forme et pas une simple conversion du fichier en `SceneTree`** :
§13.C.23 est une règle de collaboration explicite — « toute extension du
reconstructeur doit être additive et isolée, jamais une réécriture qui repasse sur du
code déjà validé empiriquement ». Le cœur mathématique (`_unreal_rotator_to_basis`
avec le signe `qy` négatif de V10.7, `_convert_basis_ue_to_godot` à déterminant -1 de
V10.8) a coûté deux conventions fausses avant d'être juste (§7.9, §7.10, §13.A.5).
On le **déplace de fichier sans le retoucher** et on garde l'ancien chemin d'exécution
en parallèle. `entry_editor.gd` reste le filet : si le headless se comporte
différemment, le workflow manuel d'aujourd'hui fonctionne toujours.

**Ce que l'orchestrateur gagne concrètement** :

```bash
# passe 1 — import des assets (obligatoire, voir plus bas)
godot --headless --path <projet_godot> --import

# passe 2 — construction
godot --headless --path <projet_godot> \
      --script res://addons/ue2godot/entry_headless.gd -- \
      --manifest res://ue2godot_in/level_manifest.json \
      --report   <chemin_hôte>/reports/step6_build.json
```

`entry_headless.gd` lit ses arguments via `OS.get_cmdline_user_args()` (ce qui suit
`--`), appelle `MapBuilder.build()`, écrit le rapport JSON, puis `quit(0)` ou
`quit(1)`. C'est exactement le manque pointé en §10 : un code de retour réel **plus**
un fichier de rapport.

### Pourquoi pas les deux autres options

- **Plugin / `EditorPlugin` avec entrée de menu** : améliore l'ergonomie du lancement
  manuel, mais ne supprime pas l'éditeur de la boucle. §11 demande un orchestrateur
  qui « gère tout à partir de là » — un bouton de menu ne répond pas à ça. À garder
  comme confort ultérieur, pas comme solution.
- **Automatisation de l'éditeur Godot (piloter l'UI)** : §10 la mentionne comme
  possibilité (« si elle est disponible et fiable pour ce cas d'usage »). C'est la
  seule option qui ajoute une dépendance fragile là où on vient de démontrer qu'aucune
  API d'éditeur n'est nécessaire (§3.9.7). Écartée.

### Deux prérequis runtime `[À VÉRIFIER EMPIRIQUEMENT]`

1. **L'import des `.glb` doit avoir eu lieu.** En dehors de l'éditeur, `load()` résout
   via les métadonnées d'import (`.godot/imported/`). D'où la passe `--import`
   séparée. Bonne nouvelle sur le mode d'échec : si l'import n'a pas eu lieu, `load()`
   renvoie `null` → le chemin `FAIL_ON_MISSING_GLB` (systémique → abort, §3.9.3) se
   déclenche bruyamment. C'est un échec visible, pas une scène silencieusement vide.
2. **`ResourceSaver.save()` vers `res://` depuis un `SceneTree` headless.** Attendu
   comme fonctionnel quand le binaire tourne sur un répertoire de projet (pas sur un
   `.pck` packagé), à confirmer.

### Protocole d'équivalence à exécuter avant de basculer

§13.A.1 (« mesurer, pas diagnostiquer à l'œil ») et §13.A.5 (« aucun recoupement
théorique ne remplace un test empirique ») s'appliquent directement ici. Avant de
déclarer le headless bon :

1. construire une fois avec `entry_editor.gd` → `ref.tscn` (référence) ;
2. construire avec `entry_headless.gd` sur exactement les mêmes 3 JSON → `head.tscn` ;
3. comparer **mécaniquement** : nombre de nœuds, et pour chaque nœud
   `(nom, classe, Transform3D quantifié à 1e-4, chemin de ressource mesh/matériau)` ;
4. vérifier à l'œil **un objet asymétrique** (escalier), jamais un pilier — §13.A.3 ;
5. vérifier que le compte de nœuds sauvegardés est égal au compte de nœuds construits :
   c'est le test qui attrape un `.owner` mal assigné (§13.B.10), qui ne produit ni
   erreur ni warning, seulement des nœuds absents du `.tscn`.

Tant que (3) n'est pas à zéro écart, on ne supprime rien de l'ancien chemin.

---

## 14.1 Architecture du framework

### La frontière core / profil / run

Une règle unique, testable :

> **Le core connaît des *formats*. Le profil connaît des *packs*. Le run connaît
> *cette map-ci*.**
>
> Test : si la réponse change quand on change de pack d'assets ou de direction
> artistique → **profil**. Si elle change quand on change de version de moteur ou de
> format de fichier → **core**. Si elle change quand on change de map ou de machine →
> **run**.

C'est la formalisation directe du partage fait en §8. Deux cas limites que cette règle
tranche et qui étaient ambigus dans le document source :

- le réarrangement des axes de la boîte de décal (Unreal projette sur +X avec
  `[épaisseur, largeur, hauteur]`, Godot sur -Y avec `size=(l, h, p)`, §3.8) est une
  conversion **de format**, donc **core** — le document le dit déjà explicitement ;
- les `MASK_PARAMETER_NAMES` / `TINT_PARAMETER_NAMES` (§3.6) sont une convention de
  nommage **de pack**, donc **profil**, même si le mécanisme de résolution en cascade
  qui les consomme est core.

### Arborescence

```
ue2godot/
├── core/                         # Python pur — AUCUN `import unreal`, testable hors éditeur
│   ├── result.py                 # Result(value, error) — §6.3, convention imposée partout
│   ├── safe.py                   # safe_call / safe_property / safe_int|float|bool — §6.1
│   ├── ids.py                    # object_path(), stable_id() SHA1 — §6.2
│   ├── registry.py               # AssetRegistry générique (info + usage_count + back-refs) — §6.7
│   ├── axis.py                   # LA source de vérité de la convention d'axe — §6.4, §9.12
│   ├── glb.py                    # writer glTF binaire maison — §6.6
│   ├── png_codec.py              # tel quel, zéro modification — §3.7
│   ├── schema/                   # dataclasses + validateurs des 3 JSON — §4, §5
│   ├── config.py                 # chargement, fusion en couches, hash, gel
│   ├── crosscheck.py             # vérification croisée des 3 JSON — §9.8
│   └── report.py                 # StepReport (contrat commun à toutes les étapes)
│
├── ue/                           # tout ce qui fait `import unreal`
│   ├── session.py                # découverte d'acteurs "V4/V5/V6 éprouvée" — §3.2.2 NE PAS REMPLACER
│   ├── classify.py               # actor_category / component_kind + table externe — §3.2.4, §9.5
│   ├── transforms.py             # actor_transform, component_transform_diagnostic,
│   │                             #   *_via_properties, compose_chain_transform — §3.2.5, §3.2.6
│   ├── raycast.py                # raycast_grid_heights() unifié — §6.5 (voir §14.4, décision H3)
│   ├── rendertarget.py           # bake par matériau temporaire + RT — commun Landscape/décals
│   │                             #   (§3.4 bake_base_color ≡ §3.6 extraction de texture)
│   ├── steps/
│   │   ├── step0_preprocess.py   # ex-ue5_preprocess_detach_all.py — §3.1
│   │   ├── step1_manifest.py     # ex-unreal_export_manifest_v10-8.py — §3.2
│   │   ├── step2_meshes.py       # ex-unreal_export_godot_assets_PATCHED.py — §3.3
│   │   ├── step3_landscape.py    # ex-unreal_export_landscape.py — §3.4
│   │   └── step4_decals_vfx.py   # ex-unreal_export_decals_vfx.py — §3.6
│   └── entry.py                  # SEUL point d'entrée in-editor : lit un run_config.json, dispatch
│
├── godot/addons/ue2godot/        # cf. §14.0
│
├── profiles/
│   ├── _default.json             # cascade générique seule, aucun nom de paramètre deviné
│   └── necropolis.json           # noms de params décals, 12 presets VFX, calibrations torch/candle
│
└── orchestrator/
    ├── pipeline.py               # machine d'états + garde-fous entre étapes
    ├── adapters/ue_remote.py     # transport vers l'éditeur Unreal
    └── adapters/godot_cli.py     # sous-processus Godot headless
```

### Ce qui n'entre pas dans le framework

`auto_terrain_generator_ue55.py` reste dehors, intégralement (§3.5, §8). Le framework
n'en retient que **le contrat**, formulé en §3.5 : *« avant d'exporter le Landscape, le
Landscape doit avoir un matériau qui produit le rendu voulu ; la génération de ce
matériau est hors scope. »* Traduction opérationnelle : `step3_landscape` vérifie
qu'un matériau non-défaut est assigné au Landscape et **avertit** sinon, plutôt que de
baker en silence un damier par défaut.

### Empaquetage — ce qui débloque l'importabilité

Les 9 fichiers actuels sont des scripts autonomes à constantes de module, sans API
(§6.8). Le framework s'installe comme un **vrai package Python sous
`<ProjetUE>/Content/Python/ue2godot/`**, répertoire déjà ajouté au `sys.path` de la
session Python embarquée d'Unreal. `import ue2godot` fonctionne alors sans bricolage.

Effet de bord utile : cela supprime la contrainte §10 « `png_codec.py` doit être
physiquement à côté de `unreal_export_decals_vfx.py` » (chargement par
`exec(compile(...))` sur chemin relatif, §3.6). Le codec devient
`from ue2godot.core import png_codec`, un import normal.

### Signature imposée à chaque étape

```python
def run(cfg: ResolvedConfig) -> StepReport: ...
```

Aucune constante de module, aucun effet de bord à l'import, aucun `main()` implicite.
C'est le « changement structurel n°1 » nommé en §6.8 et §9.1.

---

## 14.2 Schéma de configuration externe

### Problème nu

Les valeurs qui varient d'une map à l'autre sont aujourd'hui dispersées en constantes
dans 6 fichiers, des deux côtés du pipeline (§6.8, §11). Pire, deux d'entre elles
doivent être **identiques** des deux côtés sans qu'aucun code ne le vérifie : la
convention d'axe (§10, §12.4).

### Format et couches

**JSON**, pas YAML ni TOML : GDScript parse nativement le JSON (`JSON.parse_string`) et
n'a pas de parseur YAML en stdlib ; tout le pipeline pivote déjà sur du JSON (§4, §5).
Une dépendance externe côté GDScript serait un point de fragilité gratuit.

Trois couches, fusionnées par *deep merge*, priorité croissante :

```
core/defaults.json      livré avec le framework, jamais édité par l'utilisateur
profiles/<pack>.json    par pack d'assets / direction artistique
runs/<map>.json         par map : chemins, activation d'étapes, surcharges ponctuelles
```

### Le mécanisme qui ferme la classe de bugs §12.4

1. L'orchestrateur résout les trois couches en une **config gelée** (`ResolvedConfig`)
   et calcule `config_hash` (sha256 du JSON canonique).
2. Il génère un `run_id` (uuid4) par exécution du pipeline.
3. `step1_manifest` **écrit la config résolue entière dans le manifeste**
   (`manifest.pipeline.resolved_config`) avec `run_id` et `config_hash`.
4. `step2/3/4` estampillent le même `run_id` + `config_hash` dans `asset_map.json` et
   `decal_map.json`.
5. Côté Godot, `MapBuilder` **ne lit pas un fichier de config séparé** : il lit la
   config embarquée dans le manifeste qu'il est en train de reconstruire.

Deux gains mécaniques, pas documentaires :

- **La convention d'axe ne peut plus diverger** entre les deux moitiés du pipeline
  (§10, §12.4) : il n'y a plus deux valeurs à synchroniser à la main, il y en a une,
  transportée par la donnée elle-même. `MapBuilder` garde une table
  `KNOWN_AXIS_PRESETS` des conventions qu'il *implémente réellement* et **abandonne
  bruyamment** si le manifeste en déclare une autre. C'est la différence entre
  `AXIS_MAP_LABEL` (une trace texte jamais vérifiée, §12.4) et un contrôle actif.
- **L'ordre d'exécution strict devient vérifiable** (§1, §10) : si on relance
  `step1` après `step3`, le manifeste porte un `run_id` neuf tandis que `asset_map`
  garde l'ancien. La divergence est détectée avant la reconstruction, au lieu d'être
  découverte par un terrain manquant dans la scène finale.

### Le schéma

```jsonc
{
  "config_version": "1.0",
  "profile": "necropolis",

  // ── chemins — remplace OUTPUT_PATH, TXT_OUTPUT_PATH, MANIFEST_PATH,
  //    OUTPUT_ROOT, GODOT_ROOT, DECAL_MAP_PATH, "Map--_REBUILT.tscn"
  //    (§3.2, §3.3, §3.4, §3.9.8, §8, §11)
  "paths": {
    "ue_export_root":    "C:/Export",
    "godot_project_root":"D:/Jeux/HorrorCoop",      // §11 : jamais géré aujourd'hui
    "godot_asset_root":  "res://UEAssets",
    "mesh_subdir":       "Meshes",
    "decal_subdir":      "Decals",
    "output_scene":      "res://Maps/Map_REBUILT.tscn",
    "report_dir":        "C:/Export/reports"
  },

  // ── convention d'axe — §6.4, §9.12, §10, §12.4
  //    NON surchargeable côté Godot. Voir décision humaine H4.
  "axis": {
    "preset": "unreal_gltf_exporter",   // seule valeur bénie aujourd'hui
    "map": ["x", "z", "y"],             // godot = (ue.x, ue.z, ue.y)
    "determinant": -1,
    "unit_scale": 0.01,
    "invert_winding": true              // dérivé du déterminant, écrit explicitement
                                        // et ASSERTÉ (§6.4, §10)
  },

  // ── étape 0 — §3.1, §11
  "preprocess": {
    "dry_run": true,
    "break_level_instances": "manual",  // "manual" | "auto_if_available"  (§9.7)
    "write_stamp": true                 // §11 : rien ne prouve aujourd'hui que l'étape 0 a tourné
  },

  // ── classification — §3.2.4, §9.5
  //    LISTE ORDONNÉE, jamais un dict : le plus spécifique d'abord (§13.B.16)
  "classification": {
    "actor_rules":     [ {"match": "LevelInstance", "category": "level_instance"} ],
    "component_rules": [ {"match": "Decal", "kind": "decal"} ],
    "blueprint_detection": { "class_suffix": "_C", "path_prefixes": ["/Game/", "/Plugin"] }
  },

  // ── export de meshes — §3.3, §9.4, §12.6
  "meshes": {
    "export_static": true,
    "export_skeletal": false,           // trou connu, voir décision humaine H5
    "reexport_policy": "skip_existing", // "skip_existing" | "force" | "hash_check"
    "validate_glb_header": true         // §3.3 : taille >= 20 o ET magie b"glTF"
  },

  // ── Landscape — §3.4, §9.6
  "landscape": {
    "enabled": true,
    "grid_resolution": 256,
    "texture_resolution": 2048,
    "max_trace_retries": 12,
    "capture_source": "SCS_BASE_COLOR", // §3.4 : albédo non éclairé, choix délibéré
    "rt_format": "RTF_RGBA8_SRGB",
    "synthetic_ue_path": "/AutoTerrain/Landscape.BakedLandscape",  // §8
    "multi_policy": "primary_only",     // "primary_only" | "all" | "fail"  (voir H6)
    "require_assigned_material": true   // contrat §3.5
  },

  // ── décals — §3.6, §3.9.5, §9.3, §12.8
  //    Ce bloc vit dans le PROFIL, pas dans le run.
  "decals": {
    "enabled": true,
    "material_parameters": {
      "mask":   ["Mask", "Opacity Mask", "Alpha"],
      "tint":   ["Tint 02", "Tint 01", "Color"],
      "normal": ["Normal", "NormalMap"]
    },
    "tint_fallback": [1.0, 1.0, 1.0],   // §3.6 : blanc neutre, visible plutôt que sombre
    "flat_mask_policy": "warn",         // "warn" | "fail"  (§3.6 détection de masque plat)
    "base_size_uu": 256.0,
    "size_scale": 1.0                   // §12.8 : constante inerte aujourd'hui — À BRANCHER
  },

  // ── VFX — §3.9.6, §3.9.8, §9.10, §9.10b, §13.B.15/16
  //    Presets et mots-clés = PROFIL. Budgets et lois = core.
  "vfx": {
    "mode": "substitutes",              // "markers_only" | "substitutes"  (voir H7)
    "marker_root_name": "VFX_MARKERS_NOT_CONVERTED",   // §13.C.24 : le nom porte l'intention
    "category_rules": [                 // ORDONNÉE : "candle" avant "flame" (§13.B.16)
      {"match": "candle", "category": "candle"},
      {"match": "torch",  "category": "torch"},
      {"match": "fire",   "category": "fire"}
    ],
    "presets": { "candle": { /* couleurs, durées de vie, vitesses */ } },
    "max_lights": 24,                   // §13.B.15 : 341 bougies ≠ 341 OmniLight3D
    "light_min_spacing_m": 6.0,
    "use_parcel_shader": false,         // §13.B.12 + §7.12 : défaut sans shader, toujours
    "calibration": {
      "source": "constants",            // "constants" | "mesh_bounds"  (§9.10b)
      "torch_assumed_height_m": 1.40,
      "candle_assumed_height_m": 0.05
    }
  },

  // ── audio — §13.C.25 : champ OBLIGATOIRE, sans valeur par défaut implicite
  "audio": { "policy": null },          // "ignore" | "markers" | "export" — voir H8

  // ── tolérance — les 5 FAIL_ON_* du reconstructeur, §3.9.3
  //    5 catégories distinctes, JAMAIS fusionnées en un flag unique (leçon V10.4)
  "tolerance": {
    "missing_asset_mapping": "abort",   // systémique
    "missing_glb":           "abort",   // systémique
    "transform_failure":     "skip",    // isolé
    "instantiate_failure":   "skip",    // isolé
    "duplicate_placement_id":"skip",    // isolé
    "readiness_policy":      "warn"     // "ignore" | "warn" | "abort"  — voir H2
  },

  "metadata": { "keep_placement_metadata": true }   // §3.8 : traçabilité Godot → Unreal
}
```

### Deux règles de rédaction du schéma

- **Aucune valeur par défaut implicite pour une question jamais tranchée.**
  `audio.policy` vaut `null` et fait échouer la validation de config tant qu'un humain
  ne l'a pas renseignée. C'est la traduction mécanique de §13.C.25 : l'audio n'est pas
  un choix assumé comme Niagara, c'est un oubli reconduit — un défaut silencieux le
  reconduirait une fois de plus.
- **Toute correspondance par sous-chaîne est une liste ordonnée**, jamais un
  dictionnaire. §13.B.16 formule la leçon sur les mots-clés VFX (`NS_candle_flame`
  contient `candle` *et* `flame`), mais c'est exactement le même mode de défaillance
  que la classification par `in` sur les noms de classe pointée comme « le point le
  plus fragile du système » en §3.2.4. Un dict Python conserve l'ordre d'insertion,
  mais ne le *documente* pas comme sémantique ; une liste, si.

---

## 14.3 Le contrat d'invocation de l'orchestrateur

### Problème nu

L'orchestrateur vit hors d'Unreal et hors de Godot. Les étapes 0-4 tournent dans la
session Python embarquée de l'éditeur Unreal (`import unreal`), l'étape 6 dans Godot,
et aucun des scripts actuels n'est ni paramétrable ni importable (§6.8, §9.2, §11).
§9.2 laisse trois transports ouverts côté Unreal : (a) exécution distante, (b) script
de config temporaire, (c) plugin/commandlet invocable en ligne de commande.

### Tranche retenue côté Unreal : (a) exécution distante contre un éditeur ouvert, avec (b) en repli

**Pourquoi pas (c), le commandlet headless** — c'est le choix qui paraît le plus propre
et c'est celui qui casserait le plus discrètement. Deux raisons, toutes deux dans le
document :

- §3.2.2 : la découverte d'acteurs passe par `ObjectIterator` filtré sur
  `actor.get_level() == level`, avec un commentaire bloc **NE PAS REMPLACER** qui a
  survécu à 10 versions majeures. Ce que cette méthode voit dépend de ce qui est
  chargé dans la session.
- §10 : « World Partition limite la garantie du scan au contenu chargé/accessible ».

Un éditeur lancé en commandlet ne charge pas forcément les mêmes cellules qu'un
éditeur où un humain a ouvert la map et navigué. On changerait donc l'environnement
d'exécution de la partie explicitement marquée « à ne jamais reconsidérer sans preuve
nouvelle », pour gagner en automatisation. Mauvais échange à ce stade.

**Transport (a)** : l'exécution Python distante d'Unreal (activée dans
`Project Settings → Python → Enable Remote Execution`) permet à un processus externe
d'envoyer une commande à l'éditeur en cours et d'en récupérer stdout/stderr. La
commande envoyée est minimale et toujours la même forme :

```python
import ue2godot.entry as e; e.run_step("manifest", r"C:/Export/runs/necropolis_01.json")
```

**Transport (b), repli permanent** : si l'exécution distante est indisponible,
l'orchestrateur écrit le même `run_config.json` et **imprime la ligne à coller** dans
la console Python d'Unreal. Le pipeline reste utilisable à 100 %, avec un humain dans
la boucle sur une seule ligne au lieu de six scripts lancés à la main. Ce mode doit
exister dès le premier jour : il rend le framework indépendant de la fiabilité du
transport.

### Tranche retenue côté Godot : sous-processus CLI, deux passes

Voir §14.0 pour les deux commandes. L'orchestrateur récupère un vrai code de retour,
ce qui n'existe pas aujourd'hui (§10).

### La règle qui rend les deux transports équivalents : le rapport d'étape

Le canal de retour d'une exécution distante est du texte sur stdout — peu fiable pour
du structuré, et §10 rappelle qu'on ne peut pas s'en remettre à un code de retour côté
Unreal. Donc : **la source de vérité de l'orchestrateur n'est jamais le canal de
transport, c'est un fichier de rapport sur disque**, plus la présence réelle des
sorties déclarées.

```jsonc
// C:/Export/reports/step1_manifest.json
{
  "step": "manifest",
  "run_id": "…", "config_hash": "…",
  "status": "OK",                    // OK | OK_WITH_WARNINGS | FAILED
  "started_at": "…", "duration_s": 412.7,
  "outputs": [ {"path": "C:/Export/level_manifest.json", "bytes": 88431203, "sha256": "…"} ],
  "counters": { "actors": 5929, "placements": 7876, "unique_meshes": 166,
                "transform_failures": 0 },
  "errors": [], "warnings": []
}
```

C'est la réponse directe à §10 : *« un orchestrateur qui attend un fichier de sortie
doit vérifier sa présence réelle après l'exécution, pas seulement l'absence de code de
retour d'erreur »*.

### La machine d'états et ses garde-fous

```
  ┌─ G0 ─┐                   G0 : tampon de prétraitement présent et postérieur
  │      ▼                         à la dernière modification du niveau — §11
  │   step0 preprocess             (sinon : --allow-unpreprocessed explicite)
  │      ▼
  ├─ G1 ─┤                   G1 : niveau chargé ; World Partition — cellules
  │      ▼                         pertinentes chargées — §10
  │   step1 manifest  ────────► écrit run_id + config_hash + config résolue
  │      ▼
  ├─ G2 ─┤                   G2 : GLTFExporter disponible (`unreal.GLTFExporter`
  │      ▼                         non None) — §10, §11
  │   step2 meshes
  │      ▼
  ├─ G3 ─┤                   G3 : len(asset_map["assets"]) == len(unique_meshes)
  │      ▼                         — automatisation de la vérif MANUELLE de §1/§9.8
  │   step3 landscape  (si landscape.enabled)
  │      ▼
  │   step4 decals_vfx (si decals.enabled)
  │      ▼
  ├─ G4 ─┤                   G4 : run_id identique dans les 3 JSON — détecte
  │      ▼                         mécaniquement une relance de step1/2 après
  │   step5 copy                   step3/4, qui efface leur travail — §1, §10
  │      ▼
  ├─ G5 ─┤                   G5 : readiness — selon tolerance.readiness_policy
  │      ▼                         (décision humaine H2)
  │   step6 build (godot --import puis --script)
  │      ▼
  └─ G6 ─┘                   G6 : le .tscn existe réellement sur disque ET son
                                   compte de nœuds est cohérent avec le rapport —
                                   §10 : pack/save échoue → root.free(), rien écrit
```

**step5 (copie) devient une vraie étape du framework.** §11 constate qu'elle est
« entièrement manuelle aujourd'hui ». L'orchestrateur connaît `paths.ue_export_root`
et `paths.godot_project_root` : il copie les 2 dossiers et les 3 JSON, et vérifie les
sha256 déclarés dans les rapports après copie.

**G3 et G4 sont les deux garde-fous les plus rentables** du lot : ils remplacent les
deux contrôles aujourd'hui purement documentaires (le comptage croisé noté en §1, et
l'avertissement « Landscape EN DERNIER » écrit dans les en-têtes de fichiers).

---

## 14.4 Décisions qui demandent un retour humain

Ces points ne sont pas tranchés ici volontairement. Chacun a des conséquences que la
lecture du code ne suffit pas à arbitrer.

### H1 — Stratégie Blueprint (§9.11, §12.2)

Le manifeste capture la *structure* des composants d'un Blueprint, jamais son
comportement (`blueprint_logic_included: false`). Trois postures possibles :

- **(a) limite assumée** : on documente, on n'ajoute rien. Coût nul.
- **(b) point d'extension** : le profil déclare un mapping `BP_door_C →
  res://scenes/Door.tscn`, et le reconstructeur instancie la scène Godot fournie à la
  main au lieu du mesh nu. Coût moyen, valeur élevée pour les portes/leviers d'un jeu.
- **(c) tentative de conversion de logique** — hors de question, mais à écarter
  explicitement une fois pour que la question ne revienne pas.

**Ce qui doit être mesuré avant de décider** (§13.A.1) : combien de Blueprints
distincts portent réellement une logique qui compte pour le jeu, dans le manifeste
actuel ? `blueprints.actors[]` donne la réponse par comptage, pas par intuition.

*Note séparée* : §12.2 signale que `blueprint_component_detail()` utilise
`component_transform()` **brut**, sans le diagnostic ni le repli propriétés — un Decal
ou un Niagara *à l'intérieur* d'un Blueprint peut donc récupérer `transform: null` sans
jamais passer par les correctifs V10.3/V10.8. Ce point-là n'est pas une décision, c'est
un bug à mesurer puis corriger (§14.5).

### H2 — Fusion du contrat de « readiness » (§9.10c, §12.7, §3.9.2)

Le manifeste calcule `ready_for_godot_geometry` / `ready_for_godot_fx` ; le
reconstructeur ne les lit **jamais** et refait sa propre validation complète.

**Piège à ne pas franchir en tranchant** : ces deux mécanismes ne font pas la même
chose, et les confondre coûterait cher.

| | Portée | Rôle | Origine |
|---|---|---|---|
| `reconstruction.ready_*` | globale | **verdict d'entrée** : « faut-il lancer la reconstruction ? » | manifeste, §3.2.8 |
| `_build_geometry_placement` | par placement | **résilience** : un cas limite connu ne doit pas jeter 99,8 % de bonnes données | `.gd`, §3.9.3 |

Faire du `.gd` un simple consommateur du verdict global (option (a) de §9.10c) est
défendable ; **supprimer sa tolérance par placement ne l'est pas** — c'est exactement
le bug V10.4 (§3.9.3, §7.4) où un flag catch-all avortait toute la reconstruction pour
un seul composant périmé déjà documenté.

Options à arbitrer :
- **(a)** le manifeste porte la vérité, le `.gd` lit le verdict et refuse de démarrer
  si `false` ; il garde ses 5 tolérances par placement telles quelles ;
- **(b)** le `.gd` reste indépendant, et c'est **l'orchestrateur** (gate G5) qui
  compare les deux verdicts et échoue sur divergence ;
- **(c)** `readiness_policy: "warn"` par défaut — on journalise la divergence pendant
  quelques maps avant de décider, sur données réelles.

(c) est le choix conservateur et il est déjà câblé dans le schéma §14.2. À confirmer.

### H3 — Unification du raycast en grille (§6.5)

§6.5 appelle la factorisation « candidat évident ». Deux frictions qui demandent un
arbitrage :

1. **Le sens de la dépendance.** Les deux implémentations sont dans
   `unreal_export_landscape.py` (dans le scope du framework) et
   `auto_terrain_generator_ue55.py` (**explicitement hors scope**, §3.5, §8).
   Factoriser signifie soit faire de l'outil d'authoring un consommateur du framework
   — ce qui l'y rattache alors qu'on vient de l'en exclure — soit assumer la
   duplication. À trancher.
2. **Les deux usages n'ont pas la même sémantique des trous.** L'export Landscape
   traite un « non touché » comme un trou qu'il faut préserver (`build_grid_mesh`
   n'émet un quad que si les 4 coins sont touchés, §3.4) ; le générateur de matériau
   fait une analyse statistique de distribution d'altitudes où un trou n'a pas de
   sens. Une fonction commune doit donc renvoyer **la grille brute avec ses `None`
   préservés**, et laisser chaque appelant décider — ne pas boucher, ne pas
   interpoler. Si la factorisation devait aboutir à une signature qui masque les
   trous, mieux vaut garder deux implémentations.

Forme minimale proposée si l'unification est retenue :
`raycast_grid_heights(bounds, resolution, channel, ignore_seed, max_retries) -> list[Optional[float]]`.

### H4 — La convention d'axe doit-elle rester configurable ? (§6.4, §9.12, §13.A.5)

Tension réelle entre deux exigences du document. §9.12 demande une source de vérité
unique partagée par les deux côtés — d'où le bloc `axis` en config. Mais §13.A.5 est
la « leçon la plus chère de tout ce projet » : la bonne convention ne se déduit pas,
elle s'observe sur la sortie réelle de l'exporteur glTF d'Unreal. Un champ librement
éditable invite précisément au raisonnement qui a produit deux conventions fausses.

Proposition à valider : **configurable, mais avec un seul preset béni**
(`unreal_gltf_exporter`), et une valeur `custom` qui exige de déclarer explicitement
`determinant` et `invert_winding`, et qui **refuse de tourner** sans un test de
validation passé sur un objet asymétrique (§13.A.3). Alternative : figer en dur dans
le core et n'externaliser que le *label* vérifié. À trancher.

### H5 — SkeletalMesh : dans le scope v1 ou non ? (§9.4, §12.6)

Capturés dans le manifeste depuis V10.1, **aucun chemin d'export GLB n'existe nulle
part** dans le pipeline. Sans animation ni retargeting côté Godot, un skeletal mesh
exporté est un mesh statique coûteux. Question de périmètre, pas de technique.

### H6 — Multi-Landscape (§9.6)

Aujourd'hui `landscapes[0]` avec un avertissement. Généraliser implique : une passe de
raycast et un bake par Landscape, la gestion des coutures entre dalles, et un choix
entre une texture par terrain ou un atlas. Coût non trivial. Nécessaire maintenant ou
repoussé ?

### H7 — Le système de substitution VFX est-il du core ou un profil ? (§3.9.6, §3.9.8)

~1000 lignes de substitution visuelle (12 catégories, 8 lois de panache, embers,
budget de lumières) dont §3.9.8 dit que les *valeurs* sont réglées pour ce cimetière,
mais dont les *mécanismes* sont génériques. Deux lectures possibles :

- **core avec presets en profil** : une nouvelle map a des flammes par défaut, réglées
  sur des valeurs génériques possiblement fausses pour elle ;
- **plugin Necropolis** : une nouvelle map obtient `markers_only` (§13.C.24, honnête)
  et n'a des VFX que si quelqu'un écrit son profil.

La deuxième est plus conforme à §13.C.24 (« ne jamais produire un résultat qui a l'air
converti quand il ne l'est pas »), la première est plus utile immédiatement.

### H8 — Audio (§13.C.25)

Le seul point mort jamais tranché du pipeline : ni export, ni même une passe de
marqueurs à la `VFX_MARKERS_NOT_CONVERTED`. Trois valeurs possibles pour
`audio.policy` : `ignore` (assumé et documenté), `markers` (placement seul, cohérent
avec le traitement Niagara), `export` (sortir les fichiers son — coût réel, valeur à
estimer). Le schéma §14.2 refuse de démarrer tant que ce champ est `null` : c'est
délibéré.

### H9 — Version de schéma (§9.9, §12.3)

`manifest_version` est figé à `"10.0"` à travers 8 sous-versions de correctifs de
comportement. Passer à `"11.0"` casse deux contrôles existants : le
`begins_with("10")` du reconstructeur (§3.9.2) et la vérification de
l'exporteur d'assets (§3.3). Décision couplée : soit bump + fenêtre de compatibilité
acceptant `10.*` et `11.*`, soit conserver `"10.0"` et ajouter un champ
`pipeline_version` distinct qui, lui, bouge à chaque correctif de comportement.

---

## 14.5 Correctifs mécaniques à embarquer (ce ne sont pas des décisions)

Repérés en §12, à traiter pendant la généralisation — chacun précédé d'une **mesure**,
jamais d'une correction à l'aveugle (§13.A.1) :

| # | Point | Mesure préalable | §
|---|---|---|---|
| 1 | `asset_inventory.skeletal_mesh_assets` toujours à `0` | comparer au registre réel | §12.1 |
| 2 | `component_transform()` brut sur `particle` et sur tous les composants de Blueprint | compter les Decal/Niagara vivant sous un Blueprint dans le manifeste actuel — si le compte est non nul, c'est un vrai trou de couverture des correctifs V10.3/V10.8 | §12.2 |
| 3 | `DECAL_SIZE_SCALE := 0.9` déclarée, documentée, jamais référencée | vérifier l'empreinte réelle des décals avant/après branchement | §12.8 |
| 4 | `stats["recomputed_li_transforms"]` imprimé, jamais incrémenté | supprimer le compteur ou l'implémenter — pas laisser un 0 trompeur | §12.9 |
| 5 | numérotation d'étapes flottante entre en-têtes (« script 4 » vs « script 5 sur 6 ») | — | §12.5 |

---

## 14.6 Ordre de travail proposé

| Jalon | Contenu | Débloque |
|---|---|---|
| **M0** | Extraction `MapBuilder` + `entry_headless.gd` + **test d'équivalence §14.0** | tout le reste ; sans ça l'orchestrateur n'existe pas |
| **M1** | `core/config.py`, `run_id`, `config_hash`, config résolue embarquée dans le manifeste | gates G3/G4, fin de la classe de bugs §12.4 |
| **M2** | Empaquetage `Content/Python/ue2godot/` + `run(cfg)` sur step1 et step2 | importabilité (§6.8, §9.1), fin du chargement relatif de `png_codec` |
| **M3** | `crosscheck.py`, `StepReport`, `orchestrator/pipeline.py` avec G0→G6, step5 automatisée | pipeline pilotable de bout en bout |
| **M4** | Profils : décals (§9.3) puis VFX (H7) | réutilisation sur une 2ᵉ map |
| **M5** | Trous : skeletal (H5), multi-landscape (H6), audio (H8) | couverture |

**Axes de test obligatoires à chaque jalon** — la leçon transversale de §7 : presque
tous les bugs de ce projet ont la même signature, « ça marche pour le cas simple et
casse silencieusement dès qu'un cas complexe apparaît ». Toute validation doit couvrir
les 4 axes, jamais le cas trivial seul :

1. rotation non-identité (pas seulement de la translation — §7.5) ;
2. acteur d'origine Blueprint (pas seulement `StaticMeshActor` — §7.8) ;
3. LevelInstances imbriqués à profondeur ≥ 2 (§7.7) ;
4. ISM/HISM multi-instance (§7.4) ;
5. et, pour tout ce qui touche aux axes : **un objet asymétrique**, jamais un pilier
   (§13.A.3).


---

# §15 — Analyse profonde du dépôt `convertisseur` (session 1 : diagnostic, pas de correctif)

> Méthode : lecture du dépôt entier (`main.py` 4532 lignes, `ue2godot/` en totalité,
> `godot/addons/ue2godot/` en totalité) croisée avec les 9 fichiers de référence
> (racine du dépôt) et avec `UE5_TO_GODOT_PIPELINE_SOURCE_OF_TRUTH.md`. Chaque
> affirmation ci-dessous est vérifiée par lecture directe du code, pas déduite —
> les chemins et numéros de ligne sont donnés pour que tu puisses re-vérifier
> chaque point toi-même en un coup d'œil.
>
> Portée : **diagnostic seul**, comme demandé. Rien n'est corrigé ici.

---

> **MISE À JOUR (session courante, postérieure à ce diagnostic)** : le premier
> point de la famille "Câblage" listée en §15.10 a été entamé — pas terminé.
> `main.py` n'appelle plus `step1_manifest`/`step2_meshes` en dur, et
> n'appelle plus non plus `PipelineOrchestrator.run_unreal_steps()`
> directement (ce qui aurait de toute façon été impossible, voir §16.1). Il
> passe maintenant par `ue2godot.ue.entry.run_step`, exécuté à la main par
> l'utilisateur dans la console Python d'Unreal, une étape à la fois, dans
> l'ordre `preprocess → manifest → meshes → landscape → decals_vfx`. Détails,
> bugs rencontrés et statut de validation empirique : **§16, à la fin de ce
> document.** Les affirmations "0 appelant" du tableau §15.5 concernant
> `UERemoteAdapter` et `entry.run_step` (indirectement) ne sont donc plus
> exactes telles quelles — voir annotations en ligne ci-dessous.

---

## 15.0 Verdict en une phrase

Le dépôt contient **deux systèmes construits en parallèle qui ne se parlent pas** —
un framework `ue2godot/` correctement architecturé (il respecte presque au mot près
le plan de la session précédente, §14) et une application `main.py` qui prétend
l'orchestrer mais n'en utilise que 2 fonctions sur une douzaine — et, séparément,
**la logique difficilement acquise des 9 scripts de référence (§3, §6, §7, §13 de la
source de vérité) a été redigérée en versions très amputées** lors du portage vers le
framework, avec une perte de fidélité qui va du cosmétique au fonctionnellement
cassé. Les deux problèmes sont indépendants et se corrigent séparément.

---

## 15.1 Carte du dépôt — ce qui existe, ce qui est branché

```
main.py (4532 lignes, PySide6)
 │
 ├─ importe réellement et appelle :
 │    ue2godot.core.config.ResolvedConfig            (ligne 95, 2582, 890)
 │    ue2godot.orchestrator.step5_copy.copy_step5     (ligne 96, 892)
 │    ue2godot.ue.steps.step1_manifest.run            (ligne 2586, bouton "Exports Unreal" only)
 │    ue2godot.ue.steps.step2_meshes.run              (ligne 2587, même bouton)
 │
 └─ n'importe JAMAIS et n'appelle JAMAIS :
      ue2godot.ue.steps.step0_preprocess
      ue2godot.ue.steps.step3_landscape
      ue2godot.ue.steps.step4_decals_vfx
      ue2godot.ue.entry.run_step               (le dispatcher générique par étape)
                                                 [MIS À JOUR §16 : appelé désormais,
                                                 mais depuis la console Unreal, pas
                                                 depuis ce process — main.py ne fait
                                                 que générer la commande]
      ue2godot.orchestrator.pipeline.PipelineOrchestrator
                                                 [MIS À JOUR §16 : PipelineOrchestrator
                                                 lui-même reste non instancié pour
                                                 run_unreal_steps() — seule sa méthode
                                                 crosscheck() est maintenant appelée]
      ue2godot.orchestrator.adapters.godot_cli.GodotCLIAdapter
      ue2godot.orchestrator.adapters.ue_remote.UERemoteAdapter
                                                 [MIS À JOUR §16 : branché — c'est lui
                                                 qui génère la commande à coller dans
                                                 Unreal]
      ue2godot.core.crosscheck.crosscheck_json_outputs
      ue2godot.core.schema.validators.*
      ue2godot.ue.classify.*
```

Et à l'intérieur même de `ue2godot/`, une deuxième coupure :

```
ue2godot/orchestrator/pipeline.py
    class PipelineOrchestrator.run_pipeline():
        # gates G0→G6 promis par le docstring ("Main pipeline state machine
        # and gates G0-G6") : AUCUNE implémentée
        return [copy_step5(self.cfg)]     # <- c'est tout. 1 ligne utile sur 21.
```

`PipelineOrchestrator` n'est lui-même appelé par **rien** (`grep` confirmé, §15.5).
Le fichier qui porte le nom « pipeline » et le rôle documenté d'orchestrateur
central est donc une coquille vide, non branchée, qui ne fait que dupliquer —
en pire — ce que `main.py` fait déjà directement à la ligne 892.

**Conséquence directe** : les garde-fous G0–G6 conçus en §14.3 de la session
précédente (vérification croisée des `run_id`, `readiness`, ordre d'exécution)
existent en code (`crosscheck.py` les implémente correctement, voir §15.6) mais
ne protègent absolument rien, puisque rien ne les appelle avant de lancer une
étape suivante.

---

## 15.2 Paradoxe n°1 — ta plainte exacte, avec la preuve

> *« tu peux jamais utiliser une fonction seule comme autogeneration du material
> a landscape avec configuration du material reperer dans un folder specifiquement
> nommee landscape_material »*

Preuve, en deux temps.

**(a) La fonction existe, isolée, correctement écrite** :
`ue2godot/ue/landscape_material_config.py::auto_configure_landscape_material()` —
prend un acteur Landscape et un `target_folder` (défaut `/Game/LandscapeMaterials`),
cherche un matériau existant, sinon scanne le dossier et assigne. C'est exactement
la fonction atomique que tu décris.

**(b) Elle n'est appelable QUE depuis l'intérieur de `step3_landscape.run()`**,
jamais seule :

```python
# ue2godot/ue/steps/step3_landscape.py, ligne 67
from ue2godot.ue.landscape_material_config import auto_configure_landscape_material
```

Cet import est **local à la fonction `run()`**, pas au niveau du module — un
signal fort que personne n'a envisagé de l'appeler autrement que noyée dans
tout le reste de l'étape 3 : découverte des acteurs Landscape (lignes 45-51),
mesure des bounds (lignes 82-97), **puis seulement** configuration matériau
(lignes 67-77), **puis** raycast 256×256, **puis** écriture GLB, **puis**
patch de deux fichiers JSON. Impossible d'obtenir « juste assigne le matériau
et dis-moi ce qui a été trouvé » sans déclencher les ~100 lignes qui suivent.

**Et le plus révélateur : l'UI elle-même a déjà le bon modèle mental, mais
aucun code derrière.** `main.py` ligne 756-775 déclare deux étapes de pipeline
séparées :

```python
PipelineStep("landscape", "Landscape",
             "Génération/préparation du terrain et de ses données."),
# ... (après manifest, meshes) ...
PipelineStep("landscape_bake", "Landscape Bake",
             "Produire les données de landscape exploitables côté Godot."),
```

Deux concepts distincts, dans le bon ordre relatif (préparation du matériau
tôt, bake tard) — **exactement la décomposition qu'il faudrait**. Mais
`PIPELINE_STEPS` n'est référencée nulle part ailleurs dans tout le fichier
(`grep -n "PIPELINE_STEPS\b" main.py` ne retourne que sa propre définition,
ligne 750) : c'est une liste **morte**, jamais rendue, jamais exécutée. Il
n'existe dans tout le dépôt **qu'une seule fonction Landscape**
(`step3_landscape.run`), qui fait les deux choses à la fois, sans jamais
avoir été découpée pour correspondre au modèle à deux étapes que l'UI décrit
déjà.

> *« ou par exemple il peut jamais lance un script d'export seul map ou assets
> presents dans la map »*

Confirmé, mais avec une nuance importante : **au niveau du framework, ce n'est
pas vrai** — `ue2godot/ue/entry.py::run_step(step_name, run_config_path)`
existe précisément pour ça, avec un dictionnaire `STEPS` qui expose
`preprocess`, `manifest`, `meshes`, `landscape`, `decals_vfx` comme cinq
invocations indépendantes, chacune écrivant son propre rapport JSON. C'est
la bonne pièce, au bon endroit.

**Le problème est que `main.py` ne l'utilise pas.** Le seul bouton d'export
de l'UI (`run_unreal_export_steps`, ligne 2567) appelle en dur `step1_manifest`
puis `step2_meshes`, sans jamais passer par `entry.run_step`, et sans aucun
moyen dans l'UI de choisir *seulement* manifest, ou *seulement* meshes, ou
d'atteindre `landscape`/`decals_vfx`/`preprocess` du tout. Le manque n'est pas
dans le framework — il est entre le framework et l'unique bouton qui le pilote.

> **MISE À JOUR §16** : ce paragraphe décrit l'état d'AVANT la session
> courante. `run_unreal_export_steps` passe désormais par `entry.run_step`
> pour les 5 étapes (`preprocess`, `manifest`, `meshes`, `landscape`,
> `decals_vfx`), plus de code en dur pour manifest/meshes seuls. Le "manque
> entre le framework et l'unique bouton" est donc comblé pour l'atteignabilité
> des 5 étapes — mais **pas remplacé par une exécution automatique** :
> `run_step` tourne toujours à l'intérieur de l'éditeur Unreal, sur commande
> collée à la main par l'utilisateur, parce que `import unreal` réel n'existe
> que là. Voir §16.1 pour pourquoi un appel automatique depuis `main.py`
> reste structurellement impossible sans un vrai pont réseau (non implémenté).

---

## 15.3 Paradoxe n°2 — les toggles de la page Options sont décoratifs

`DEFAULT_CONFIG["construction"]["modules"]` (lignes 127-139) présente à
l'utilisateur des interrupteurs `landscape` / `materials` / `decals` /
`lighting` / `vfx` / `audio` / `level_instances`, comme s'ils contrôlaient
individuellement quelles étapes de conversion s'exécutent. `prepare()`
(ligne 838) les sérialise dans le plan (`selected_modules`) — mais **ce plan
n'est ensuite jamais lu par du code qui exécuterait ou sauterait une étape
en conséquence**, parce qu'aucune étape autre que la copie de fichiers n'est
jamais lancée par l'orchestrateur (§15.5).

La page 05 « Reconstruction » est honnête là-dessus — plus honnête que la page
03 ne le laisse deviner : son propre texte dit noir sur blanc « les 3 premières
lignes sont exécutées par cet orchestrateur [validation, plan, copie]. La
dernière reste un geste manuel » (ligne 2231). Donc l'application sait et
documente qu'elle ne fait que la copie — mais la page Options, trois écrans
plus tôt, présente sept interrupteurs de modules qui donnent l'impression
inverse. **C'est le paradoxe que tu nommes : deux parties de la même
application se contredisent sur ce que l'application fait réellement.**

---

## 15.4 Paradoxe n°3 — l'ordre déclaré contredit l'ordre réel

Rappel de la source de vérité (§1) : *« manifeste → assets → Landscape EN
DERNIER »* — parce que le Landscape et les décals **patchent** (append) des
JSON que le manifeste et l'export de meshes **réécrivent en entier**
(`from scratch`). Relancer manifest/meshes après landscape efface son travail
silencieusement.

`PIPELINE_STEPS` (main.py, ligne 750) déclare l'ordre :
`preprocess → landscape → manifest → meshes → landscape_bake → decals → output → godot`.

Landscape apparaît **avant** manifest. Même en admettant la lecture la plus
charitable — que ce « landscape » précoce désigne uniquement la préparation
matériau et que « landscape_bake » (après meshes) désigne le raycast+GLB —
cette lecture ne correspond à **aucune fonction réelle du dépôt** : il n'y a
qu'un seul `step3_landscape.run()`, qui fait les deux à la fois et qui, dans
son propre code, **lit et patche un manifest qui doit déjà exister**
(`manifest_path = os.path.join(export_root, "level_manifest_v10.json")`,
ligne 27, ouvert en lecture ligne 155 s'il existe). Le placer avant `manifest`
dans la liste UI est donc non seulement en contradiction avec la règle
documentée, mais avec le code lui-même tel qu'il est écrit aujourd'hui.

(Cette liste étant morte — §15.2 — elle ne casse rien en pratique. Mais elle
document un ordre faux, ce qui est pire que ne rien documenter : quiconque
s'y fie pour comprendre le pipeline se trompera.)

---

## 15.5 Code mort recensé (vérifié par `grep`, zéro appelant hors définition)

| Fichier | Symbole | Rôle prévu | Appelants réels |
|---|---|---|---|
| `orchestrator/pipeline.py` | `PipelineOrchestrator` | état G0-G6 | **0** |
| `orchestrator/adapters/godot_cli.py` | `GodotCLIAdapter` | lance Godot headless (`--import` puis `--script`) | **0** |
| `orchestrator/adapters/ue_remote.py` | `UERemoteAdapter` | exécution distante Unreal | ~~0~~ **1** (`main.py::run_unreal_export_steps`, session courante — §16) |
| `core/crosscheck.py` | `crosscheck_json_outputs` | détecte une relance manifest/meshes après landscape/decals (la règle §15.4) | **0** |
| `core/schema/validators.py` | `validate_manifest_schema`, `validate_asset_map_schema`, `validate_decal_map_schema` | valider les 3 JSON avant de les consommer | **0** |
| `ue/classify.py` | `classify_actor`, `classify_component` | classification externalisée par règles | **0** — remplacée par une copie non synchronisée dans `step1_manifest.py` (§15.7) |
| `main.py` | `PIPELINE_STEPS` | modèle de pipeline affiché | **0** (référencée nulle part après sa définition) |
| `ue/steps/step0_preprocess.py`, `step3_landscape.py`, `step4_decals_vfx.py` | `run()` | 3 des 5 étapes Unreal | ~~**0 depuis l'UI**~~ **atteignables depuis §16** — `entry.run_step` est désormais généré par `main.py` et appelé manuellement dans la console Unreal ; `preprocess` et `manifest` validés `OK` en conditions réelles au moment de cette mise à jour, `meshes`/`landscape`/`decals_vfx` en attente de validation (§16.3) |

Ce n'est pas juste « du code inutilisé » à balayer — chacune de ces pièces
**duplique une intention déjà présente ailleurs en pire** (ex. `main.py`
réimplémente à la main, en pire, ce que `PipelineOrchestrator` +
`GodotCLIAdapter` étaient censés faire ensemble). Corriger ce paradoxe n'est
donc pas « supprimer le code mort », c'est **choisir lequel des deux chemins
devient la seule vérité** — probablement brancher `main.py` sur le chemin
`orchestrator/` plutôt que le contraire, puisque le chemin `orchestrator/`
est celui qui porte réellement les garde-fous.

---

## 15.6 Ce qui, dans `ue2godot/orchestrator/` et `core/`, est fiable tel quel

Pour ne pas tout mettre dans le même sac — une partie de ce code mort est du
**bon** code mort, prêt à être branché sans réécriture :

- `core/crosscheck.py` implémente correctement la détection de divergence de
  `run_id` entre manifest/asset_map/decal_map (§14.2, la garde contre une
  relance qui efface le travail du Landscape) — juste jamais appelée.
- `orchestrator/adapters/godot_cli.py` implémente correctement les deux passes
  `--import` puis `--script entry_headless.gd` décrites en §14.0 — juste
  jamais instanciée.
- `orchestrator/step5_copy.py` est solide : vérifie le sha256 après copie
  (ligne 20, 58-59), gère les deux emplacements possibles des JSON. C'est la
  seule étape réellement fiable de bout en bout dans tout le dépôt.
- `ue/steps/step0_preprocess.py` et `step2_meshes.py` sont fidèles à la
  source de vérité (KEEP_WORLD explicite sur les 3 règles, idempotence par
  taille de fichier, validation du header `glTF`, hash SHA1 anti-collision
  de nom de fichier) — voir détail en §15.7, ce sont les deux seules étapes
  Python qui s'en sortent bien.
- `godot/addons/ue2godot/core/transform.gd` est fidèle au signe et au
  déterminant validés empiriquement (§13.A.5) — voir détail §15.8.
- `core/axis.py`, `ue/raycast.py` sont fidèles.

Ces pièces-là n'ont pas besoin d'être réécrites. Le travail de correction du
paradoxe n°1/2/3 consiste largement à **les relier**, pas à les refaire.

---

## 15.7 Effondrement de fidélité côté Unreal (Python)

### `step1_manifest.py` — le cœur du système, 489 lignes contre 7609

C'est la régression la plus grave du dépôt, et son propre en-tête la dément :
*« Reframed generalist step 1 using the complete V10-8 manifest scanner
algorithm »* (ligne 6) — c'est faux. Comparaison point par point :

| Comportement documenté (source de vérité) | Statut dans `step1_manifest.py` |
|---|---|
| Découverte d'acteurs par `ObjectIterator(unreal.Actor)` filtré par niveau — marqué **NE PAS REMPLACER**, survécu à 10 versions (§3.2.2) | **Remplacé** par `EditorActorSubsystem.get_all_level_actors()` (ligne 369-370), exactement la méthode que le commentaire interdit de reconsidérer « sans preuve nouvelle » |
| Registre canonique des LevelInstances, `instance_chain`, `children_actor_paths`, descente récursive `collect_level_contents()` jusqu'à profondeur 64 (§3.2.3) | **Absent.** `level_instances` reste `{"placements": [], "hierarchy": [], "registry": {}}` toute l'exécution — jamais peuplé. `MAX_LEVEL_INSTANCE_DEPTH = 64` est déclarée (ligne 28) et jamais utilisée. |
| ISM/HISM : transform **par instance** via `get_instance_transform(i, world_space=True)`, correctif mesuré du bug V10.4 (jusqu'à 165 instances perdues sur un seul foliage, §7.4) | **Régression directe.** Les kinds `instanced_mesh`/`hierarchical_instanced_mesh` sont traités dans la même branche que `static_mesh` (ligne 408), avec un seul `tf_value` — un ISM à 165 occurrences ne produira à nouveau qu'1 placement. |
| Composition de chaîne LI (`compose_chain_transform`) appliquée à chaque géométrie/lumière/décal/audio (§3.2.6 point 5-6) | La fonction `compose_chain_transform` (ligne 323) est **correctement écrite** (utilise `source_transform`, pas `final_world_transform`, évite le double comptage §7.7) mais **n'est appelée nulle part dans `run()`** — code mort par construction, puisque le registre LI dont elle dépend n'est jamais peuplé (point précédent). |
| Détail composant par composant des Blueprints (`blueprints.actors[]`, §3.2.5 point 2, §12.2) | **Absent.** Aucune clé `blueprints` dans `manifest_data` (ligne 373-387). |
| Registres matériaux/textures avec résolution des paramètres (`material_full_info`, `texture_registry`, §3.2.7) | **Stubs vides jamais peuplés** — `"materials": {"unique_materials": {}}`, `"textures": {"unique_textures": {}}}` restent tels quels toute l'exécution. |
| `conversion_diagnostic`, `conversion_risks[]`, `warnings[]`, `asset_inventory` (§4, schéma documenté) | **Absents du schéma entier** — aucune de ces clés de premier niveau n'existe dans `manifest_data`. |
| Contrat `reconstruction.ready_for_godot_*` : `True` **seulement si** zéro échec transform + zéro mesh manquant + zéro échec LI + zéro échec instance (§3.2.8) | **Codé en dur `True` inconditionnellement** (ligne 386 : `"reconstruction": {"ready_for_godot_geometry": True, "ready_for_godot_fx": True}`) — jamais recalculé après le scan. Un manifeste avec 100 % d'échecs de transform déclarerait quand même « prêt pour Godot ». C'est la négation exacte de la raison d'être de ce champ (§3.2.8 : *« pour qu'un échec silencieux sur les décals ne soit jamais masqué par un statut global ready »*). |
| Rotator en kwargs nommés, jamais positionnels (V10.6, §7.6) | **Fidèle** — `dict_to_unreal_transform` (ligne 289-297) utilise bien `roll=`, `pitch=`, `yaw=`. |
| Base du repli propriétés = `actor_transform`, jamais la relative du composant racine (V10.8, §7.8) | **Fidèle** — `component_transform_via_properties` (ligne 120-165) exclut bien la relative du composant dont `attach_parent is None`. |
| Pattern `(valeur, erreur)`, jamais avaler une exception (§6.3) | **Partiellement fidèle** pour les transforms ; **absent** pour tout le reste (pas de comptage `empty_mesh_slot_count` vs `missing_mesh_references`, aucun diagnostic catégorisé). |

En clair : les deux ou trois correctifs les plus « mécaniques » et faciles à
isoler (ordre des kwargs Rotator, base de repli sur l'acteur) ont survécu,
parce qu'ils tiennent dans de petites fonctions autonomes faciles à recopier.
Tout ce qui demandait de porter un **système** entier — le registre LI, les
registres matériaux/textures, les diagnostics catégorisés, la classification
Blueprint, le contrat de readiness réellement calculé — n'a pas survécu le
portage. Le fichier fonctionne (il produit un JSON valide), mais sur une
vraie scène de 5929 acteurs il reproduirait très probablement le bug V10.4
(ISM tronqués) tel quel, et ne détecterait jamais un LevelInstance profond
mal placé puisqu'il n'y a plus de LevelInstance du tout dans le manifeste.

### `step3_landscape.py` — le bake de texture a disparu

Fidèle sur : découverte des acteurs Landscape, mesure des bounds, appel au
raycast en grille (`raycast_grid_heights`, réutilisant le module unifié —
bonne chose, §14 H3 tranché de facto en faveur de la factorisation),
écriture GLB, patch idempotent de l'asset map et du manifeste.

**Manquant entièrement : la moitié « texture » de l'étape** (§3.4, « Méthode
texture — bake orthographique »). Rien dans `step3_landscape.py` ne spawn de
`SceneCapture2D`, ne configure `PRM_USE_SHOW_ONLY_LIST`, ne capture en
`SCS_BASE_COLOR`, n'exporte de PNG. L'appel final à `write_glb()` (ligne 141)
passe seulement `positions, normals, uvs, indices` — jamais de `png_bytes`.
Résultat : le Landscape reconstruit en Godot sera un maillage **sans aucune
texture**, alors que c'est précisément le point qu'Oumi avait validé comme
acquis dans le pipeline de référence (« le rendu correspond à celui d'UE5 »
une fois le terrain bake — mémoire du projet). C'est un retour en arrière
complet sur ce résultat.

En plus, les UV générés par `core/glb.py::build_grid_mesh` (`u = ix/res_x,
v = iy/res_y`, une paramétrisation grille-espace naïve) ne correspondent
**pas** à la convention documentée de la caméra de capture orthographique
(« screen right = world +Y, screen up = world +X », §3.4) — même si le bake
était réactivé tel quel demain, ces UV ne pointeraient pas vers les bons
pixels de la texture. Les deux pièces à réunir (bake + UV corrects) doivent
être reconstruites ensemble, pas l'une sans l'autre.

### `step4_decals_vfx.py` — écrit une carte de décals qui ne mène à rien

Aucune des trois pièces documentées en §3.6 n'est présente :

- **Pas de résolution de texture.** `decal_materials[mat_path]["tint"]` est
  systématiquement `cfg.get("decals.tint_fallback", [1,1,1])` (ligne 60) —
  le blanc de repli, **pour chaque décal**, jamais la vraie valeur lue sur
  le matériau. La cascade de résolution (override instance → paramètre par
  défaut du parent → scan de sous-chaîne, §3.6) n'existe pas dans ce fichier.
- **Pas de bake.** `godot_tex_path` (ligne 59) est construit comme un nom de
  fichier prévu, mais rien n'écrit jamais ce PNG. Ni `png_codec`, ni aucun
  `draw_material_to_render_target`, ni `export_render_target` n'apparaissent
  dans ce fichier — `grep -n "png_codec" ue2godot/ue/steps/step4_decals_vfx.py`
  ne retourne rien.
- **Pas de détection de masque plat.** Le garde-fou qui signalait un décal
  rectangulaire opaque (§3.6) n'a pas de raison d'exister puisqu'il n'y a
  plus de masque du tout à analyser.

Côté Godot, `decal_builder.gd` charge ce `godot_path` (ligne 30-32) qui
pointe vers un fichier qui n'existe jamais — `load(tex_path)` échouera
silencieusement (`if tex != null`, ligne 31) sur chacun des 1423 décals, qui
apparaîtront donc dans la scène reconstruite comme des `Decal` vides, sans
texture ni teinte. Les deux bouts de la chaîne (script Unreal, script Godot)
sont chacun structurellement corrects en isolation mais **aucun des deux ne
produit ou ne consomme la donnée réelle** — c'est un pipeline qui « marche »
au sens où rien ne plante, mais qui ne convertit visuellement rien.

---

## 15.8 Effondrement de fidélité côté Godot (le reconstructeur)

Le fichier de référence faisait 2283 lignes. Les 5 fichiers du nouvel addon
en font 235 au total (hors `entry_*.gd`). Répartition de ce qui a survécu :

### `transform.gd` (49 lignes) — fidèle, le seul module à 100 %

Signes de `qx`/`qy` corrects (le `-` du correctif V10.7 est bien là, ligne
19, avec le commentaire d'origine conservé), matrice `C` de conversion à
déterminant -1 correcte (§13.A.5), mapping `godot_pos` correct. **C'est le
cœur mathématique le plus critique du pipeline — le seul entièrement
préservé.**

Une nuance à noter pour la suite : `transform_from_v10()` est déclarée
`-> Transform3D` typée (jamais nulle), alors que l'original la déclarait
délibérément sans type de retour pour pouvoir renvoyer `null` sur donnée
malformée (§3.9.4, §13.B.8). Ici, des données manquantes produisent un
`Transform3D` construit sur des valeurs par défaut silencieuses
(`data.get("location", [0.0, 0.0, 0.0])`, ligne 33) plutôt qu'un signal
d'échec remonté à l'appelant — une régression du pattern « ne jamais avaler
une erreur » (§6.3), même si le calcul lui-même reste correct.

### `geometry_builder.gd` (47 lignes) — le squelette sans le corps

Fidèle : les 3 branches `missing_asset_mapping` / `missing_glb` /
`instantiate_failure` du système de tolérance par catégorie existent bien
(lignes 12, 17, 28), avec skip individuel plutôt qu'abandon global — la
philosophie du correctif V10.4 (§3.9.3) est comprise et respectée pour ces
trois-là.

**Manquant, deux des cinq `FAIL_ON_*` documentés** : aucune notion de
`transform_failure` (aucun test sur la qualité du transform avant de
l'appliquer), et aucune déduplication `duplicate_placement_id` (pas de
`seen_placement_keys`, aucun `placement_id` n'est même lu ou vérifié).

**Manquant, tout le système ISM/HISM** (§3.9.3, `instance_final_world_
transforms[]`, spawn d'un nœud par instance réelle). La fonction traite
inconditionnellement chaque `placement` comme un mesh statique unique
(`glb_resource.instantiate()`, un seul appel, ligne 30). Cohérent avec le
fait que `step1_manifest.py` ne produit de toute façon plus ces données
(§15.7) — les deux régressions se répondent, mais ça signifie que même après
correction du Python, le GDScript ne saurait toujours pas en tirer parti
sans être lui-même complété.

### `decal_builder.gd` (42 lignes) — le calcul de taille survit, tout le reste non

**Fidèle** : le réarrangement `(largeur, épaisseur, hauteur) →
Vector3(w, th, h)` (ligne 22-25) reproduit exactement la convention
documentée en §3.8 — c'est un détail facile à rater et il est correct.

**Manquant** : le tint n'est jamais appliqué au nœud `Decal` (aucune lecture
de `mat_info["tint"]`, aucune assignation à `modulate`) ; aucun
préchargement/dédoublonnage par matériau (§3.8 : « une seule fois par
matériau, partagée par les 781 placements » — ici `load(tex_path)` est
rappelé à chaque placement, 1423 fois) ; aucune métadonnée de traçabilité
(`set_meta` ue_actor_path/ue_material_path, §3.8) ; et — cf. §15.7 — le
fichier texture qu'il tente de charger n'existe jamais.

### `vfx_builder.gd` (31 lignes) — ~1000 lignes réduites à des marqueurs plats

Ce fichier ne fait qu'une chose : un `Marker3D` par composant Niagara, groupé
sous `VFX_MARKERS_NOT_CONVERTED`. C'est *honnête* au sens de §13.C.24 (ne
jamais simuler une conversion qui n'a pas eu lieu) — mais c'est la totalité
de ce qui reste du système de substitution documenté en §3.9.6 : les 12
catégories par mot-clé (ordonnées, `candle` avant `flame`), les 8 lois de
panache physique réduites, le budget de 24 lumières avec espacement minimal,
la surcouche « embers » séparée du corps de flamme, la calibration de
hauteur torch/candle, le shader parcel désactivable — **rien de tout cela
n'existe**. Aucun `OmniLight3D`, aucune particule, aucune courbe.

Contrairement aux autres pertes de fidélité de ce document, celle-ci pourrait
être un choix délibéré — c'est très exactement l'option « markers_only »
identifiée comme decision humaine H7 dans le document d'architecture
précédent (§14.4). Mais rien dans le dépôt ne documente que c'est un choix
plutôt qu'un oubli : ni commentaire, ni entrée de config qui basculerait
vers un mode `substitutes`. Sans trace de décision, il faut la traiter comme
une perte, pas comme un choix, jusqu'à preuve du contraire de ta part.

### `map_builder.gd` (66 lignes) — orchestration minimale, deux trous supplémentaires

Pas de `_validate_manifest_and_asset_map()` équivalent (§3.9.2) : aucune
vérification en amont que chaque `ue_path` de `unique_meshes` a une entrée
dans l'asset map — la validation est purement locale à chaque placement,
sans passe de cohérence globale préalable.

Pas de construction des métadonnées de LevelInstance (`_build_li_metadata_
nodes()`) — cohérent avec l'absence du système LI côté Python (§15.7), mais
signale une fois de plus que les deux bouts du pipeline ont perdu la même
pièce en miroir.

**Nouveau trou, pas dans la version de référence** : `step4_decals_vfx.py`
écrit désormais des marqueurs audio dans `decal_map["audio"]["markers"]`
(ligne 74-79 de `step4_decals_vfx.py` — une extension du framework, absente
du pipeline de référence, qui répond en apparence à la décision H8 laissée
ouverte en §14.4). Mais **`map_builder.gd` ne lit jamais cette clé** —
`grep -n "audio" godot/addons/ue2godot/core/map_builder.gd` ne retourne
rien. Le Python produit une réponse à H8 que le GDScript ignore entièrement :
un nouveau désaccord entre les deux côtés du pipeline, du même type que ceux
déjà documentés en §10/§12 de la source de vérité, mais introduit après
coup plutôt qu'hérité.

---

## 15.9 Deux implémentations concurrentes de la même chose

Au-delà du code mort simple (§15.5), il existe au moins un cas de **duplication
active et divergente** : la classification acteur/composant existe en deux
endroits séparés, avec des taxonomies différentes, sans qu'aucun des deux ne
soit dérivé de l'autre.

| | `ue/classify.py` (jamais appelé) | `step1_manifest.py` (réellement utilisé) |
|---|---|---|
| Catégories acteur | `level_instance`, `static_mesh`, `landscape`, `light`, `vfx`, `decal`, `audio`, `generic` | `world_partition_system`, `level_instance`, `landscape`, …, `spline`, `collision`, `camera`, `ui`, `other` (13 catégories) |
| Règles externes | Oui — `rules` en paramètre, teste `match in cname` (liste ordonnée) | Non — entièrement en dur dans la fonction |

Si quelqu'un modifie un jour la classification en pensant agir sur le
comportement réel (parce que `classify.py` a l'air d'être *le* module
dédié à ça, avec un nom qui l'annonce), rien ne changera dans le manifeste
produit — la modification tombera dans le mauvais fichier. C'est un piège
pour la prochaine session de travail, pas seulement un vestige inoffensif.

---

## 15.10 Ce que ce diagnostic implique pour la suite (sans encore agir)

Trois familles de correctifs bien distinctes ressortent, à ne pas mélanger
en une seule passe :

1. **Câblage** (§15.1-§15.5) — relier `main.py` au chemin `orchestrator/`
   plutôt qu'à ses appels directs actuels ; supprimer ou brancher
   `PIPELINE_STEPS` ; faire de `entry.run_step` le seul point d'entrée par
   étape, y compris depuis l'UI. Aucune de ces corrections ne touche à la
   logique métier — c'est un problème de plomberie, mécaniquement le moins
   risqué des trois.

   > **MISE À JOUR §16 — EN COURS** : « faire de `entry.run_step` le seul
   > point d'entrée par étape, y compris depuis l'UI » est fait pour les 5
   > étapes Unreal. Toujours ouvert dans cette famille : `PIPELINE_STEPS`
   > (main.py) n'est ni supprimée ni branchée — elle reste une liste morte,
   > et son ordre reste faux (§15.4) ; les toggles de la page Options restent
   > décoratifs pour les étapes 0-4 (§15.3, seule `step5_copy` en tenait déjà
   > compte) ; `PipelineOrchestrator.run_pipeline()` (§15.1, gates G0-G6) n'a
   > pas été touché.

2. **Fidélité côté Unreal** (§15.7) — `step1_manifest.py` doit récupérer le
   système LevelInstance, l'ISM/HISM par instance, et un contrat de
   readiness réellement calculé, avant que le reste du pipeline ait un sens
   sur une vraie map de la taille de Necropolis. `step3_landscape.py` doit
   récupérer le bake de texture. `step4_decals_vfx.py` doit récupérer la
   cascade de résolution + le bake par render target + `png_codec`.

3. **Fidélité côté Godot** (§15.8) — dépend en partie de (2) : le portage
   ISM/HISM et les 2 `FAIL_ON_*` manquants dans `geometry_builder.gd` n'ont
   de sens qu'une fois que `step1_manifest.py` les produit à nouveau. Le
   système VFX (H7) et l'audio (map_builder.gd qui ignore ce que step4
   produit désormais) demandent d'abord une décision de portée — pas
   seulement du code.

Ce que ce document ne fait pas : il ne priorise pas entre ces trois familles,
ni entre les points à l'intérieur de chacune — c'est un travail de décision,
pas d'analyse, et tu as dit vouloir d'abord voir la carte complète avant de
choisir par où commencer.

---

## §16 — Avancement session courante (correctif, pas diagnostic)

> Contrairement à §15, cette section documente du code **effectivement
> modifié et partiellement validé en conditions réelles** (map `m1`, éditeur
> Unreal réel), pas seulement lu. Portée : uniquement le premier item de la
> famille « Câblage » identifiée en §15.10 — les familles 2 (fidélité Unreal)
> et 3 (fidélité Godot) n'ont pas été touchées.

### 16.0 Point de départ : deux bugs empilés dans `run_unreal_export_steps`

Avant cette session, une tentative précédente avait déjà remplacé l'appel en
dur à `step1_manifest` + `step2_meshes` (§15.2) par un appel à
`PipelineOrchestrator.run_unreal_steps()` — dans l'intention de corriger
exactement le paradoxe n°1. Mais cette tentative a introduit deux problèmes
empilés :

1. **Un vrai bug de câblage** : seul l'import de `PipelineOrchestrator`
   était protégé par un `try/except` — pas l'appel à `run_unreal_steps()`
   qui suivait. Une exception non rattrapée à cet endroit faisait planter
   toute l'application PySide6.
2. **Un problème d'architecture plus profond, dont (1) n'était qu'un
   symptôme** : `run_unreal_steps()` déclenche des steps qui font
   `import unreal` *pour de vrai* (`get_editor_subsystem`, etc.) — un module
   qui n'existe que dans l'interpréteur Python **embarqué par l'éditeur
   Unreal**, jamais dans le Python standalone qui fait tourner `main.py`.
   Aucun `try/except` ne peut réparer ça : appeler ces steps depuis ce
   process ne peut structurellement jamais fonctionner, avec ou sans
   exception rattrapée. C'était déjà documenté dans l'architecture (transport
   par exécution distante ou copier-coller dans la console Unreal — voir
   `ue2godot.orchestrator.adapters.ue_remote.UERemoteAdapter` et
   `ue2godot.ue.entry.run_step`, tous deux déjà présents dans le dépôt mais
   non branchés, §15.5), et la tentative précédente l'avait contourné au lieu
   de le respecter.

### 16.1 Correctif retenu — respecter le transport documenté, pas le contourner

`run_unreal_export_steps` (main.py) ne fait plus qu'orchestrer un aller-retour
manuel avec l'éditeur Unreal, en deux phases, pilotées par le même bouton
(« Générer les exports Unreal ») :

**Phase 1 — préparation (1er clic)**
- Construit un `ResolvedConfig` comme avant (modules activés, chemins).
- Écrit `run_config_<run_id>.json` dans le dossier d'export.
- Pour chacune des étapes activées (`preprocess?`, `manifest`, `meshes`,
  `landscape`, `decals_vfx`), génère via `UERemoteAdapter.build_command()`
  une ligne du type :
  ```
  import sys; sys.path.insert(0, r"<dossier contenant ue2godot>");
  import ue2godot.ue.entry as e; e.run_step("<step>", r"<run_config.json>")
  ```
- Affiche ces commandes dans une `QMessageBox`, à coller à la main dans la
  console Python d'Unreal, une par une, dans l'ordre.
- Mémorise le `run_id` en attente (`self._pending_unreal_run_id`).

**Phase 2 — lecture des résultats (clic suivant)**
- Si un `run_id` est en attente, cherche sur disque les rapports que
  `ue.entry.run_step` a écrits *depuis l'intérieur d'Unreal*
  (`step_<clé>_<run_id>.json` dans `report_dir`).
- Si un rapport manque encore → message invitant à finir de coller les
  commandes, sans régénérer un nouveau `run_id` entre-temps.
- Si tous les rapports sont là → les relit (`StepReport.load()`, nouvelle
  méthode symétrique de `save()`), affiche les statuts, puis appelle
  `PipelineOrchestrator.crosscheck()` — qui ne lit que des JSON sur disque et
  n'a jamais eu besoin de `unreal`, donc peut tourner sans problème dans le
  process de l'UI. Le `run_id` en attente est ensuite réinitialisé.

Ce que ça corrige, précisément :
- Le bug (1) n'existe plus par construction : il n'y a plus d'appel à
  `run_unreal_steps()` dans `main.py` du tout.
- Le problème (2) est traité en respectant le transport déjà prévu plutôt
  qu'en tentant à nouveau un appel direct impossible.
- `entry.run_step` devient enfin le point d'entrée réel des 5 étapes,
  atteignable depuis l'UI (§15.2, §15.5) — même si l'exécution elle-même
  reste manuelle, pas automatique.

Ce que ça NE corrige PAS (hors-scope, déjà noté en §15.10) :
- `PIPELINE_STEPS` (liste mickey-morte, ordre faux, §15.4) — inchangée.
- Les toggles de la page Options pour les étapes 0-4 (§15.3) — toujours
  décoratifs au niveau de l'UI ; seul le `cfg_dict` passé transporte déjà
  `landscape.enabled` / `decals.enabled`, que step3/step4 lisent eux-mêmes.
- `UERemoteAdapter.execute_step()` reste un vrai *fallback copier-coller*,
  pas une exécution distante automatisée (pas de socket vers le protocole
  remote-exec d'Unreal) — c'était déjà son comportement avant cette session,
  volontairement pas étendu ici pour rester dans le périmètre « câblage,
  pas nouvelle fonctionnalité ».

### 16.2 Bug annexe découvert en cours de route : `sys.path` non hérité

Premier essai réel dans Unreal → `ModuleNotFoundError: No module named
'ue2godot'`, alors que le même `import ue2godot...` fonctionne sans problème
dans le process `main.py`. Cause : la console Python embarquée d'Unreal a son
propre interpréteur et son propre `sys.path`, indépendant de celui du process
qui a généré la commande — rien n'est hérité automatiquement.

Correctif : chaque commande générée commence désormais par
`sys.path.insert(0, r"<module_root>")`, où `module_root` est calculé dans
`main.py` via `os.path.dirname(os.path.abspath(__file__))` — donc toujours
correct quel que soit l'endroit où l'utilisateur a placé le dossier de l'outil
sur sa machine, sans valeur en dur. Une astuce est aussi affichée pour ajouter
ce chemin une fois pour toutes dans *Project Settings > Plugins > Python >
Additional Paths*, afin de ne plus avoir à le répéter à chaque commande.

Effet de bord nettoyé au passage : `UERemoteAdapter.execute_step()` faisait
un `print()` du snippet dans le `stdout` du process `main.py` lui-même (donc
dans un terminal VS Code, par exemple) — une sortie qui n'a rien à voir avec
ce que reçoit réellement Unreal, et qui a semé une confusion légitime chez
l'utilisateur en test. Extrait dans une méthode pure `build_command()` sans
effet de bord ; `execute_step()` est conservée telle quelle pour compatibilité
mais n'est plus appelée par `main.py`.

### 16.3 Statut de validation empirique (map `m1`, éditeur Unreal réel)

| Étape | Statut observé (`LogPython`) |
|---|---|
| `preprocess` | ✅ `OK` — rapport écrit sur disque, relu avec succès |
| `manifest` | ✅ `OK` — rapport écrit sur disque |
| `meshes` | ⏳ en attente de confirmation de l'utilisateur |
| `landscape` | ⏳ en attente |
| `decals_vfx` | ⏳ en attente |
| Relecture des 5 rapports + `crosscheck()` (phase 2, 2ᵉ clic) | ⏳ en attente — pas encore testée de bout en bout |

Le `sys.path.insert` a bien réglé le `ModuleNotFoundError` initial : aucune
erreur d'import n'apparaît plus dans les deux exécutions confirmées. Rien
n'indique à ce stade de nouveau problème derrière — mais §16 sera à
compléter dès que les 3 étapes restantes et le second clic (phase 2) auront
été confirmés, en particulier parce que `landscape` et `decals_vfx` sont les
deux étapes dont la fidélité fonctionnelle est déjà connue comme dégradée
(§15.7) : un `status: OK` à ce niveau ne dit rien sur la qualité du contenu
produit (texture de Landscape absente, décals sans tint ni bake, §15.7-§15.8)
— seulement que le script n'a pas levé d'exception. Les deux diagnostics sont
indépendants, à ne pas confondre.
