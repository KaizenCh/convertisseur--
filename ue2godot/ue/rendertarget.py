# -*- coding: utf-8 -*-
"""
Render-target bake utilities — shared by the Landscape bake (step3) and the
decal texture bake (step4).
"""

import os
import shutil
import traceback
from typing import List, Optional, Tuple, Any

from ue2godot.core.strategies import Strategy, StrategyChain

try:
    import unreal
except ImportError:
    unreal = None


def get_capture_component(capture_actor: Any) -> Any:
    component = None
    try:
        component = capture_actor.get_component_by_class(unreal.SceneCaptureComponent2D)
    except Exception:
        component = None

    if component is None:
        try:
            components = capture_actor.get_components_by_class(unreal.SceneCaptureComponent2D)
            if components:
                component = components[0]
        except Exception:
            component = None

    if component is None:
        component = capture_actor.get_editor_property("capture_component2d")

    return component


def restrict_capture_to(component: Any, actors: List[Any]) -> bool:
    try:
        component.set_editor_property(
            "primitive_render_mode",
            unreal.SceneCapturePrimitiveRenderMode.PRM_USE_SHOW_ONLY_LIST,
        )
    except Exception:
        return False

    try:
        component.set_editor_property("show_only_actors", actors)
        return True
    except Exception:
        pass

    added = 0
    for actor in actors:
        for method_name in ("show_only_actor_components", "show_only_actors_components"):
            method = getattr(component, method_name, None)
            if method is None:
                continue
            try:
                method(actor)
                added += 1
                break
            except Exception:
                continue
    if added:
        return True

    try:
        all_actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
        keep = set()
        for actor in actors:
            try:
                keep.add(actor.get_path_name())
            except Exception:
                pass
        hidden = [a for a in all_actors if a.get_path_name() not in keep]
        component.set_editor_property(
            "primitive_render_mode",
            unreal.SceneCapturePrimitiveRenderMode.PRM_LEGACY_SCENE_CAPTURE,
        )
        component.set_editor_property("hidden_actors", hidden)
        return True
    except Exception:
        return False


def bake_top_down_base_color(
    world: Any,
    restrict_to_actors: List[Any],
    center_x: float, center_y: float, half_size: float, z_top: float,
    output_png_path: str,
    resolution: int = 4096,
) -> bool:
    if unreal is None:
        return False

    capture_actor = None
    try:
        render_format = getattr(unreal.TextureRenderTargetFormat, "RTF_RGBA8_SRGB", None)
        if render_format is None:
            render_format = unreal.TextureRenderTargetFormat.RTF_RGBA8

        render_target = unreal.RenderingLibrary.create_render_target2d(
            world, resolution, resolution, render_format
        )
        if render_target is None:
            return False

        location = unreal.Vector(center_x, center_y, z_top)
        rotation = unreal.Rotator(roll=0.0, pitch=-90.0, yaw=0.0)

        try:
            capture_actor = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_class(
                unreal.SceneCapture2D, location, rotation
            )
        except Exception:
            capture_actor = None
        if capture_actor is None:
            capture_actor = unreal.EditorLevelLibrary.spawn_actor_from_class(
                unreal.SceneCapture2D, location, rotation
            )
        if capture_actor is None:
            return False

        component = get_capture_component(capture_actor)
        component.set_editor_property("texture_target", render_target)
        component.set_editor_property("capture_source", unreal.SceneCaptureSource.SCS_BASE_COLOR)
        component.set_editor_property("projection_type", unreal.CameraProjectionMode.ORTHOGRAPHIC)
        component.set_editor_property("ortho_width", half_size * 2.0)

        if not restrict_capture_to(component, restrict_to_actors):
            return False

        component.set_editor_property("capture_every_frame", False)
        component.set_editor_property("capture_on_movement", False)
        try:
            component.set_editor_property("always_persist_rendering_state", True)
        except Exception:
            pass

        component.capture_scene()

        directory = os.path.dirname(output_png_path)
        filename = os.path.basename(output_png_path)
        os.makedirs(directory, exist_ok=True)
        unreal.RenderingLibrary.export_render_target(world, render_target, directory, filename)

        if not os.path.isfile(output_png_path):
            return False
        return os.path.getsize(output_png_path) >= 1024

    except Exception:
        traceback.print_exc()
        return False
    finally:
        if capture_actor is not None:
            try:
                unreal.get_editor_subsystem(unreal.EditorActorSubsystem).destroy_actor(capture_actor)
            except Exception:
                try:
                    capture_actor.destroy_actor()
                except Exception:
                    pass


def export_texture_to_png(
    world: Any,
    texture: Any,
    output_path: str,
    resolution: int = 1024,
    temp_package: str = "/Game/UE2Godot/_TextureBakeTemp",
) -> bool:
    """Draws a Texture2D through a reusable unlit material into a render target and exports it as PNG.

    Reuses the temporary material asset to prevent GC ForceDelete package corruption and modal dialog locks.
    """
    if unreal is None or texture is None:
        return False

    MEL = unreal.MaterialEditingLibrary
    material = None
    try:
        name = "M_TextureBakeTemp"
        asset_path = temp_package + "/" + name

        if unreal.EditorAssetLibrary.does_asset_exist(asset_path):
            try:
                material = unreal.load_asset(asset_path)
            except Exception:
                material = None

        if material is None:
            unreal.EditorAssetLibrary.make_directory(temp_package)
            material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
                name, temp_package, unreal.Material, unreal.MaterialFactoryNew()
            )

        if material is None:
            return False

        try:
            MEL.disconnect_material_expressions(material)
        except Exception:
            pass

        material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)

        sample = MEL.create_material_expression(material, unreal.MaterialExpressionTextureSample, -300, 0)
        sample.set_editor_property("texture", texture)
        MEL.connect_material_property(sample, "RGB", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
        MEL.recompile_material(material)

        render_format = getattr(unreal.TextureRenderTargetFormat, "RTF_RGBA8_SRGB", None)
        if render_format is None:
            render_format = unreal.TextureRenderTargetFormat.RTF_RGBA8

        render_target = unreal.RenderingLibrary.create_render_target2d(world, resolution, resolution, render_format)
        if render_target is None:
            return False

        unreal.RenderingLibrary.draw_material_to_render_target(world, render_target, material)

        directory = os.path.dirname(output_path)
        filename = os.path.basename(output_path)
        os.makedirs(directory, exist_ok=True)
        unreal.RenderingLibrary.export_render_target(world, render_target, directory, filename)

        return os.path.isfile(output_path) and os.path.getsize(output_path) > 512
    except Exception:
        traceback.print_exc()
        return False
    finally:
        if material is not None:
            try:
                MEL.disconnect_material_expressions(material)
            except Exception:
                pass


def _validate_png(path: Optional[str]) -> Tuple[bool, str]:
    if not path:
        return False, "aucun chemin rendu"
    if not os.path.isfile(path):
        return False, "fichier non créé"
    size = os.path.getsize(path)
    if size < 512:
        return False, f"fichier suspect ({size} octets)"
    try:
        with open(path, "rb") as handle:
            magic = handle.read(8)
    except OSError as exc:
        return False, f"relecture impossible : {exc}"
    if magic[:4] not in (b"\x89PNG", b"\x89P N"):
        if not magic.startswith(b"\x89PNG"):
            return False, f"signature PNG absente ({magic[:4]!r})"
    return True, ""


def _texture_via_render_target(world, texture, output_path, resolution=1024):
    ok = export_texture_to_png(world, texture, output_path, resolution)
    return output_path if ok else None


def _export_task_available() -> bool:
    return (unreal is not None
            and getattr(unreal, "AssetExportTask", None) is not None
            and getattr(unreal, "Exporter", None) is not None)


def _texture_via_export_task(world, texture, output_path, resolution=1024):
    if unreal is None:
        return None
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    task = unreal.AssetExportTask()
    task.set_editor_property("object", texture)
    task.set_editor_property("filename", output_path)
    task.set_editor_property("automated", True)
    task.set_editor_property("prompt", False)
    task.set_editor_property("replace_identical", True)
    task.set_editor_property("write_empty_files", False)

    if not unreal.Exporter.run_asset_export_task(task):
        return None
    return output_path


def _source_file_available() -> bool:
    return unreal is not None


def _texture_via_source_file(world, texture, output_path, resolution=1024):
    if unreal is None or texture is None:
        return None

    import_data = None
    for prop in ("asset_import_data", "assetImportData"):
        try:
            import_data = texture.get_editor_property(prop)
        except Exception:
            import_data = None
        if import_data is not None:
            break
    if import_data is None:
        return None

    source_path = None
    for getter in ("get_first_filename", "extract_filenames"):
        method = getattr(import_data, getter, None)
        if method is None:
            continue
        try:
            value = method()
        except Exception:
            continue
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        if value:
            source_path = str(value)
            break

    if not source_path or not os.path.isfile(source_path):
        return None

    extension = os.path.splitext(source_path)[1].lower()
    if extension not in (".png", ".tga", ".jpg", ".jpeg", ".bmp", ".exr", ".hdr"):
        return None

    if extension != ".png":
        return None

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    shutil.copy2(source_path, output_path)
    return output_path


def build_texture_export_chain(enable_task: bool = True,
                               enable_source: bool = True) -> StrategyChain:
    strategies = [
        Strategy(
            name="render_target_bake",
            run=_texture_via_render_target,
            quality="full",
        ),
    ]
    if enable_task:
        strategies.append(Strategy(
            name="asset_export_task",
            run=_texture_via_export_task,
            available=_export_task_available,
            quality="full",
            caveat=("pixels de l'asset, sans passe de rendu"),
        ))
    if enable_source:
        strategies.append(Strategy(
            name="original_source_file",
            run=_texture_via_source_file,
            available=_source_file_available,
            quality="full",
            caveat="fichier source d'import copié tel quel",
        ))
    return StrategyChain("texture_export", strategies, _validate_png)


def export_texture_with_fallbacks(world, texture, output_path,
                                  resolution: int = 1024, chain=None):
    chain = chain or build_texture_export_chain()
    return chain.run(world, texture, output_path, resolution)
