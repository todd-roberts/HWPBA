# pyright: reportInvalidTypeForm=false
import bpy, os, time
from mathutils import Matrix
from .utils import (
    settings, clean, validate, ensure_dirs, find_parts,
    gather_images_from_objects, save_or_copy_image_to, write_instructions,
    clean_known_outputs,
)
from .gltf_to_json import convert_gltf_to_json

# ---------------------------------------------------------------------------
# Helpers: safe context / mode management
# ---------------------------------------------------------------------------

def _view3d_override(context, active_obj, selected_objs):
    win = context.window
    scr = win.screen if win else None
    area = next((a for a in (scr.areas if scr else []) if a.type == 'VIEW_3D'), None)
    region = next((r for r in (area.regions if area else []) if r.type == 'WINDOW'), None)
    ov = {
        "window": win, "screen": scr, "area": area, "region": region,
        "active_object": active_obj, "object": active_obj,
        "selected_objects": selected_objs, "selected_editable_objects": selected_objs,
        "view_layer": context.view_layer, "scene": context.scene,
    }
    return {k: v for k, v in ov.items() if v is not None}

def _ensure_object_mode(context, active=None):
    if active is None:
        active = context.view_layer.objects.active
        if active is None:
            for o in context.view_layer.objects:
                if o.type in {"MESH", "ARMATURE"}:
                    active = o
                    break
    if active:
        context.view_layer.objects.active = active

    ov = _view3d_override(context, active, [active] if active else [])
    try:
        with context.temp_override(**ov):
            if bpy.context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        try:
            if bpy.context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        except Exception as e:
            print(f"[HWPBA] mode_set fallback failed: {e}")

# ---------------------------------------------------------------------------
# Scene snapshot/restore (protect Pose Mode & transforms)
# ---------------------------------------------------------------------------

def _find_armature_from_parts(parts):
    arm = None
    for o in parts:
        a = o.find_armature() or (o.parent if o.parent and o.parent.type == "ARMATURE" else None)
        if a:
            arm = a
            break
    return arm

def _snapshot_scene(context, parts, arm):
    state = {}
    state["orig_mode"] = bpy.context.mode
    state["active_name"] = context.view_layer.objects.active.name if context.view_layer.objects.active else None
    state["sel_names"] = [o.name for o in context.selected_objects]

    objset = set(parts)
    if arm: objset.add(arm)
    state["obj_mats"] = {o.name: o.matrix_world.copy() for o in objset}

    pose_basis = {}
    if arm and arm.type == "ARMATURE" and arm.pose:
        for pb in arm.pose.bones:
            pose_basis[pb.name] = pb.matrix_basis.copy()
    state["pose_basis"] = pose_basis

    return state

def _restore_scene(context, state, parts, arm):
    _ensure_object_mode(context)

    for o in parts:
        mw = state["obj_mats"].get(o.name)
        if mw is not None:
            o.matrix_world = mw
    if arm:
        mw = state["obj_mats"].get(arm.name)
        if mw is not None:
            arm.matrix_world = mw

    if arm and arm.type == "ARMATURE" and arm.pose and state["pose_basis"]:
        for pb in arm.pose.bones:
            mb = state["pose_basis"].get(pb.name)
            if mb is not None:
                pb.matrix_basis = mb

    for o in list(context.selected_objects):
        o.select_set(False)
    for name in state["sel_names"]:
        ob = context.view_layer.objects.get(name)
        if ob:
            ob.select_set(True)

    if state["active_name"]:
        ao = context.view_layer.objects.get(state["active_name"])
        if ao:
            context.view_layer.objects.active = ao

    target_mode = state["orig_mode"]
    if target_mode and target_mode != bpy.context.mode:
        if target_mode == 'POSE' or target_mode == 'EDIT_ARMATURE':
            if arm:
                context.view_layer.objects.active = arm
        ov = _view3d_override(context, context.view_layer.objects.active, list(context.selected_objects))
        try:
            with context.temp_override(**ov):
                bpy.ops.object.mode_set(mode=target_mode)
        except Exception:
            try:
                bpy.ops.object.mode_set(mode=target_mode)
            except Exception as e:
                print(f"[HWPBA] restore mode failed: {e}")

    try:
        bpy.context.view_layer.update()
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Initial placement (RAW world-space)
# ---------------------------------------------------------------------------

def _compute_initial_positions(objs):
    initial = {}
    for o in objs:
        t = o.matrix_world.translation
        initial[o.name] = [float(t.x), float(t.y), float(t.z)]
    return initial

# ---------------------------------------------------------------------------
# Filename safety
# ---------------------------------------------------------------------------

_ILLEGAL = set('<>:"/\\|?*')

def _safe_filename_component(s: str) -> str:
    out = "".join(c if c not in _ILLEGAL else "_" for c in s)
    return out.rstrip(" .")

# ---------------------------------------------------------------------------
# TEMP bone/object name re-sync for export (minimal + safe)
# ---------------------------------------------------------------------------

def _resync_bone_names_for_export(context, parts, arm):
    ops = []
    if not arm or arm.type != 'ARMATURE':
        return ops

    _ensure_object_mode(context, arm)
    bones = arm.data.bones

    for obj in parts:
        vg_name = None
        for vg in obj.vertex_groups:
            if vg.name in bones:
                vg_name = vg.name
                break
        if not vg_name:
            continue

        new_name = obj.name
        if vg_name == new_name:
            continue
        if new_name in bones:
            continue

        try:
            bones[vg_name].name = new_name
            vg = obj.vertex_groups.get(vg_name)
            if vg:
                vg.name = new_name
            ops.append((obj.name, vg_name, new_name))
        except Exception as e:
            print(f"[HWPBA] Bone/vgroup rename failed for '{obj.name}': {e}")

    return ops

def _undo_resync_bone_names(arm, ops):
    if not arm or arm.type != 'ARMATURE':
        return
    bones = arm.data.bones
    for obj_name, old_name, new_name in reversed(ops):
        try:
            if new_name in bones and old_name not in bones:
                bones[new_name].name = old_name
        except Exception:
            pass
        obj = bpy.data.objects.get(obj_name)
        if obj:
            vg = obj.vertex_groups.get(new_name)
            if vg:
                try:
                    vg.name = old_name
                except Exception:
                    pass

# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

def _export_parts_fbx(context, models_dir, prefix: str):
    """Export each mesh part as its own FBX (Apply Transform; no per-part texture folders)."""
    objs, _ = find_parts(context)

    prev_sel = list(context.selected_objects)
    prev_active = context.view_layer.objects.active
    exported = 0

    for obj in objs:
        if obj.type != "MESH":
            continue

        _ensure_object_mode(context, obj)

        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        stem = f"{prefix}{obj.name}"
        stem_safe = _safe_filename_component(stem)
        fbx_path = os.path.join(models_dir, stem_safe + ".fbx")

        ov = _view3d_override(context, obj, [obj])
        try:
            with context.temp_override(**ov):
                bpy.ops.export_scene.fbx(
                    filepath=fbx_path,
                    use_selection=True,
                    bake_space_transform=True,
                    apply_unit_scale=True,
                    apply_scale_options='FBX_SCALE_UNITS',
                    axis_forward='-Z',
                    axis_up='Y',
                    use_mesh_modifiers=True,
                    mesh_smooth_type='FACE',
                    add_leaf_bones=False,
                    bake_anim=False,
                    path_mode='AUTO',
                    embed_textures=False,
                )
            exported += 1
        except Exception as e:
            print(f"[HWPBA] FBX export failed for '{obj.name}': {e}")

    # Restore selection
    for o in context.selected_objects:
        o.select_set(False)
    if prev_active:
        try:
            context.view_layer.objects.active = prev_active
        except Exception:
            pass
    for o in prev_sel:
        try:
            o.select_set(True)
        except Exception:
            pass

    # ---- COPY/SAVE TEXTURES with Horizon-friendly names ----
    img_specs = gather_images_from_objects(objs)  # [(image, 'Base_BR'), ...]
    existing_by_name = {}
    copied = 0
    for (img, base) in img_specs:
        out = save_or_copy_image_to(img, models_dir, existing_by_name, preferred_base=base)
        if out:
            copied += 1

    return exported, copied, objs

def _export_gltf_and_write_json(context, temp_dir, json_out_path, initial_positions, name_prefix: str):
    """Export GLTF (separate) of the armature + parts, then write [Name]_Animations.json with wrapper."""
    objs, _ = find_parts(context)
    arm = _find_armature_from_parts(objs)

    # --- TEMP re-sync bones to current object names (restore after) ---
    resync_ops = _resync_bone_names_for_export(context, objs, arm)

    prev_sel = list(context.selected_objects)
    prev_active = context.view_layer.objects.active

    sel = list(objs)
    if arm:
        sel.append(arm)
        active = arm
    else:
        active = objs[0] if objs else None

    _ensure_object_mode(context, active)
    for o in context.selected_objects:
        o.select_set(False)
    for o in sel:
        try:
            o.select_set(True)
        except Exception:
            pass
    if active:
        context.view_layer.objects.active = active

    s = settings(context)
    char_name = (s.character_name if s and s.character_name else
                 bpy.path.display_name_from_filepath(bpy.data.filepath) or "Character")
    gltf_path = os.path.join(temp_dir, f"{clean(char_name)}.gltf")

    ov = _view3d_override(context, active, sel if active else sel[:1])
    try:
        with context.temp_override(**ov):
            bpy.ops.export_scene.gltf(
                filepath=gltf_path,
                export_format='GLTF_SEPARATE',
                export_animations=True,
                export_yup=True,
                export_apply=True,
                use_selection=True,
                export_optimize_animation_size=True
            )
    except Exception as e:
        _undo_resync_bone_names(arm, resync_ops)
        for o in context.selected_objects:
            o.select_set(False)
        if prev_active:
            try:
                context.view_layer.objects.active = prev_active
            except Exception:
                pass
        for o in prev_sel:
            try:
                o.select_set(True)
            except Exception:
                pass
        return False, f"GLTF export failed: {e}"

    # Restore selection
    for o in context.selected_objects:
        o.select_set(False)
    if prev_active:
        try:
            context.view_layer.objects.active = prev_active
        except Exception:
            pass
    for o in prev_sel:
        try:
            o.select_set(True)
        except Exception:
            pass

    # Wait for .bin to exist
    bin_path = os.path.splitext(gltf_path)[0] + ".bin"
    t0 = time.time()
    while not os.path.exists(bin_path):
        if time.time() - t0 > 10:
            return False, "GLTF .bin not found after export"
        time.sleep(0.1)

    ok, msg = convert_gltf_to_json(
        gltf_path,
        json_out_path,
        initial_positions,   # raw world-space; converter will produce HW-native
        name_prefix=name_prefix,
    )

    return ok, msg

# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------

class HWPBA_OT_CreateFiles(bpy.types.Operator):
    bl_idname = "hwpba.create_files"
    bl_label = "Create Files"
    bl_description = ("Export FBXs + Textures to assetsToUpload/3dModels, "
                      "and write [Character]_Animations.json + instructions.txt")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        ok, _, _, _, _ = validate(context)
        return ok

    def execute(self, context):
        parts, _ = find_parts(context)
        arm = _find_armature_from_parts(parts)
        state = _snapshot_scene(context, parts, arm)

        try:
            _ensure_object_mode(context)

            ok, _, _, source, base_abs = validate(context)
            if not ok:
                self.report({'ERROR'}, "Validation failed")
                return {'CANCELLED'}

            root_dir, assets_dir, models_dir, temp_dir = ensure_dirs(base_abs)

            try:
                clean_known_outputs(assets_dir, models_dir, temp_dir)
            except Exception as e:
                print(f"[HWPBA] Pre-export cleanup skipped: {e}")

            s = settings(context)
            char_name = (s.character_name if s and s.character_name else
                         bpy.path.display_name_from_filepath(bpy.data.filepath) or "Character")

            prefix = clean(char_name) + "_"

            fbx_count, tex_count, objs = _export_parts_fbx(context, models_dir, prefix)
            initial_positions = _compute_initial_positions(objs)

            json_filename = f"{clean(char_name)}_Animations.json"
            json_out = os.path.join(assets_dir, json_filename)
            ok2, msg2 = _export_gltf_and_write_json(
                context, temp_dir, json_out, initial_positions, name_prefix=prefix
            )
            if not ok2:
                self.report({'ERROR'}, f"Animation JSON failed: {msg2}")
                return {'CANCELLED'}

            #write_instructions(root_dir, json_filename)

            src = f" from '{source}'" if source else ""
            self.report({'INFO'}, f"Exported {fbx_count} FBX part(s){src}; {tex_count} texture(s). {msg2}.")
            return {'FINISHED'}
        finally:
            try:
                _restore_scene(context, state, parts, arm)
            except Exception as e:
                print(f"[HWPBA] Scene restore warning: {e}")
