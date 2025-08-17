# pyright: reportInvalidTypeForm=false
import bpy

def _bl_idname():
    # Use the FULL package path as Blender registers it (works for Extensions and legacy)
    return __package__ or __name__

class HWPBA_Prefs(bpy.types.AddonPreferences):
    bl_idname = _bl_idname()

    output_root: bpy.props.StringProperty(
        name="Output Root",
        subtype='DIR_PATH',
        default="//HWPBA_Output"
    )
    character_name: bpy.props.StringProperty(
        name="Character Name",
        default=""
    )
    source_collection: bpy.props.StringProperty(
        name="Source Collection",
        default=""
    )

    def draw(self, ctx):
        c = self.layout.column(align=True)
        c.prop(self, "output_root")
        c.prop(self, "character_name")
        c.prop(self, "source_collection")
        row = c.row(align=True)
        row.operator("hwpba.mark_active_collection", icon='CHECKMARK')
        row.operator("hwpba.clear_source_collection", icon='X')