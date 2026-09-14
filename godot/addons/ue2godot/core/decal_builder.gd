# addons/ue2godot/core/decal_builder.gd
#
# Ported from godot_decals_vfx_addition.gd (repo root, the validated
# reference, §3.8): preload each material's texture ONCE and share it
# across every placement that uses it — a single texture serving 781
# placements should be loaded once, not 781 times — and apply the tint
# baked into the material (previously loaded and never applied: every
# decal rendered at the texture's raw colour, ignoring decal_map.tint).
class_name DecalBuilder
extends RefCounted

const KEEP_PLACEMENT_METADATA := true

static func build_decals(
	decal_map: Dictionary,
	manifest: Dictionary,
	root_node: Node3D,
	stats: Dictionary
) -> void:
	var decals_list: Array = manifest.get("effects", {}).get("decals", [])
	if decals_list.size() == 0:
		return

	var decal_materials: Dictionary = decal_map.get("decal_materials", {})

	# Preload once per material path, shared by every placement referencing it.
	var texture_cache: Dictionary = {}

	for placement in decals_list:
		var mat_path: String = placement.get("material_path", "")
		var tf_data: Dictionary = placement.get("transform", {})
		var tf := TransformConverter.transform_from_v10(tf_data)

		var decal_node := Decal.new()
		decal_node.transform = tf.orthonormalized()

		var size_data: Array = placement.get("size", [256.0, 256.0, 256.0])
		if size_data.size() >= 3:
			var w: float = float(size_data[1]) * TransformConverter.UE_CM_TO_GODOT_M
			var h: float = float(size_data[2]) * TransformConverter.UE_CM_TO_GODOT_M
			var th: float = float(size_data[0]) * TransformConverter.UE_CM_TO_GODOT_M
			decal_node.size = Vector3(w, th, h)

		if decal_materials.has(mat_path):
			var mat_info: Dictionary = decal_materials[mat_path]

			if not texture_cache.has(mat_path):
				var tex_path: String = mat_info.get("godot_path", "")
				var loaded = null
				if tex_path != "":
					loaded = load(tex_path)
				texture_cache[mat_path] = loaded

			var tex = texture_cache[mat_path]
			if tex != null and tex is Texture2D:
				decal_node.texture_albedo = tex
			else:
				stats["decal_texture_missing"] = stats.get("decal_texture_missing", 0) + 1

			# The RGBA baked by step4 already carries the tint in its colour
			# channel (png_codec.compose_tinted_rgba), so modulate is not
			# needed to reproduce it there — but a decal referencing a
			# material whose bake failed still lands here with a fallback
			# white texture (or none), so also apply the tint via modulate
			# as a second line of defense: even an untextured/fallback
			# decal shows the right colour instead of flat white.
			var tint: Array = mat_info.get("tint", [1.0, 1.0, 1.0])
			if tint.size() >= 3:
				decal_node.modulate = Color(float(tint[0]), float(tint[1]), float(tint[2]), 1.0)

			if KEEP_PLACEMENT_METADATA:
				var actor_info: Dictionary = placement.get("actor", {})
				decal_node.set_meta("ue_actor_path", actor_info.get("path", ""))
				decal_node.set_meta("ue_material_path", mat_path)
		else:
			stats["decal_material_unresolved"] = stats.get("decal_material_unresolved", 0) + 1

		root_node.add_child(decal_node)
		decal_node.owner = root_node
		stats["built_decals"] = stats.get("built_decals", 0) + 1
