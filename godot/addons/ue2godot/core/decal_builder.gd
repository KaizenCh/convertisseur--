# addons/ue2godot/core/decal_builder.gd
class_name DecalBuilder
extends RefCounted

const KEEP_PLACEMENT_METADATA := true

static func build_decals(
	decal_map: Variant,
	manifest: Variant,
	root_node: Node3D,
	stats: Dictionary
) -> void:
	if not (manifest is Dictionary) or root_node == null:
		return

	var m_dict: Dictionary = manifest as Dictionary
	var effects_raw = m_dict.get("effects", {})
	var effects: Dictionary = effects_raw if effects_raw is Dictionary else {}
	var decals_raw = effects.get("decals", [])
	var decals_list: Array = decals_raw if decals_raw is Array else []

	if decals_list.size() == 0:
		return

	var d_map: Dictionary = decal_map if decal_map is Dictionary else {}
	var decal_materials_raw = d_map.get("decal_materials", {})
	var decal_materials: Dictionary = decal_materials_raw if decal_materials_raw is Dictionary else {}

	var texture_cache: Dictionary = {}

	for placement in decals_list:
		if not (placement is Dictionary):
			continue
		var p_dict: Dictionary = placement as Dictionary

		var mat_path: String = str(p_dict.get("material_path", ""))
		if mat_path == "":
			var mat_block = p_dict.get("material", {})
			if mat_block is Dictionary:
				mat_path = str((mat_block as Dictionary).get("path", ""))

		var tf_data = p_dict.get("transform", {})
		var tf := TransformConverter.transform_from_v10(tf_data)

		var decal_node := Decal.new()
		decal_node.transform = tf.orthonormalized()

		if p_dict.has("sort_order"):
			decal_node.sorting_offset = float(p_dict["sort_order"])
		if p_dict.has("fade_screen_size"):
			decal_node.distance_fade_enabled = true

		var size_data_raw = p_dict.get("size", [256.0, 256.0, 256.0])
		if size_data_raw is Array and (size_data_raw as Array).size() >= 3:
			var s_arr: Array = size_data_raw as Array
			var w: float = float(s_arr[1]) * TransformConverter.UE_CM_TO_GODOT_M
			var h: float = float(s_arr[2]) * TransformConverter.UE_CM_TO_GODOT_M
			var th: float = float(s_arr[0]) * TransformConverter.UE_CM_TO_GODOT_M
			decal_node.size = Vector3(w, th, h)

		if decal_materials.has(mat_path):
			var mat_info_raw = decal_materials[mat_path]
			var mat_info: Dictionary = mat_info_raw if mat_info_raw is Dictionary else {}

			if not texture_cache.has(mat_path):
				var tex_path: String = str(mat_info.get("godot_path", ""))
				var loaded = null
				if tex_path != "":
					loaded = load(tex_path)
				texture_cache[mat_path] = loaded

			var tex = texture_cache[mat_path]
			if tex != null and tex is Texture2D:
				decal_node.texture_albedo = tex
			else:
				stats["decal_texture_missing"] = stats.get("decal_texture_missing", 0) + 1

			var tint_raw = mat_info.get("tint", [1.0, 1.0, 1.0])
			if tint_raw is Array and (tint_raw as Array).size() >= 3:
				var t_arr: Array = tint_raw as Array
				decal_node.modulate = Color(float(t_arr[0]), float(t_arr[1]), float(t_arr[2]), 1.0)

			var normal_path: String = str(mat_info.get("normal_godot_path", ""))
			if normal_path != "":
				var normal_tex = load(normal_path)
				if normal_tex != null and normal_tex is Texture2D:
					decal_node.texture_normal = normal_tex

			if KEEP_PLACEMENT_METADATA:
				var actor_info_raw = p_dict.get("actor", {})
				var actor_info: Dictionary = actor_info_raw if actor_info_raw is Dictionary else {}
				decal_node.set_meta("ue_actor_path", str(actor_info.get("path", "")))
				decal_node.set_meta("ue_material_path", mat_path)
		else:
			stats["decal_material_unresolved"] = stats.get("decal_material_unresolved", 0) + 1

		root_node.add_child(decal_node)
		decal_node.owner = root_node
		stats["built_decals"] = stats.get("built_decals", 0) + 1
