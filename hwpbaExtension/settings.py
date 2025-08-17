# pyright: reportInvalidTypeForm=false
import bpy

class HWPBA_Settings(bpy.types.PropertyGroup):
    output_root: bpy.props.StringProperty(
        name="Output Folder",
        subtype='DIR_PATH',
        description="Base folder; the add-on will create HWPBA_Output/AssetsToUpload under it",
        default=""
    )
    character_name: bpy.props.StringProperty(
        name="Character Name",
        description="Used as filename prefix for exported FBX parts and TS file",
        default=""
    )
    source_collection: bpy.props.StringProperty(
        name="Source Collection",
        description="Optional: force-export from this collection of mesh parts",
        default=""
    )
