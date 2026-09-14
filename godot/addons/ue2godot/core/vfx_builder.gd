# addons/ue2godot/core/vfx_builder.gd
#
# VFX mode is read from the manifest's embedded resolved config
# (manifest.pipeline.resolved_config.vfx.mode — see core/config.py,
# step1_manifest.py writes the whole resolved config into the manifest so
# both sides of the pipeline share one source of truth instead of two
# constants synchronised by hand):
#
#   "none"       -> nothing built at all, not even markers.
#   "markers"    -> Marker3D per Niagara component, honestly named
#                   VFX_MARKERS_NOT_CONVERTED (the previous, only, behaviour).
#   "substitutes"-> a real, if partial, particle reconstruction (see
#                   build_vfx_substitutes below) — NOT the full V10.12
#                   system from the reference (12 categories, 8 physics
#                   laws, a separate embers emitter): this ports the parts
#                   proven cheapest to get right and costliest to get
#                   wrong — shared per-category resources (§13.B.11),
#                   a shader-free default render path that cannot fail to
#                   compile (§13.B.12), a hard light budget (§13.B.15),
#                   and height calibration (§3.9.6) — without the 8 reduced-
#                   order plume-physics functions or the embers emitter.
#
# Defaults to "markers" if the config key is absent, which is the
# conservative, always-safe choice — never claims a conversion that did
# not happen (§13.C.24).
class_name VFXBuilder
extends RefCounted

const VFX_MAX_LIGHTS := 24
const VFX_LIGHT_MIN_SPACING := 6.0

# Order matters — most specific first (§13.B.16): "candle" must be tested
# before "flame", or "NS_candle_flame" would fall into a generic bucket.
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
	var resolved: Dictionary = manifest.get("pipeline", {}).get("resolved_config", {})
	var vfx_cfg: Dictionary = resolved.get("vfx", {})
	return str(vfx_cfg.get("mode", "markers"))


static func build_vfx(
	decal_map: Dictionary,
	manifest: Dictionary,
	root_node: Node3D,
	stats: Dictionary
) -> void:
	var mode := _resolve_vfx_mode(manifest)
	stats["vfx_mode"] = mode

	match mode:
		"none":
			return
		"substitutes":
			build_vfx_substitutes(manifest, root_node, stats)
		_:
			build_vfx_markers(manifest, root_node, stats)


static func build_vfx_markers(
	manifest: Dictionary,
	root_node: Node3D,
	stats: Dictionary
) -> void:
	var niagara_list: Array = manifest.get("effects", {}).get("niagara", [])
	if niagara_list.size() == 0:
		return

	var vfx_root := Node3D.new()
	vfx_root.name = "VFX_MARKERS_NOT_CONVERTED"
	root_node.add_child(vfx_root)
	vfx_root.owner = root_node

	for item in niagara_list:
		var sys_name: String = item.get("system_name", item.get("name", "VFX"))
		var tf_data: Dictionary = item.get("transform", {})
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
		if lowered.contains(str(pair[0])):
			return str(pair[1])
	return "generic"


# One ParticleProcessMaterial/mesh/material triple PER CATEGORY, shared by
# every instance of that category — the direct fix for the ~2500-resource
# problem measured in the reference (§13.B.11): 422 individual Niagara
# markers must never produce 422 individual shader/material resources.
static func _shared_resources_for_category(category: String, cache: Dictionary) -> Dictionary:
	if cache.has(category):
		return cache[category]

	var preset: Dictionary = CATEGORY_PRESETS.get(category, CATEGORY_PRESETS["generic"])

	# Radial falloff via GradientTexture2D + vertex-colour albedo +
	# billboard — deliberately NOT a custom shader. A broken/unsupported
	# shader falls back to Godot's opaque white default with no warning
	# (§13.B.12); this default path cannot fail to compile.
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
	process_material.gravity = Vector3(0, 0.15, 0)  # slight upward drift, not real gravity
	process_material.initial_velocity_min = preset["speed"] * 0.6
	process_material.initial_velocity_max = preset["speed"] * 1.2
	process_material.scale_min = 0.7
	process_material.scale_max = 1.3
	process_material.color = preset["color"]
	# Never write depth for accumulating translucent elements (candle/fire/
	# smoke overlapping each other), or they'd cut into one another instead
	# of adding up (§13.B.14). This is depth_draw_mode (write), not
	# no_depth_test (read) — disabling the read as well would break correct
	# occlusion behind solid geometry, which is not the problem being
	# solved here.
	_try_set(material, "depth_draw_mode", BaseMaterial3D.DEPTH_DRAW_DISABLED)

	var resources := {"mesh": mesh, "process_material": process_material, "preset": preset}
	cache[category] = resources
	return resources


static func _try_set(obj: Object, property: String, value) -> void:
	# Godot particle-material property names have moved between minor
	# versions; a direct unguarded set would break the whole constructor
	# on an older/newer editor that does not expose this exact name yet.
	if property in obj:
		obj.set(property, value)


static func build_vfx_substitutes(
	manifest: Dictionary,
	root_node: Node3D,
	stats: Dictionary
) -> void:
	var niagara_list: Array = manifest.get("effects", {}).get("niagara", [])
	if niagara_list.size() == 0:
		return

	var resolved: Dictionary = manifest.get("pipeline", {}).get("resolved_config", {})
	var vfx_cfg: Dictionary = resolved.get("vfx", {})
	var keywords: Array = vfx_cfg.get("category_rules", [])
	var keyword_pairs: Array = []
	for rule in keywords:
		if rule.has("match") and rule.has("category"):
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
		var sys_name: String = item.get("system_name", item.get("name", "VFX"))
		var tf_data: Dictionary = item.get("transform", {})
		var tf := TransformConverter.transform_from_v10(tf_data)

		var category := _category_for_name(sys_name, keyword_pairs)
		var resources := _shared_resources_for_category(category, resource_cache)
		var preset: Dictionary = resources["preset"]

		# Marker transform is the actor/component origin exported from
		# Unreal — for a torch/candle this is almost always the mesh's
		# BASE (pivot at socket/ground), not the flame. Without this
		# offset every flame would sit at the foot of its prop (§3.9.6).
		# transform.basis.y is used un-normalized on purpose, so it
		# carries the instance's own scale.
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
		# Shadow casting is a GeometryInstance3D (node) property in Godot 4,
		# not a material one — small billboard quads casting shadows onto
		# the terrain/walls would look wrong and cost real-time performance
		# for no visual gain. _try_set guards a property name that has
		# moved between minor versions.
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


# step4_decals_vfx.py writes audio.markers[] into ue5_godot_decal_map.json
# whenever audio.policy == "markers" — this side of the pipeline used to
# never read that key at all, so the audio markers the Python step produced
# were silently dropped (see ANALYSE_PROFONDE_S15 §15.8). Mirrors
# build_vfx_markers()'s honesty: a Marker3D per audio component, grouped
# under a name that says plainly nothing was converted, no attempt to fake
# 3D audio playback that was never built.
static func build_audio_markers(
	decal_map: Dictionary,
	root_node: Node3D,
	stats: Dictionary
) -> void:
	var audio_section: Dictionary = decal_map.get("audio", {})
	var markers_list: Array = audio_section.get("markers", [])
	if markers_list.size() == 0:
		return

	var audio_root := Node3D.new()
	audio_root.name = "AUDIO_MARKERS_NOT_CONVERTED"
	root_node.add_child(audio_root)
	audio_root.owner = root_node

	for item in markers_list:
		var tf_data: Dictionary = item.get("transform", {})
		var tf := TransformConverter.transform_from_v10(tf_data)

		var marker := Marker3D.new()
		var sound_path: String = item.get("sound_path", "")
		marker.name = sound_path.get_file() if sound_path != "" else "Audio"
		marker.transform = tf
		marker.set_meta("ue_component_path", item.get("component_path", ""))
		marker.set_meta("ue_sound_path", sound_path)

		audio_root.add_child(marker)
		marker.owner = root_node
		stats["built_audio_markers"] = stats.get("built_audio_markers", 0) + 1
