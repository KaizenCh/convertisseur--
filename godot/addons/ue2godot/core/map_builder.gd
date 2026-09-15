# addons/ue2godot/core/map_builder.gd
class_name MapBuilder
extends RefCounted


static func _load_json(path: String, report: Dictionary, label: String) -> Dictionary:
	if not FileAccess.file_exists(path):
		return {}
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		(report["warnings"] as Array).append(label + " illisible (ouverture refusée) : " + path)
		return {}
	var text := f.get_as_text()
	f.close()
	if text.strip_edges() == "":
		(report["warnings"] as Array).append(label + " vide : " + path)
		return {}
	var parsed = JSON.parse_string(text)
	if parsed == null or not (parsed is Dictionary):
		(report["errors"] as Array).append(
			label + " : JSON invalide ou trop volumineux pour être parsé (" +
			str(text.length()) + " caractères) — " + path
		)
		return {}
	return parsed as Dictionary


static func build(cfg: Dictionary) -> Dictionary:
	var manifest_path: String = cfg.get("manifest_path", "res://level_manifest_v10.json")
	var asset_map_path: String = cfg.get("asset_map_path", "res://ue5_godot_asset_map.json")
	var decal_map_path: String = cfg.get("decal_map_path", "res://ue5_godot_decal_map.json")
	var output_scene_path: String = cfg.get("output_scene", "res://Map--_REBUILT.tscn")

	var report := {
		"status": "OK",
		"built_nodes": 0,
		"errors": [],
		"warnings": [],
		"stats": {},
		"declared": {},
		"inputs": {
			"manifest": manifest_path,
			"asset_map": asset_map_path,
			"decal_map": decal_map_path,
		},
	}

	if not FileAccess.file_exists(manifest_path):
		report["status"] = "FAILED"
		(report["errors"] as Array).append("Manifest introuvable : " + manifest_path)
		return report

	var manifest_data := _load_json(manifest_path, report, "Manifest")

	if manifest_data.is_empty():
		var slim_path := manifest_path.replace(".json", ".slim.json")
		if FileAccess.file_exists(slim_path):
			(report["warnings"] as Array).append(
				"Manifeste complet illisible — bascule sur le manifeste allégé : " + slim_path
			)
			manifest_data = _load_json(slim_path, report, "Manifest allégé")
			if not manifest_data.is_empty():
				report["manifest_source"] = "slim_fallback"
				var demoted: Array = report["errors"]
				for message in demoted:
					(report["warnings"] as Array).append("(repli) " + str(message))
				report["errors"] = []

	if manifest_data.is_empty():
		report["status"] = "FAILED"
		(report["errors"] as Array).append(
			"Le manifeste n'a produit aucune donnée exploitable, ni en version " +
			"complète ni en version allégée — rien ne peut être reconstruit."
		)
		return report

	var asset_map_data := _load_json(asset_map_path, report, "Asset map")
	var decal_map_data := _load_json(decal_map_path, report, "Decal map")

	if asset_map_data.is_empty():
		(report["warnings"] as Array).append(
			"Asset map absente ou vide (" + asset_map_path + ") — aucun mesh ne " +
			"pourra être résolu : la scène sera vide de géométrie."
		)
	if decal_map_data.is_empty():
		(report["warnings"] as Array).append(
			"Decal map absente ou vide (" + decal_map_path + ") — les décals " +
			"déclarés dans le manifeste seront absents de la scène. " +
			"L'étape 4 (decals_vfx) a-t-elle bien été exécutée côté Unreal ?"
		)

	# Safety check on data sections
	var geometry_raw = manifest_data.get("geometry", {})
	var geometry: Dictionary = geometry_raw if geometry_raw is Dictionary else {}

	var placements_raw = geometry.get("placements", [])
	var placements: Array = placements_raw if placements_raw is Array else []

	var skeletal_raw = geometry.get("skeletal_mesh_placements", [])
	var skeletal_placements: Array = skeletal_raw if skeletal_raw is Array else []

	var effects_raw = manifest_data.get("effects", {})
	var effects: Dictionary = effects_raw if effects_raw is Dictionary else {}

	var declared_decals_raw = effects.get("decals", [])
	var declared_decals: Array = declared_decals_raw if declared_decals_raw is Array else []

	var declared_niagara_raw = effects.get("niagara", [])
	var declared_niagara: Array = declared_niagara_raw if declared_niagara_raw is Array else []

	var features_raw = manifest_data.get("world_features", {})
	var features: Dictionary = features_raw if features_raw is Dictionary else {}

	var declared_lights_raw = features.get("lights", [])
	var declared_lights: Array = declared_lights_raw if declared_lights_raw is Array else []

	var asset_entries_raw = asset_map_data.get("assets", {})
	var asset_entries: Dictionary = asset_entries_raw if asset_entries_raw is Dictionary else {}

	var declared := {
		"placements": placements.size(),
		"skeletal_placements": skeletal_placements.size(),
		"decals": declared_decals.size(),
		"niagara": declared_niagara.size(),
		"lights": declared_lights.size(),
		"assets_in_map": asset_entries.size(),
	}
	report["declared"] = declared

	if placements.size() == 0 and skeletal_placements.size() == 0:
		report["status"] = "FAILED"
		(report["errors"] as Array).append(
			"Le manifeste ne déclare AUCUN placement géométrique. " +
			"L'étape 1 (manifest) a-t-elle réellement scanné la map ? " +
			"Une scène vide serait produite ; construction abandonnée."
		)
		return report

	var root_node := Node3D.new()
	root_node.name = "Map_REBUILT"
	var stats := {}

	for p in placements:
		if p is Dictionary:
			GeometryBuilder.build_geometry_placement(p, asset_map_data, root_node, stats)
		else:
			stats["invalid_placement_skipped"] = stats.get("invalid_placement_skipped", 0) + 1

	for p in skeletal_placements:
		if p is Dictionary:
			GeometryBuilder.build_geometry_placement(p, asset_map_data, root_node, stats)
		else:
			stats["invalid_placement_skipped"] = stats.get("invalid_placement_skipped", 0) + 1

	if skeletal_placements.size() > 0:
		stats["skeletal_mesh_placements_declared"] = skeletal_placements.size()

	DecalBuilder.build_decals(decal_map_data, manifest_data, root_node, stats)
	LightBuilder.build_lights(manifest_data, root_node, stats)
	VFXBuilder.build_vfx(decal_map_data, manifest_data, root_node, stats)
	VFXBuilder.build_audio_markers(decal_map_data, root_node, stats)

	var built_placements: int = stats.get("built_placements", 0)
	var total_declared_geo: int = placements.size() + skeletal_placements.size()
	report["built_nodes"] = root_node.get_child_count()

	if built_placements == 0:
		report["status"] = "FAILED"
		(report["errors"] as Array).append(
			"0 placement construit sur " + str(total_declared_geo) + " déclaré(s). " +
			"Causes comptées : " +
			"mapping asset manquant=" + str(stats.get("missing_asset_mapping", 0)) + ", " +
			"GLB introuvable=" + str(stats.get("missing_glb", 0)) + ", " +
			"instanciation échouée=" + str(stats.get("instantiate_failure", 0)) + ", " +
			"slot mesh vide=" + str(stats.get("skipped_slot_empty", 0)) + "."
		)
		root_node.free()
		report["stats"] = stats
		return report

	if built_placements < total_declared_geo:
		var lost := total_declared_geo - built_placements
		var ratio := float(lost) / float(total_declared_geo) * 100.0
		var msg := (
			str(lost) + " placement(s) sur " + str(total_declared_geo) +
			" (" + ("%.1f" % ratio) + " %) n'ont pas été construits — " +
			"mapping manquant=" + str(stats.get("missing_asset_mapping", 0)) +
			", GLB introuvable=" + str(stats.get("missing_glb", 0)) +
			", instanciation échouée=" + str(stats.get("instantiate_failure", 0)) +
			", slot vide=" + str(stats.get("skipped_slot_empty", 0)) + "."
		)
		if ratio >= 50.0:
			report["status"] = "FAILED"
			(report["errors"] as Array).append(msg)
		else:
			report["status"] = "OK_WITH_WARNINGS"
			(report["warnings"] as Array).append(msg)

	if declared_decals.size() > 0 and stats.get("built_decals", 0) == 0:
		if report["status"] == "OK":
			report["status"] = "OK_WITH_WARNINGS"
		(report["warnings"] as Array).append(
			str(declared_decals.size()) + " décal(s) déclaré(s) mais aucun construit."
		)

	var packed_scene := PackedScene.new()
	var pack_result := packed_scene.pack(root_node)

	if pack_result != OK:
		report["status"] = "FAILED"
		(report["errors"] as Array).append("Échec de pack() de la scène : " + str(pack_result))
		root_node.free()
		report["stats"] = stats
		return report

	var save_result := ResourceSaver.save(packed_scene, output_scene_path)
	if save_result != OK:
		report["status"] = "FAILED"
		(report["errors"] as Array).append("Échec de sauvegarde de la scène : " + str(save_result))
		root_node.free()
		report["stats"] = stats
		return report

	report["output_scene"] = output_scene_path
	report["stats"] = stats
	root_node.free()
	return report
