# addons/ue2godot/core/vfx_builder.gd
class_name VFXBuilder
extends RefCounted

const VFX_MAX_LIGHTS := 24
const VFX_LIGHT_MIN_SPACING := 6.0

const DEFAULT_CATEGORY_KEYWORDS := [
	["candle", "candle"],
	["torch", "torch"],
	["campfire", "fire"], ["bonfire", "fire"], ["fire", "fire"],
	["smoke", "smoke"],
	["steam", "steam"],
	["spark", "spark"],
]

const CATEGORY_PRESETS := {
	"candle":  {"color": Color(1.0, 0.55, 0.15, 1.0), "scale": 0.10, "lifetime": 0.6, "speed": 0.15, "light": true,  "light_energy": 1.2, "light_range": 1.5, "height_offset": 0.05},
	"torch":   {"color": Color(1.0, 0.45, 0.10, 1.0), "scale": 0.22, "lifetime": 0.8, "speed": 0.35, "light": true,  "light_energy": 2.5, "light_range": 3.5, "height_offset": 1.10},
	"fire":    {"color": Color(1.0, 0.35, 0.05, 1.0), "scale": 0.45, "lifetime": 1.0, "speed": 0.55, "light": true,  "light_energy": 4.0, "light_range": 6.0, "height_offset": 0.20},
	"smoke":   {"color": Color(0.5, 0.5, 0.5, 0.45),  "scale": 0.60, "lifetime": 2.2, "speed": 0.60, "light": false, "light_energy": 0.0, "light_range": 0.0, "height_offset": 0.80},
	"steam":   {"color": Color(0.85, 0.85, 0.9, 0.35),"scale": 0.50, "lifetime": 1.8, "speed": 0.45, "light": false, "light_energy": 0.0, "light_range": 0.0, "height_offset": 0.10},
	"spark":   {"color": Color(1.0, 0.85, 0.4, 1.0),  "scale": 0.05, "lifetime": 0.4, "speed": 1.5,  "light": false, "light_energy": 0.0, "light_range": 0.0, "height_offset": 0.10},
	"generic": {"color": Color(0.8, 0.8, 0.8, 0.6),   "scale": 0.25, "lifetime": 1.0, "speed": 0.3,  "light": false, "light_energy": 0.0, "light_range": 0.0, "height_offset": 0.10},
}


static func _resolve_vfx_mode(manifest: Dictionary) -> String:
	var pipeline_raw = manifest.get("pipeline", {})
	var pipeline_cfg: Dictionary = pipeline_raw if pipeline_raw is Dictionary else {}
	var resolved_raw = pipeline_cfg.get("resolved_config", {})
	var resolved: Dictionary = resolved_raw if resolved_raw is Dictionary else {}
	var vfx_raw = resolved.get("vfx", {})
	var vfx_cfg: Dictionary = vfx_raw if vfx_raw is Dictionary else {}
	return str(vfx_cfg.get("mode", "markers"))


static func build_vfx(
	decal_map: Variant,
	manifest: Variant,
	root_node: Node3D,
	stats: Dictionary
) -> void:
	if not (manifest is Dictionary) or root_node == null:
		return

	var m_dict: Dictionary = manifest as Dictionary
	var mode := _resolve_vfx_mode(m_dict)
	stats["vfx_mode"] = mode

	match mode:
		"none":
			return
		"substitutes":
			build_vfx_substitutes(m_dict, root_node, stats)
		_:
			build_vfx_markers(m_dict, root_node, stats)


static func build_vfx_markers(
	manifest: Dictionary,
	root_node: Node3D,
	stats: Dictionary
) -> void:
	var effects_raw = manifest.get("effects", {})
	var effects: Dictionary = effects_raw if effects_raw is Dictionary else {}
	var niagara_raw = effects.get("niagara", [])
	var niagara_list: Array = niagara_raw if niagara_raw is Array else []
	if niagara_list.size() == 0:
		return

	var vfx_root := Node3D.new()
	vfx_root.name = "VFX_MARKERS_NOT_CONVERTED"
	root_node.add_child(vfx_root)
	vfx_root.owner = root_node

	for item in niagara_list:
		if not (item is Dictionary):
			continue
		var i_dict: Dictionary = item as Dictionary
		var sys_name: String = str(i_dict.get("system_name", i_dict.get("name", "VFX")))
		var tf_data = i_dict.get("transform", {})
		var tf := TransformConverter.transform_from_v10(tf_data)

		var marker := Marker3D.new()
		marker.name = sys_name
		marker.transform = tf

		vfx_root.add_child(marker)
		marker.owner = root_node
		stats["built_vfx_markers"] = stats.get("built_vfx_markers", 0) + 1


static func _category_for_name(name: String, keywords: Array) -> String:
	var lowered := name.to_lower()
	for pair in keywords:
		if pair is Array and (pair as Array).size() >= 2:
			if lowered.contains(str(pair[0])):
				return str(pair[1])
	return "generic"


static func _shared_resources_for_category(category: String, cache: Dictionary) -> Dictionary:
	if cache.has(category):
		return cache[category]

	var preset: Dictionary = CATEGORY_PRESETS.get(category, CATEGORY_PRESETS["generic"])

	var gradient := Gradient.new()
	gradient.add_point(0.0, Color(1, 1, 1, 1))
	gradient.add_point(1.0, Color(1, 1, 1, 0))
	var falloff := GradientTexture2D.new()
	falloff.gradient = gradient
	falloff.fill = GradientTexture2D.FILL_RADIAL
	falloff.fill_from = Vector2(0.5, 0.5)
	falloff.fill_to = Vector2(1.0, 0.5)
	falloff.width = 32
	falloff.height = 32

	var material := StandardMaterial3D.new()
	material.vertex_color_use_as_albedo = true
	material.albedo_color = preset["color"]
	material.albedo_texture = falloff
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	material.billboard_mode = BaseMaterial3D.BILLBOARD_PARTICLES
	material.cull_mode = BaseMaterial3D.CULL_DISABLED

	var mesh := QuadMesh.new()
	mesh.size = Vector2(preset["scale"], preset["scale"])
	mesh.material = material

	var process_material := ParticleProcessMaterial.new()
	process_material.direction = Vector3(0, 1, 0)
	process_material.spread = 20.0
	process_material.gravity = Vector3(0, 0.15, 0)
	process_material.initial_velocity_min = preset["speed"] * 0.6
	process_material.initial_velocity_max = preset["speed"] * 1.2
	process_material.scale_min = 0.7
	process_material.scale_max = 1.3
	process_material.color = preset["color"]
	_try_set(material, "depth_draw_mode", BaseMaterial3D.DEPTH_DRAW_DISABLED)

	var resources := {"mesh": mesh, "process_material": process_material, "preset": preset}
	cache[category] = resources
	return resources


static func _try_set(obj: Object, property: String, value) -> void:
	if property in obj:
		obj.set(property, value)


static func build_vfx_substitutes(
	manifest: Dictionary,
	root_node: Node3D,
	stats: Dictionary
) -> void:
	var effects_raw = manifest.get("effects", {})
	var effects: Dictionary = effects_raw if effects_raw is Dictionary else {}
	var niagara_raw = effects.get("niagara", [])
	var niagara_list: Array = niagara_raw if niagara_raw is Array else []
	if niagara_list.size() == 0:
		return

	var pipeline_raw = manifest.get("pipeline", {})
	var pipeline_cfg: Dictionary = pipeline_raw if pipeline_raw is Dictionary else {}
	var resolved_raw = pipeline_cfg.get("resolved_config", {})
	var resolved: Dictionary = resolved_raw if resolved_raw is Dictionary else {}
	var vfx_raw = resolved.get("vfx", {})
	var vfx_cfg: Dictionary = vfx_raw if vfx_raw is Dictionary else {}

	var keywords_raw = vfx_cfg.get("category_rules", [])
	var keywords: Array = keywords_raw if keywords_raw is Array else []
	var keyword_pairs: Array = []
	for rule in keywords:
		if rule is Dictionary and rule.has("match") and rule.has("category"):
			keyword_pairs.append([rule["match"], rule["category"]])
	if keyword_pairs.is_empty():
		keyword_pairs = DEFAULT_CATEGORY_KEYWORDS

	var vfx_root := Node3D.new()
	vfx_root.name = "VFX_Substitutes"
	root_node.add_child(vfx_root)
	vfx_root.owner = root_node

	var resource_cache: Dictionary = {}
	var lights_placed: Array[Vector3] = []
	var lights_skipped := 0

	for item in niagara_list:
		if not (item is Dictionary):
			continue
		var i_dict: Dictionary = item as Dictionary
		var sys_name: String = str(i_dict.get("system_name", i_dict.get("name", "VFX")))
		var tf_data = i_dict.get("transform", {})
		var tf := TransformConverter.transform_from_v10(tf_data)

		var category := _category_for_name(sys_name, keyword_pairs)
		var resources := _shared_resources_for_category(category, resource_cache)
		var preset: Dictionary = resources["preset"]

		var offset: Vector3 = tf.basis.y.normalized() * float(preset["height_offset"])

		var emitter := GPUParticles3D.new()
		emitter.name = sys_name
		emitter.transform = Transform3D(tf.basis, tf.origin + offset)
		emitter.amount = 12
		emitter.lifetime = preset["lifetime"]
		emitter.process_material = resources["process_material"]
		emitter.draw_pass_1 = resources["mesh"]
		emitter.set_meta("ue_vfx_category", category)
		emitter.set_meta("ue_system_name", sys_name)
		_try_set(emitter, "cast_shadow", GeometryInstance3D.SHADOW_CASTING_SETTING_OFF)

		vfx_root.add_child(emitter)
		emitter.owner = root_node
		stats["built_vfx_substitutes"] = stats.get("built_vfx_substitutes", 0) + 1

		if preset.get("light", false):
			var too_close := false
			for placed in lights_placed:
				if placed.distance_to(emitter.global_transform.origin) < VFX_LIGHT_MIN_SPACING:
					too_close = true
					break

			if too_close or lights_placed.size() >= VFX_MAX_LIGHTS:
				lights_skipped += 1
			else:
				var light := OmniLight3D.new()
				light.transform = Transform3D(Basis(), Vector3.ZERO)
				light.light_color = preset["color"]
				light.light_energy = preset["light_energy"]
				light.omni_range = preset["light_range"]
				light.shadow_enabled = false
				emitter.add_child(light)
				light.owner = root_node
				lights_placed.append(emitter.global_transform.origin)
				stats["built_vfx_lights"] = stats.get("built_vfx_lights", 0) + 1

	if lights_skipped > 0:
		stats["vfx_lights_skipped_over_budget"] = lights_skipped


static func build_audio_markers(
	decal_map: Variant,
	root_node: Node3D,
	stats: Dictionary
) -> void:
	if not (decal_map is Dictionary) or root_node == null:
		return

	var d_map: Dictionary = decal_map as Dictionary
	var audio_raw = d_map.get("audio", {})
	var audio_section: Dictionary = audio_raw if audio_raw is Dictionary else {}
	var markers_raw = audio_section.get("markers", [])
	var markers_list: Array = markers_raw if markers_raw is Array else []
	if markers_list.size() == 0:
		return

	var audio_root := Node3D.new()
	audio_root.name = "AUDIO_MARKERS_NOT_CONVERTED"
	root_node.add_child(audio_root)
	audio_root.owner = root_node

	for item in markers_list:
		if not (item is Dictionary):
			continue
		var i_dict: Dictionary = item as Dictionary
		var tf_data = i_dict.get("transform", {})
		var tf := TransformConverter.transform_from_v10(tf_data)

		var marker := Marker3D.new()
		var sound_path: String = str(i_dict.get("sound_path", ""))
		marker.name = sound_path.get_file() if sound_path != "" else "Audio"
		marker.transform = tf
		marker.set_meta("ue_component_path", str(i_dict.get("component_path", "")))
		marker.set_meta("ue_sound_path", sound_path)

		audio_root.add_child(marker)
		marker.owner = root_node
		stats["built_audio_markers"] = stats.get("built_audio_markers", 0) + 1
