# -*- coding: utf-8 -*-
"""
Standalone pure-Python binary glTF (GLB) writer + heightfield-to-mesh builder.

build_grid_mesh() and write_glb() are a faithful port of the two functions
of the same name in unreal_export_landscape.py (repo root, the validated
reference) — same vertex compaction, same analytic central-difference
normals, same UV convention tied to the orthographic capture camera, same
triangle winding compensation for the determinant -1 axis convention.
Ported here so the framework core stays engine-agnostic (no `import unreal`)
and testable in isolation, per the source of truth's own recommendation
(§6.6): "a reusable pattern for any future procedural geometry export".
"""

import json
import math
import struct
from typing import List, Tuple, Optional, Dict, Any, Callable


def build_grid_mesh(
    heights: List[Optional[float]],
    side: int,
    x_min: float, x_max: float,
    y_min: float, y_max: float,
    axis_map: Callable[[float, float, float], Tuple[float, float, float]],
) -> Tuple[List[float], List[float], List[float], List[int]]:
    """Turns a sampled heightfield into indexed triangles.

    `heights` is a flat, row-major (X outer, per raycast_grid_heights())
    list of length side*side, with None where a trace found nothing — a
    hole. Only grid points that were actually hit become vertices, and a
    quad is emitted only when its 4 corners were all hit, so holes stay
    holes instead of being filled with an invented height (matches the
    reference's documented behaviour exactly).

    `axis_map(x, y, z) -> (gx, gy, gz)` is the UE-cm -> Godot-m conversion
    to apply to every vertex and (after re-normalising) every normal — see
    core/axis.py for the blessed implementation. Passed in rather than
    hardcoded so this module has zero engine/project coupling.

    Returns (positions, normals, uvs, indices) as flat Python lists,
    ready for write_glb().
    """
    resolution = side - 1
    span_x = x_max - x_min
    span_y = y_max - y_min

    def height_at(ix: int, iy: int) -> Optional[float]:
        if ix < 0 or iy < 0 or ix >= side or iy >= side:
            return None
        return heights[ix * side + iy]

    index_of = [-1] * (side * side)
    positions: List[float] = []
    normals: List[float] = []
    uvs: List[float] = []

    step_x = span_x / float(resolution) if resolution else 1.0
    step_y = span_y / float(resolution) if resolution else 1.0

    for ix in range(side):
        for iy in range(side):
            z = height_at(ix, iy)
            if z is None:
                continue

            x = x_min + span_x * (ix / float(resolution)) if resolution else x_min
            y = y_min + span_y * (iy / float(resolution)) if resolution else y_min

            index_of[ix * side + iy] = len(positions) // 3
            positions.extend(axis_map(x, y, z))

            # Normal from central differences on the heightfield, falling
            # back to one-sided differences at the border and around holes
            # — never an invented flat-shaded face normal, which would not
            # respect holes the same way the vertex data does.
            zx_prev = height_at(ix - 1, iy)
            zx_next = height_at(ix + 1, iy)
            zy_prev = height_at(ix, iy - 1)
            zy_next = height_at(ix, iy + 1)

            if zx_prev is not None and zx_next is not None:
                dzdx = (zx_next - zx_prev) / (2.0 * step_x)
            elif zx_next is not None:
                dzdx = (zx_next - z) / step_x
            elif zx_prev is not None:
                dzdx = (z - zx_prev) / step_x
            else:
                dzdx = 0.0

            if zy_prev is not None and zy_next is not None:
                dzdy = (zy_next - zy_prev) / (2.0 * step_y)
            elif zy_next is not None:
                dzdy = (zy_next - z) / step_y
            elif zy_prev is not None:
                dzdy = (z - zy_prev) / step_y
            else:
                dzdy = 0.0

            # Surface normal in UE space, then mapped like a direction.
            nx, ny, nz = -dzdx, -dzdy, 1.0
            length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            gx, gy, gz = axis_map(nx / length, ny / length, nz / length)

            # axis_map also applies the cm->m scale, which is uniform, so
            # re-normalising restores a unit direction.
            glen = math.sqrt(gx * gx + gy * gy + gz * gz) or 1.0
            normals.extend((gx / glen, gy / glen, gz / glen))

            # Planar UVs matching the orthographic capture: with the
            # capture camera at pitch=-90/yaw=0, screen right = world +Y,
            # screen up = world +X, and glTF v grows downward from the top
            # of the image. This is unaffected by the axis mapping — the
            # camera framing did not change.
            u = (y - y_min) / span_y if span_y else 0.0
            v = (x_max - x) / span_x if span_x else 0.0
            uvs.extend((u, v))

    indices: List[int] = []
    skipped_quads = 0

    for ix in range(resolution):
        for iy in range(resolution):
            a = index_of[ix * side + iy]
            b = index_of[ix * side + (iy + 1)]
            c = index_of[(ix + 1) * side + iy]
            d = index_of[(ix + 1) * side + (iy + 1)]

            if a < 0 or b < 0 or c < 0 or d < 0:
                skipped_quads += 1
                continue

            # axis_map has determinant -1 (handedness flip), which reverses
            # triangle winding. The index order is flipped here to
            # compensate — otherwise every face would point downward and
            # the terrain would be invisible from above.
            indices.extend((a, b, c))
            indices.extend((b, d, c))

    return positions, normals, uvs, indices


def _pad(data: bytes, alignment: int = 4, filler: bytes = b"\x00") -> bytes:
    remainder = len(data) % alignment
    if remainder == 0:
        return data
    return data + filler * (alignment - remainder)


def write_glb(
    output_path: str,
    positions: List[float],
    normals: List[float],
    uvs: List[float],
    indices: List[int],
    png_bytes: Optional[bytes] = None,
    name: str = "Mesh",
) -> bool:
    """Writes a self-contained binary glTF 2.0 (GLB) file.

    Layout: one buffer, up to 5 bufferViews (positions/normals/uvs/indices/
    embedded PNG), 4 accessors, one PBR material. If png_bytes is given it
    becomes baseColorTexture (clamped, not repeated — the bake covers the
    mesh exactly once); otherwise a neutral flat baseColorFactor is used.
    """
    vertex_count = len(positions) // 3
    if vertex_count == 0 or not indices:
        raise ValueError("refusing to write an empty mesh")

    pos_bytes = struct.pack("<%df" % len(positions), *positions)
    nrm_bytes = struct.pack("<%df" % len(normals), *normals)
    uv_bytes = struct.pack("<%df" % len(uvs), *uvs)
    idx_bytes = struct.pack("<%dI" % len(indices), *indices)

    chunks: List[bytes] = []
    views: List[Dict[str, Any]] = []
    offset = 0

    def add_view(data: bytes, target: Optional[int] = None) -> int:
        nonlocal offset
        padded = _pad(data)
        view: Dict[str, Any] = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        views.append(view)
        chunks.append(padded)
        offset += len(padded)
        return len(views) - 1

    ARRAY_BUFFER = 34962
    ELEMENT_ARRAY_BUFFER = 34963

    pos_view = add_view(pos_bytes, ARRAY_BUFFER)
    nrm_view = add_view(nrm_bytes, ARRAY_BUFFER)
    uv_view = add_view(uv_bytes, ARRAY_BUFFER)
    idx_view = add_view(idx_bytes, ELEMENT_ARRAY_BUFFER)

    xs = positions[0::3]
    ys = positions[1::3]
    zs = positions[2::3]

    accessors = [
        {"bufferView": pos_view, "componentType": 5126, "count": vertex_count, "type": "VEC3",
         "min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]},
        {"bufferView": nrm_view, "componentType": 5126, "count": vertex_count, "type": "VEC3"},
        {"bufferView": uv_view, "componentType": 5126, "count": vertex_count, "type": "VEC2"},
        {"bufferView": idx_view, "componentType": 5125, "count": len(indices), "type": "SCALAR"},
    ]

    gltf: Dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "ue2godot_glb_writer"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{
            "name": name,
            "primitives": [{
                "attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
                "indices": 3, "material": 0, "mode": 4,
            }],
        }],
        "accessors": accessors,
        "bufferViews": views,
        "materials": [{
            "name": name + "_Material",
            "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 1.0},
            "doubleSided": False,
        }],
    }

    if png_bytes:
        image_view = add_view(png_bytes)
        gltf["images"] = [{"bufferView": image_view, "mimeType": "image/png", "name": name + "_BaseColor"}]
        # Clamp: the baked texture covers the mesh exactly once, so
        # repeating it at the edges would smear it.
        gltf["samplers"] = [{"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}]
        gltf["textures"] = [{"sampler": 0, "source": 0}]
        gltf["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}
    else:
        gltf["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] = [0.35, 0.32, 0.28, 1.0]

    binary = b"".join(chunks)
    gltf["buffers"] = [{"byteLength": len(binary)}]

    json_bytes = _pad(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), 4, b" ")
    binary = _pad(binary)

    total = 12 + 8 + len(json_bytes) + 8 + len(binary)

    with open(output_path, "wb") as handle:
        handle.write(b"glTF")
        handle.write(struct.pack("<I", 2))
        handle.write(struct.pack("<I", total))
        handle.write(struct.pack("<I", len(json_bytes)))
        handle.write(b"JSON")
        handle.write(json_bytes)
        handle.write(struct.pack("<I", len(binary)))
        handle.write(b"BIN\x00")
        handle.write(binary)

    return True
