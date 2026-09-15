# addons/ue2godot/core/light_builder.gd
class_name LightBuilder
extends RefCounted

const MAX_REALTIME_LIGHTS := 48
const SHADOW_CASTING_LIMIT := 8

const CANDELA_TO_ENERGY := 0.05
const LUMEN_TO_ENERGY := 0.004
const LUX_TO_ENERGY := 0.01
const UNITLESS_TO_ENERGY := 0.05


static func _energy_from(record: Dictionary) -> float:
	var intensity := float(record.get("intensity", 0.0))
	var units := str(record.get("intensity_units", "")).to_lower()
	var light_type := str(record.get("light_type", "point"))

	if light_type == "directional":
		return clampf(intensity * LUX_TO_ENERGY, 0.0, 16.0)
	if units.contains("candela"):
		return clampf(intensity * CANDELA_TO_ENERGY, 0.0, 32.0)
	if units.contains("lumen"):
		return clampf(intensity * LUMEN_TO_ENERGY, 0.0, 32.0)
	return clampf(intensity * UNITLESS_TO_ENERGY, 0.0, 32.0)


static func _color_from(record: Dictionary) -> Color:
	var raw = record.get("color", null)
	if raw is Dictionary:
		var d: Dictionary = raw as Dictionary
		return Color(float(d.get("r", 1.0)), float(d.get("g", 1.0)),
					 float(d.get("b", 1.0)), 1.0)
	return Color(1, 1, 1, 1)


static func build_lights(manifest: Variant, root_node: Node3D, stats: Dictionary) -> void:
	if not (manifest is Dictionary) or root_node == null:
		return

	var m_dict: Dictionary = manifest as Dictionary
	var features_raw = m_dict.get("world_features", {})
	var features: Dictionary = features_raw if features_raw is Dictionary else {}
	var lights_raw = features.get("lights", [])
	var lights: Array = lights_raw if lights_raw is Array else []

	if lights.size() == 0:
		return

	var container := Node3D.new()
	container.name = "Lights"
	root_node.add_child(container)
	container.owner = root_node

	var valid_lights: Array = []
	for l in lights:
		if l is Dictionary:
			valid_lights.append(l)

	valid_lights.sort_custom(func(a, b):
		return float(a.get("intensity", 0.0)) > float(b.get("intensity", 0.0)))

	var built := 0
	var shadow_count := 0

	for record in valid_lights:
		if built >= MAX_REALTIME_LIGHTS:
			stats["lights_skipped_over_budget"] = \
				stats.get("lights_skipped_over_budget", 0) + 1
			continue

		var light_type := str(record.get("light_type", "point"))
		var node: Light3D = null

		match light_type:
			"directional":
				node = DirectionalLight3D.new()
			"spot":
				var spot := SpotLight3D.new()
				if record.has("outer_cone_angle"):
					spot.spot_angle = clampf(float(record["outer_cone_angle"]), 1.0, 89.0)
				if record.has("attenuation_radius"):
					spot.spot_range = maxf(
						float(record["attenuation_radius"]) * TransformConverter.UE_CM_TO_GODOT_M,
						0.1)
				node = spot
			"sky":
				stats["sky_lights_not_converted"] = \
					stats.get("sky_lights_not_converted", 0) + 1
				continue
			_:
				var omni := OmniLight3D.new()
				if record.has("attenuation_radius"):
					omni.omni_range = maxf(
						float(record["attenuation_radius"]) * TransformConverter.UE_CM_TO_GODOT_M,
						0.1)
				node = omni

		if node == null:
			continue

		var tf_raw = record.get("transform", {})
		node.transform = TransformConverter.transform_from_v10(tf_raw)
		node.light_color = _color_from(record)
		node.light_energy = _energy_from(record)

		if bool(record.get("use_temperature", false)) and record.has("temperature"):
			var kelvin := float(record["temperature"])
			node.light_color = node.light_color * _kelvin_to_color(kelvin)

		var wants_shadow := bool(record.get("cast_shadows", true))
		if wants_shadow and shadow_count < SHADOW_CASTING_LIMIT:
			node.shadow_enabled = true
			shadow_count += 1
		else:
			node.shadow_enabled = false

		var actor_raw = record.get("actor", {})
		var actor_info: Dictionary = actor_raw if actor_raw is Dictionary else {}
		node.name = str(actor_info.get("name", "Light"))
		node.set_meta("ue_actor_path", str(actor_info.get("path", "")))
		node.set_meta("ue_light_type", light_type)

		container.add_child(node)
		node.owner = root_node
		built += 1

	stats["built_lights"] = built
	stats["lights_with_shadows"] = shadow_count


static func _kelvin_to_color(kelvin: float) -> Color:
	var temp := clampf(kelvin, 1000.0, 40000.0) / 100.0
	var r := 255.0
	var g := 255.0
	var b := 255.0

	if temp <= 66.0:
		g = clampf(99.4708025861 * log(temp) - 161.1195681661, 0.0, 255.0)
		if temp <= 19.0:
			b = 0.0
		else:
			b = clampf(138.5177312231 * log(temp - 10.0) - 305.0447927307, 0.0, 255.0)
	else:
		r = clampf(329.698727446 * pow(temp - 60.0, -0.1332047592), 0.0, 255.0)
		g = clampf(288.1221695283 * pow(temp - 60.0, -0.0755148492), 0.0, 255.0)

	return Color(r / 255.0, g / 255.0, b / 255.0, 1.0)
