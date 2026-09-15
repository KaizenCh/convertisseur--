# -*- coding: utf-8 -*-
"""
Landscape material folder discovery + auto-configuration.

Previously: a single hardcoded virtual content path ("/Game/LandscapeMaterials")
had to exist exactly, or nothing was assigned. That is now the FIRST thing
tried, not the only thing:

  1. Try each configured/blessed candidate path directly.
  2. If none exist, auto-discover a folder whose name matches "landscape
     material" (normalized — handles "LandscapeMaterials", "Landscape__
     Material", "landscape-material", etc.) by walking the /Game/ virtual
     tree in BOTH directions from an anchor derived from the source .umap's
     own location: up toward /Game (siblings of each ancestor folder), and
     down (descendants), bounded in depth so a huge project tree does not
     get fully walked on every run.
  3. Inside whichever folder is found, each immediate subfolder is treated
     as one material "pack" (matching the description: several folders,
     each containing material X) — every pack is inspected and the first
     one that actually yields a usable Material/MaterialInstanceConstant is
     used, with every pack that was tried recorded in the report so a human
     can see what was considered, not just what was picked.
"""

from typing import Dict, Any, List, Optional
import re

try:
    import unreal
except ImportError:
    unreal = None


_NORMALIZE_RE = re.compile(r"[^a-z]+")


def _normalize(name: str) -> str:
    return _NORMALIZE_RE.sub("", name.lower())


def _looks_like_landscape_material_folder(name: str) -> bool:
    n = _normalize(name)
    return "landscape" in n and "material" in n


def _get_sub_paths(parent_path: str, recurse: bool = False) -> List[str]:
    """Folder listing — unlike EditorAssetLibrary.list_assets(), this
    returns virtual FOLDER paths, not asset paths. get_sub_paths() is the
    correct AssetRegistry API for that; a manual fallback via list_assets
    is used if the registry method is unavailable in this engine version."""
    if unreal is None:
        return []
    try:
        registry = unreal.AssetRegistryHelpers.get_asset_registry()
        return list(registry.get_sub_paths(parent_path, recurse))
    except Exception:
        pass
    try:
        assets = unreal.EditorAssetLibrary.list_assets(parent_path, recursive=False, include_folder=True)
        folders = set()
        for a in assets:
            # list_assets returns asset paths like "/Game/X/Y/Asset.Asset";
            # the folder one level below parent_path is everything up to
            # the next "/".
            rel = a[len(parent_path):].lstrip("/")
            if "/" in rel:
                folders.add(parent_path.rstrip("/") + "/" + rel.split("/")[0])
        return list(folders)
    except Exception:
        return []


def _umap_filesystem_path_to_game_folder(umap_path: str) -> Optional[str]:
    """Derives the /Game/... virtual folder containing a .umap from its
    filesystem path, via the standard <Project>/Content/... convention —
    the only mapping a raw filesystem path can be turned into without
    asking Unreal itself (which is not always reachable from outside the
    editor, e.g. when this is only called with a path picked in the UI)."""
    if not umap_path:
        return None
    normalized = umap_path.replace("\\", "/")
    marker = "/content/"
    idx = normalized.lower().find(marker)
    if idx == -1:
        return None
    rest = normalized[idx + len(marker):]
    if rest.lower().endswith(".umap"):
        rest = rest.rsplit("/", 1)[0] if "/" in rest else ""
    return "/Game/" + rest if rest else "/Game"


def discover_landscape_material_folder(
    primary_candidates: Optional[List[str]] = None,
    map_filesystem_path: Optional[str] = None,
    max_search_depth: int = 6,
) -> Dict[str, Any]:
    """Returns {"folder": <path> or None, "tried": [...], "method": str}."""
    tried: List[str] = []

    candidates = list(primary_candidates or []) or [
        "/Game/LandscapeMaterials", "/Game/Landscape_Materials", "/Game/landscape_material",
    ]
    if unreal is not None:
        for candidate in candidates:
            tried.append(candidate)
            try:
                if unreal.EditorAssetLibrary.does_directory_exist(candidate):
                    return {"folder": candidate, "tried": tried, "method": "configured_candidate"}
            except Exception:
                continue

    if unreal is None:
        return {"folder": None, "tried": tried, "method": "unavailable"}

    anchor = _umap_filesystem_path_to_game_folder(map_filesystem_path or "") or "/Game"

    # --- search UP: check every ancestor folder's direct children -------
    segments = [s for s in anchor.split("/") if s]  # ["Game", ...]
    ancestor = ""
    checked_folders = set()
    for depth, seg in enumerate(segments):
        ancestor = ancestor + "/" + seg if ancestor else "/" + seg
        if depth > max_search_depth:
            break
        for sibling in _get_sub_paths(ancestor, recurse=False):
            name = sibling.rsplit("/", 1)[-1]
            if sibling in checked_folders:
                continue
            checked_folders.add(sibling)
            tried.append(sibling)
            if _looks_like_landscape_material_folder(name):
                return {"folder": sibling, "tried": tried, "method": "ancestor_scan"}

    # --- search DOWN: bounded breadth-first descent from /Game -----------
    frontier = ["/Game"]
    depth = 0
    while frontier and depth <= max_search_depth:
        next_frontier: List[str] = []
        for parent in frontier:
            for child in _get_sub_paths(parent, recurse=False):
                if child in checked_folders:
                    continue
                checked_folders.add(child)
                tried.append(child)
                name = child.rsplit("/", 1)[-1]
                if _looks_like_landscape_material_folder(name):
                    return {"folder": child, "tried": tried, "method": "descendant_scan"}
                next_frontier.append(child)
        frontier = next_frontier
        depth += 1

    return {"folder": None, "tried": tried, "method": "not_found"}


def _material_candidates_in_pack(pack_folder: str) -> List[Any]:
    if unreal is None:
        return []
    try:
        asset_reg = unreal.get_editor_subsystem(unreal.AssetRegistrySubsystem)
        assets = asset_reg.get_assets_by_path(pack_folder, recursive=True)
    except Exception:
        return []

    results = []
    for a in assets:
        try:
            cname = str(a.asset_class_path.asset_name) if hasattr(a, "asset_class_path") else str(a.asset_class)
        except Exception:
            continue
        if cname in ("Material", "MaterialInstanceConstant"):
            results.append(a)
    return results


def auto_configure_landscape_material(
    landscape_actor: Any,
    target_folder: str = "/Game/LandscapeMaterials",
    map_filesystem_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Inspects and auto-configures the Landscape material.

    If the landscape already has one, nothing is searched — the existing
    material is only reported on. Otherwise: discover_landscape_material_
    folder() finds the pack folder (configured path, or auto-discovered by
    name near the source map), then every immediate subfolder inside it is
    tried as one material pack until one yields a usable material."""
    result: Dict[str, Any] = {
        "status": "OK",
        "assigned_material": None,
        "paint_layers": [],
        "auto_configured": False,
        "discovery_method": None,
        "packs_considered": [],
        "notes": [],
    }

    if unreal is None or landscape_actor is None:
        result["status"] = "UNAVAILABLE"
        return result

    mat = None
    try:
        mat = landscape_actor.get_editor_property("landscape_material")
    except Exception:
        pass

    if mat is None:
        discovery = discover_landscape_material_folder(
            primary_candidates=[target_folder], map_filesystem_path=map_filesystem_path,
        )
        folder = discovery["folder"]
        result["discovery_method"] = discovery["method"]

        if folder is None:
            result["notes"].append(
                f"No landscape-material-like folder found (tried {len(discovery['tried'])} "
                "folder(s), configured candidate + name-based scan near the source map)."
            )
        else:
            # Every immediate subfolder is one "pack" — try each until one
            # actually has a usable material. If the folder itself directly
            # contains materials (no subfolders), it is tried as its own
            # single pack too.
            packs = _get_sub_paths(folder, recurse=False) or [folder]
            for pack_folder in packs:
                candidates = _material_candidates_in_pack(pack_folder)
                result["packs_considered"].append({
                    "folder": pack_folder, "material_count": len(candidates),
                })
                if not candidates:
                    continue
                loaded = None
                try:
                    loaded = candidates[0].get_asset()
                except Exception:
                    loaded = None
                if loaded is None:
                    continue
                try:
                    landscape_actor.set_editor_property("landscape_material", loaded)
                except Exception as exc:
                    result["notes"].append(f"Found {loaded.get_name()} in {pack_folder} but assignment failed: {exc}")
                    continue

                mat = loaded
                result["auto_configured"] = True
                result["notes"].append(
                    f"Auto-assigned material from pack '{pack_folder}' (found via "
                    f"{discovery['method']}): {loaded.get_name()}"
                )
                break

            if mat is None:
                result["notes"].append(
                    f"Folder '{folder}' found but none of its {len(packs)} pack(s) yielded "
                    "a usable Material/MaterialInstanceConstant."
                )

    if mat is not None:
        try:
            result["assigned_material"] = str(mat.get_path_name())
        except Exception:
            result["assigned_material"] = str(mat)

        try:
            if hasattr(mat, "get_editor_property"):
                params = mat.get_editor_property("vector_parameter_values")
                if params:
                    for p in params:
                        pname = str(getattr(p, "parameter_info", p))
                        result["paint_layers"].append(pname)
        except Exception:
            pass

    return result
