# addons/ue2godot/core/geometry_builder.gd
class_name GeometryBuilder
extends RefCounted

static func build_geometry_placement(
	placement: Variant,
	asset_map: Variant,
	root_node: Node3D,
	stats: Dictionary
) -> void:
	if not (placement is Dictionary) or not (asset_map is Dictionary) or root_node == null:
		stats["invalid_placement_data"] = stats.get("invalid_placement_data", 0) + 1
		return

	var p_dict: Dictionary = placement as Dictionary
	var a_map: Dictionary = asset_map as Dictionary

	var mesh_data_raw = p_dict.get("mesh", null)
	var mesh_data: Dictionary = mesh_data_raw if mesh_data_raw is Dictionary else {}
	var ue_path: String = str(mesh_data.get("path", ""))
	if ue_path == "":
		stats["skipped_slot_empty"] = stats.get("skipped_slot_empty", 0) + 1
		return

	var assets_raw = a_map.get("assets", null)
	var assets: Dictionary = assets_raw if assets_raw is Dictionary else {}
	if not assets.has(ue_path):
		stats["missing_asset_mapping"] = stats.get("missing_asset_mapping", 0) + 1
		return

	var asset_info_raw = assets.get(ue_path, null)
	var asset_info: Dictionary = asset_info_raw if asset_info_raw is Dictionary else {}
	var godot_path: String = str(asset_info.get("godot_path", ""))

	if godot_path == "":
		stats["missing_asset_mapping"] = stats.get("missing_asset_mapping", 0) + 1
		return

	var glb_resource = load(godot_path)
	if glb_resource == null:
		stats["missing_glb"] = stats.get("missing_glb", 0) + 1
		return

	var actor_info_raw = p_dict.get("actor", null)
	var actor_info: Dictionary = actor_info_raw if actor_info_raw is Dictionary else {}
	var actor_name: String = str(actor_info.get("name", "Mesh"))

	# Handle instanced static mesh (ISM / HISM) with multiple instance transforms
	var inst_transforms_raw = p_dict.get("instance_final_world_transforms", null)
	if inst_transforms_raw == null or not (inst_transforms_raw is Array) or (inst_transforms_raw as Array).is_empty():
		inst_transforms_raw = p_dict.get("instance_transforms", null)

	if inst_transforms_raw is Array and not (inst_transforms_raw as Array).is_empty():
		var inst_list: Array = inst_transforms_raw as Array
		var base_tf_data_raw = p_dict.get("reconstruction_transform", p_dict.get("final_world_transform", {}))
		var base_tf := TransformConverter.transform_from_v10(base_tf_data_raw)

		var container := Node3D.new()
		container.name = actor_name + "_ISM"
		root_node.add_child(container)
		container.owner = root_node

		var built_inst_count := 0
		for i in range(inst_list.size()):
			var tf_item = inst_list[i]
			if tf_item == null or not (tf_item is Dictionary):
				continue
			var inst_tf := TransformConverter.transform_from_v10(tf_item)

			# If instance_transforms was used instead of instance_final_world_transforms,
			# compose with base transform
			if not p_dict.has("instance_final_world_transforms"):
				inst_tf = base_tf * inst_tf

			var instance = glb_resource.instantiate()
			if instance == null:
				stats["instantiate_failure"] = stats.get("instantiate_failure", 0) + 1
				continue

			if instance is Node3D:
				(instance as Node3D).transform = inst_tf

			instance.name = str(actor_name) + "_inst_" + str(i)
			container.add_child(instance)
			instance.owner = root_node
			built_inst_count += 1

		if built_inst_count > 0:
			stats["built_placements"] = stats.get("built_placements", 0) + 1
			stats["built_ism_instances"] = stats.get("built_ism_instances", 0) + built_inst_count
		else:
			container.free()
			stats["instantiate_failure"] = stats.get("instantiate_failure", 0) + 1
	else:
		var tf_data_raw = p_dict.get("reconstruction_transform", p_dict.get("final_world_transform", {}))
		var tf := TransformConverter.transform_from_v10(tf_data_raw)

		var instance = glb_resource.instantiate()
		if instance == null:
			stats["instantiate_failure"] = stats.get("instantiate_failure", 0) + 1
			return

		if instance is Node3D:
			(instance as Node3D).transform = tf

		instance.name = actor_name
		root_node.add_child(instance)
		instance.owner = root_node
		stats["built_placements"] = stats.get("built_placements", 0) + 1
