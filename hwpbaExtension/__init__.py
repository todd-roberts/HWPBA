# hw_parts_based_animation/__init__.py
import bpy
from bpy.utils import register_class, unregister_class
from bpy.props import StringProperty, PointerProperty

# ---------- Scene Settings ----------
class HWPBA_SceneSettings(bpy.types.PropertyGroup):
    character_name: StringProperty(
        name="Character Name",
        description="Name used for exported filenames (e.g., Goblin_Animations.json)",
        default="",
    )
    output_root: StringProperty(
        name="Output Folder",
        description="Folder where HWPBA_Output will be created (choose a parent folder)",
        subtype='DIR_PATH',
        default="",
    )
    source_collection: StringProperty(
        name="Source Collection",
        description="Optional: collection containing your separated mesh parts",
        default="",
    )

# ---------- Local imports (ops) ----------
from .ops_common import (
    HWPBA_OT_SelectActiveCollection,
    HWPBA_OT_ClearSourceCollection,
    HWPBA_OT_OpenOutput,
    HWPBA_OT_AutoRigFromParts,
    HWPBA_OT_CleanPreexistingArmature,
)
from .export_parts import HWPBA_OT_CreateFiles

_PANEL_CLASS = None  # set at runtime in register()

_CORE_CLASSES = (
    HWPBA_SceneSettings,
    HWPBA_OT_SelectActiveCollection,
    HWPBA_OT_ClearSourceCollection,
    HWPBA_OT_OpenOutput,
    HWPBA_OT_AutoRigFromParts,
    HWPBA_OT_CleanPreexistingArmature,
    HWPBA_OT_CreateFiles,
)

# ---------- Safe (idempotent) register helpers ----------
def _safe_register(cls):
    try:
        register_class(cls)
    except RuntimeError as e:
        # Typical Blender message:
        # "register_class(...): already registered as a subclass 'ClassName'"
        if "already registered" in str(e):
            try:
                unregister_class(cls)
            except Exception:
                pass
            register_class(cls)
        else:
            raise

def _safe_unregister(cls):
    try:
        unregister_class(cls)
    except Exception:
        pass

def register():
    for cls in _CORE_CLASSES:
        _safe_register(cls)
    bpy.types.Scene.hwpba_settings = PointerProperty(type=HWPBA_SceneSettings)

    # Defer panel import/registration
    global _PANEL_CLASS
    try:
        from .panel import HWPBA_PT_Main as _Panel
        _safe_register(_Panel)
        _PANEL_CLASS = _Panel
    except Exception as e:
        print("[HWPBA] Failed to import/register panel:", e)

def unregister():
    global _PANEL_CLASS
    if _PANEL_CLASS is not None:
        _safe_unregister(_PANEL_CLASS)
        _PANEL_CLASS = None

    try:
        del bpy.types.Scene.hwpba_settings
    except Exception:
        pass

    for cls in reversed(_CORE_CLASSES):
        _safe_unregister(cls)
