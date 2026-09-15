# addons/ue2godot/core/transform.gd
class_name TransformConverter
extends RefCounted

const UE_CM_TO_GODOT_M := 0.01

static func unreal_rotator_to_basis(pitch: float, yaw: float, roll: float) -> Basis:
	var p := deg_to_rad(pitch)
	var y := deg_to_rad(yaw)
	var r := deg_to_rad(roll)

	var cp := cos(p * 0.5)
	var sp := sin(p * 0.5)
	var cy := cos(y * 0.5)
	var sy := sin(y * 0.5)
	var cr := cos(r * 0.5)
	var sr := sin(r * 0.5)

	var qx := cr * sp * sy - sr * cp * cy
	var qy := -cr * sp * cy - sr * cp * sy  # V10.7 fix: negative qy
	var qz := cr * cp * sy - sr * sp * cy
	var qw := cr * cp * cy + sr * sp * sy

	return Basis(Quaternion(qx, qy, qz, qw))

static func convert_basis_ue_to_godot(ue_basis: Basis) -> Basis:
	# C matrix: Gx=Ux, Gy=Uz, Gz=Uy (det -1)
	var c := Basis(
		Vector3(1, 0, 0),
		Vector3(0, 0, 1),
		Vector3(0, 1, 0)
	)
	return c * ue_basis * c.inverse()

static func transform_from_v10(data: Variant) -> Transform3D:
	if not (data is Dictionary):
		return Transform3D.IDENTITY

	var dict_data: Dictionary = data as Dictionary

	var loc_raw = dict_data.get("location", null)
	var loc := [0.0, 0.0, 0.0]
	if loc_raw is Array and (loc_raw as Array).size() >= 3:
		var arr: Array = loc_raw as Array
		loc = [float(arr[0]), float(arr[1]), float(arr[2])]

	var rot_raw = dict_data.get("rotation", null)
	var rot := {"pitch": 0.0, "yaw": 0.0, "roll": 0.0}
	if rot_raw is Dictionary:
		var rot_dict: Dictionary = rot_raw as Dictionary
		rot["pitch"] = float(rot_dict.get("pitch", 0.0))
		rot["yaw"] = float(rot_dict.get("yaw", 0.0))
		rot["roll"] = float(rot_dict.get("roll", 0.0))

	var scl_raw = dict_data.get("scale", null)
	var scl := [1.0, 1.0, 1.0]
	if scl_raw is Array and (scl_raw as Array).size() >= 3:
		var arr: Array = scl_raw as Array
		scl = [float(arr[0]), float(arr[1]), float(arr[2])]

	var ue_pos := Vector3(loc[0], loc[1], loc[2])
	var ue_rot := Vector3(rot["pitch"], rot["yaw"], rot["roll"])
	var ue_scl := Vector3(scl[0], scl[1], scl[2])

	var ue_basis := unreal_rotator_to_basis(ue_rot.x, ue_rot.y, ue_rot.z)
	var converted_basis := convert_basis_ue_to_godot(ue_basis)
	converted_basis = converted_basis.scaled(ue_scl)

	var godot_pos := Vector3(ue_pos.x, ue_pos.z, ue_pos.y) * UE_CM_TO_GODOT_M
	return Transform3D(converted_basis, godot_pos)
