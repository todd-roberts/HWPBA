# pyright: reportInvalidTypeForm=false
import bpy
from .utils import validate, settings

class HWPBA_PT_Main(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "HWPBA"
    bl_label = "HW Parts-based Animation"

    def draw(self, context):
        layout = self.layout
        s = settings(context)

        # ── Settings ────────────────────────────────────────────────────────────
        box = layout.box()
        box.label(text="Settings", icon="PREFERENCES")
        row = box.row()
        row.prop(s, "character_name", text="Character Name")
        row = box.row()
        row.prop(s, "output_root", text="Output Folder")
        row = box.row(align=True)
        row.operator("hwpba.select_active_collection", text="Select Active Collection")
        row.operator("hwpba.clear_source_collection", text="", icon="X")
        if s and s.source_collection:
            box.label(text=f"Source: {s.source_collection}", icon="OUTLINER_COLLECTION")
        else:
            box.label(text="(Optional) Set a collection that contains your parts", icon="INFO")

        # ── Status ──────────────────────────────────────────────────────────────
        ok, msg, n, source, _ = validate(context)
        status = layout.box()
        if not ok:
            status.label(text=msg or "Not ready", icon="ERROR")
        else:
            src = f" from '{source}'" if source else ""
            status.label(text=f"{n} part(s){src}", icon="MESH_CUBE")

        # ── Cleanup ────────────────────────────────────────────────────────────
        clean = layout.box()
        clean.label(text="Cleanup", icon="BRUSH_DATA")
        col = clean.column(align=True)
        op = col.operator("hwpba.clean_preexisting_armature", text="Clean Pre-Existing Rig & Anim")
        # Safe defaults; user can tweak in the redo panel if desired.
        op.keep_hwpba_rig = True
        op.clear_vertex_groups = False   # SAFE DEFAULT (changed from True)
        op.disable_fake_user_before_purge = True
        clean.label(text="Removes old armature modifiers/weights; unlinks actions/NLA; purges orphan Actions.", icon="INFO")

        # ── Rigging ─────────────────────────────────────────────────────────────
        rig = layout.box()
        rig.label(text="Rigging", icon="ARMATURE_DATA")
        col = rig.column(align=True)
        col.operator("hwpba.autorig_from_parts", text="Auto-Rig From Parts")
        rig.label(text="(Applies Rotation & Scale; keeps Location; bones point +Z; Show In Front)", icon="INFO")

        # ── Export / Build ──────────────────────────────────────────────────────
        ex = layout.box()
        ex.label(text="Export / Build", icon="FILE_NEW")
        col = ex.column(align=True)
        col.operator("hwpba.create_files", text="Create Files")
        col.operator("hwpba.open_output", text="Open Output Folder")
