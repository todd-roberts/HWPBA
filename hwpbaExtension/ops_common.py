# pyright: reportInvalidTypeForm=false
import bpy, os, sys, subprocess, re
from mathutils import Vector, Matrix
import statistics as stats

from .utils import settings, validate, ensure_dirs

# ---------------- Existing operators (with descriptions) ----------------

class HWPBA_OT_SelectActiveCollection(bpy.types.Operator):
    bl_idname = "hwpba.select_active_collection"
    bl_label = "Select Active Collection"
    bl_description = "Use the active object's collection as the Source Collection (optional)"

    def execute(self, context):
        s = settings(context)
        if not s:
            self.report({'ERROR'}, "Settings not initialized")
            return {'CANCELLED'}

        act = context.active_object
        coll_name = None
        if act:
            cols = getattr(act, "users_collection", [])
            if cols:
                coll_name = cols[0].name

        # Note: context.collection is a LayerCollection; we only need its name here.
        if not coll_name and hasattr(context, "collection") and context.collection:
            coll_name = context.collection.name

        if not coll_name:
            self.report({'ERROR'}, "No active collection found. Select an object that lives in a collection.")
            return {'CANCELLED'}

        s.source_collection = coll_name
        self.report({'INFO'}, f"Source Collection set to '{coll_name}'")
        return {'FINISHED'}

class HWPBA_OT_ClearSourceCollection(bpy.types.Operator):
    bl_idname = "hwpba.clear_source_collection"
    bl_label = "Clear Source Collection"
    bl_description = "Clear the Source Collection override (use all visible meshes)"

    def execute(self, context):
        s = settings(context)
        if s:
            s.source_collection = ""
        self.report({'INFO'}, "Source Collection cleared")
        return {'FINISHED'}

class HWPBA_OT_OpenOutput(bpy.types.Operator):
    bl_idname = "hwpba.open_output"
    bl_label = "Open Output Folder"
    bl_description = "Open the HWPBA_Output folder in your file browser"

    def execute(self, context):
        ok, msg, _, _, base_abs = validate(context)
        if not ok:
            self.report({'ERROR'}, msg or "Choose Output Folder in panel")
            return {'CANCELLED'}
        root, _, _, _ = ensure_dirs(base_abs)
        try:
            if sys.platform.startswith("win"):
                os.startfile(root)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.call(["open", root])
            else:
                subprocess.call(["xdg-open", root])
        except Exception as e:
            self.report({'ERROR'}, f"Could not open folder: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}

# ---------------- Auto-Rig From Parts ----------------

ARMATURE_NAME = "HWPBA_Rig"
BONE_LENGTH_FACTOR = 0.25
BONE_MIN_LEN = 0.06
ARMATURE_DISPLAY = 'OCTAHEDRAL'  # OCTAHEDRAL | STICK | WIRE | BBONE | ENVELOPE

def _collect_parts_from_scene(context):
    """Prefer Source Collection if set; else all visible meshes in view layer.
    Always return a real bpy.types.Collection for host."""
    s = settings(context)
    if s and s.source_collection:
        coll = bpy.data.collections.get(s.source_collection)
        if coll:
            parts = [o for o in coll.objects if o.type == "MESH" and o.visible_get()]
            if parts:
                return parts, coll

    # Fallback: visible meshes
    parts = [o for o in context.view_layer.objects if o.type == "MESH" and o.visible_get()]
    if parts and parts[0].users_collection:
        host = parts[0].users_collection[0]
    else:
        # active_layer_collection is a LayerCollection; grab its .collection (the real Collection)
        host = context.view_layer.active_layer_collection.collection
    return parts, host

def _median_part_size(objs):
    sizes = []
    for o in objs:
        bb = [o.matrix_world @ Vector(c) for c in o.bound_box]
        minv = Vector((min(p.x for p in bb), min(p.y for p in bb), min(p.z for p in bb)))
        maxv = Vector((max(p.x for p in bb), max(p.y for p in bb), max(p.z for p in bb)))
        sizes.append((maxv - minv).length)
    return stats.median(sizes) if sizes else 1.0

def _ensure_armature(host_collection):
    arm_data = bpy.data.armatures.new(ARMATURE_NAME + "_Data")
    arm_obj  = bpy.data.objects.new(ARMATURE_NAME, arm_data)
    host_collection.objects.link(arm_obj)

    # World origin, identity transform
    arm_obj.matrix_world = Matrix.Identity(4)

    # Visibility defaults
    arm_obj.show_in_front = True
    arm_data.display_type = ARMATURE_DISPLAY
    arm_data.use_mirror_x = True  # good default for posing
    return arm_obj, arm_data

def _enter_edit(arm_obj):
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.objects.active = arm_obj
    for o in bpy.context.selected_objects:
        o.select_set(False)
    arm_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')

def _exit_edit():
    bpy.ops.object.mode_set(mode='OBJECT')

def _create_bone(arm_data, arm_obj, name: str, head_world: Vector, length: float):
    eb = arm_data.edit_bones.new(_safe_name(name))
    head_local = arm_obj.matrix_world.inverted() @ head_world
    eb.head = head_local
    eb.tail = head_local + Vector((0.0, 0.0, max(BONE_MIN_LEN, length)))
    eb.roll = 0.0
    return eb

def _safe_name(s: str) -> str:
    s = s.strip()
    suffix = ""
    m = re.search(r"(\.[LR])$", s, re.I)
    if m:
        suffix = m.group(1)
        s = s[: -len(suffix)]
    core = re.sub(r'[^A-Za-z0-9_\-]+', '_', s).strip('_')
    return (core + suffix) if suffix else core

def _parent_bones_from_object_hierarchy(objs, bone_map):
    """If an object has a parent that is also a part, parent the corresponding bones."""
    part_set = set(objs)
    for o in objs:
        b = bone_map.get(o)
        if not b: continue
        p = o.parent
        while p and p not in part_set:
            p = p.parent
        if p and p in bone_map:
            b.parent = bone_map[p]

def _assign_full_weight(obj, bone_name: str):
    obj.vertex_groups.clear()
    vg = obj.vertex_groups.new(name=_safe_name(bone_name))
    me = obj.data
    vg.add(range(len(me.vertices)), 1.0, 'REPLACE')

def _add_armature_mod(obj, arm_obj):
    for m in list(obj.modifiers):
        if m.type == 'ARMATURE':
            obj.modifiers.remove(m)
    mod = obj.modifiers.new(name="Armature", type='ARMATURE')
    mod.object = arm_obj
    mod.use_vertex_groups = True
    mod.use_bone_envelopes = False
    return mod

def _view3d_override(context, active_obj, selected_objs):
    """Build a 3D View override so operators have an active object + selection."""
    win = context.window
    scr = win.screen if win else None
    area = next((a for a in scr.areas if a.type == 'VIEW_3D'), None) if scr else None
    region = next((r for r in (area.regions if area else [] ) if r.type == 'WINDOW'), None)
    override = {
        "window": win,
        "screen": scr,
        "area": area,
        "region": region,
        "active_object": active_obj,
        "object": active_obj,
        "selected_objects": selected_objs,
        "selected_editable_objects": selected_objs,
        "view_layer": context.view_layer,
        "scene": context.scene,
    }
    return {k: v for k, v in override.items() if v is not None}

def _apply_rot_scale(context, parts):
    """Apply rotation & scale (NOT location) to all parts, robustly."""
    if not parts:
        return
    active = parts[0]

    for o in context.selected_objects:
        o.select_set(False)
    for o in parts:
        o.select_set(True)
    context.view_layer.objects.active = active

    ov = _view3d_override(context, active, parts)

    try:
        with context.temp_override(**ov):
            if bpy.context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception as e:
            print(f"[HWPBA] mode_set fallback failed: {e}")

    try:
        with context.temp_override(**ov):
            bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    except Exception as e:
        print(f"[HWPBA] Transform apply warning: {e}")

    for o in parts:
        o.select_set(False)

def _name_sanity(parts):
    """Warn about duplicate cleaned names and unmatched .L/.R pairs."""
    cleaned = {}
    left_set, right_set = set(), set()
    for o in parts:
        n = _safe_name(o.name)
        if n in cleaned:
            print(f"[HWPBA][Name Warning] Duplicate part name after cleaning: '{n}'")
        cleaned[n] = True
        if n.lower().endswith(".l"):
            left_set.add(n[:-2].lower())
        if n.lower().endswith(".r"):
            right_set.add(n[:-2].lower())
    for base in sorted(left_set ^ right_set):
        print(f"[HWPBA][Name Info] Unmatched side for '{base}': only one of .L/.R present")

# ---------- Helper to detect existing rig influence ----------

def _parts_have_any_armature(parts):
    """True if any part is already influenced by an armature (parent or modifier or find_armature)."""
    for o in parts:
        if o.type != "MESH":
            continue
        if o.parent and o.parent.type == "ARMATURE":
            return True
        for m in o.modifiers:
            if m.type == 'ARMATURE' and m.object:
                return True
        if o.find_armature():
            return True
    return False

class HWPBA_OT_AutoRigFromParts(bpy.types.Operator):
    """Create a simple socket-pivot rig from separated parts.
    - Armature origin at world origin (0,0,0)
    - Applies Rotation & Scale (keeps Location)
    - One bone per part, head at part origin, tail +Z
    - Parents bones from object parenting when present
    - 100% weight to its own bone
    """
    bl_idname = "hwpba.autorig_from_parts"
    bl_label = "Auto-Rig From Parts"
    bl_description = "Create a per-part socket rig and skin parts (applies Rotation & Scale only)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        parts, _ = _collect_parts_from_scene(context)
        if not parts:
            return False
        return not _parts_have_any_armature(parts)

    def execute(self, context):
        parts, host_coll = _collect_parts_from_scene(context)
        parts = [o for o in parts if o.type == "MESH"]
        if not parts:
            self.report({'ERROR'}, "No mesh parts found (visible meshes only).")
            return {'CANCELLED'}

        _name_sanity(parts)
        _apply_rot_scale(context, parts)

        median_size = _median_part_size(parts)
        bone_len = max(BONE_MIN_LEN, median_size * BONE_LENGTH_FACTOR)

        arm_obj, arm_data = _ensure_armature(host_coll)

        _enter_edit(arm_obj)
        bone_map_by_obj = {}
        for o in parts:
            eb = _create_bone(arm_data, arm_obj, o.name, o.matrix_world.translation, bone_len)
            bone_map_by_obj[o] = eb

        _parent_bones_from_object_hierarchy(parts, bone_map_by_obj)

        _exit_edit()

        if len(arm_data.bones) == 0:
            self.report({'ERROR'}, "No bones created.")
            return {'CANCELLED'}

        for o in parts:
            _assign_full_weight(o, o.name)
            _add_armature_mod(o, arm_obj)

        bpy.context.view_layer.objects.active = arm_obj
        arm_obj.select_set(True)

        self.report({'INFO'}, f"Auto-rig complete: {len(arm_data.bones)} bones (Origin at world, Show In Front, Octahedral).")
        return {'FINISHED'}

# ---------------- NEW: Clean Pre-Existing Armature / Animations ----------------

def _parts_in_scope(context):
    """Return (parts, host_collection) like _collect_parts_from_scene but meshes only."""
    parts, host = _collect_parts_from_scene(context)
    return [o for o in parts if o.type == "MESH"], host

def _armatures_referenced_by(parts):
    """Armature objects referenced by Armature modifiers on given parts."""
    refs = set()
    for o in parts:
        for m in o.modifiers:
            if m.type == 'ARMATURE' and m.object and m.object.type == 'ARMATURE':
                refs.add(m.object)
    return list(refs)

def _purge_orphan_actions(disable_fake_user=True):
    """Remove Actions that have zero users. Optionally disable fake users first."""
    removed = 0
    for act in list(bpy.data.actions):
        if disable_fake_user and act.use_fake_user:
            act.use_fake_user = False
    for act in list(bpy.data.actions):
        if act.users == 0:
            try:
                bpy.data.actions.remove(act)
                removed += 1
            except Exception:
                pass
    return removed

class HWPBA_OT_CleanPreexistingArmature(bpy.types.Operator):
    """Remove legacy Armature modifiers & vertex groups from parts.
    Unlink Action/NLA from referenced armatures and purge orphan Actions.
    Keeps the HWPBA_Rig if present.
    """
    bl_idname = "hwpba.clean_preexisting_armature"
    bl_label = "Clean Pre-Existing Rig & Anim"
    bl_description = "Remove old Armature modifiers/weights on parts, unlink animations on legacy rigs, and purge orphan Actions"
    bl_options = {"REGISTER", "UNDO"}

    keep_hwpba_rig: bpy.props.BoolProperty(
        name="Keep HWPBA_Rig",
        default=True,
        description="Do not touch the HWPBA_Rig (if present)",
    )
    clear_vertex_groups: bpy.props.BoolProperty(
        name="Clear Vertex Groups on Parts",
        default=False,  # SAFE DEFAULT
        description="Remove all vertex groups from each part",
    )
    disable_fake_user_before_purge: bpy.props.BoolProperty(
        name="Disable Fake User before purge",
        default=True,
        description="Turn off Fake User on actions so orphaned actions can be deleted",
    )

    @classmethod
    def poll(cls, context):
        parts, _ = _parts_in_scope(context)
        return bool(parts)

    def execute(self, context):
        parts, _ = _parts_in_scope(context)

        referenced = _armatures_referenced_by(parts)

        to_clean_armatures = []
        for arm in referenced:
            if self.keep_hwpba_rig and arm.name == ARMATURE_NAME:
                continue
            to_clean_armatures.append(arm)

        removed_mods = 0
        for o in parts:
            for m in list(o.modifiers):
                if m.type != 'ARMATURE':
                    continue
                if self.keep_hwpba_rig and m.object and m.object.name == ARMATURE_NAME:
                    continue
                try:
                    o.modifiers.remove(m)
                    removed_mods += 1
                except Exception:
                    pass
            if self.clear_vertex_groups and o.vertex_groups:
                o.vertex_groups.clear()

        unlinked = 0
        for arm in to_clean_armatures:
            try:
                if arm.animation_data:
                    arm.animation_data_clear()
                    unlinked += 1
            except Exception:
                pass

        purged = _purge_orphan_actions(disable_fake_user=self.disable_fake_user_before_purge)

        self.report(
            {'INFO'},
            f"Cleaned: removed {removed_mods} Armature modifiers; "
            f"unlinked anim from {unlinked} armature(s); purged {purged} orphan action(s)."
        )
        return {'FINISHED'}
