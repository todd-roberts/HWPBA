# pyright: reportInvalidTypeForm=false
import bpy, os, re, hashlib, shutil

def settings(context):
    return getattr(context.scene, "hwpba_settings", None)

def clean(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9_\-]+', '_', s).strip('_')

def collect_meshes_from_armature(arm):
    out = []
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        if o.parent == arm:
            out.append(o); continue
        a = o.find_armature()
        if a == arm:
            out.append(o); continue
        for m in o.modifiers:
            if m.type == 'ARMATURE' and m.object == arm:
                out.append(o); break
    return out

def find_parts(context):
    """
    Order:
      1) Explicit Source Collection (panel)
      2) Active armature's meshes
      3) Active mesh's armature meshes
      4) Collection literally named 'HW_Parts'
    Returns (objects, source_label)
    """
    s = settings(context)
    if s and s.source_collection:
        coll = bpy.data.collections.get(s.source_collection)
        if coll:
            objs = [o for o in coll.objects if o.type == "MESH"]
            if objs:
                return objs, coll.name

    act = context.active_object
    if act and act.type == "ARMATURE":
        objs = collect_meshes_from_armature(act)
        if objs:
            return objs, act.name

    if act and act.type == "MESH":
        arm = act.find_armature() or (act.parent if act.parent and act.parent.type == "ARMATURE" else None)
        if arm:
            objs = collect_meshes_from_armature(arm)
            if objs:
                return objs, arm.name

    for coll in bpy.data.collections:
        if coll.name.lower() == "hw_parts":
            objs = [o for o in coll.objects if o.type == "MESH"]
            if objs:
                return objs, coll.name

    return [], ""

def ensure_dirs(base_abs: str):
    """
    Create:
      root/HWPBA_Output
        assetsToUpload/
          3dModels/
        tempFiles/
    Return (root, assets_dir, models_dir, temp_dir)
    """
    base = os.path.abspath(base_abs) if base_abs else ""
    if not base:
        raise RuntimeError("No Output Folder selected")
    last = os.path.basename(os.path.normpath(base))
    root = base if last.lower() == "hwpba_output" else os.path.join(base, "HWPBA_Output")

    assets = os.path.join(root, "assetsToUpload")
    models = os.path.join(assets, "3dModels")
    temp = os.path.join(root, "tempFiles")

    os.makedirs(models, exist_ok=True)
    os.makedirs(temp, exist_ok=True)
    return root, assets, models, temp

def _empty_dir(path: str):
    """Delete all files/subdirs inside path, but keep the folder itself."""
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
        return
    for name in os.listdir(path):
        p = os.path.join(path, name)
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
        except Exception:
            pass

def clean_known_outputs(assets_dir: str, models_dir: str, temp_dir: str):
    """
    Safe pre-export cleanup:
      - empty tempFiles/
      - empty assetsToUpload/3dModels/
      - remove legacy loose FBX/texture files from assetsToUpload/ root
        (preserve any *_animations.json and legacy 'animations.json')
    """
    _empty_dir(temp_dir)
    _empty_dir(models_dir)

    # Sweep legacy strays in assets root (keep *_animations.json and subfolders)
    exts = ('.fbx', '.png', '.jpg', '.jpeg', '.webp', '.tga', '.tif', '.tiff', '.bmp', '.gif')
    try:
        for name in list(os.listdir(assets_dir)):
            p = os.path.join(assets_dir, name)
            if os.path.isdir(p):
                continue
            lower = name.lower()
            if lower == "animations.json" or lower.endswith("_animations.json"):
                continue
            if lower.endswith(exts):
                try:
                    os.remove(p)
                except Exception:
                    pass
    except FileNotFoundError:
        os.makedirs(assets_dir, exist_ok=True)

def validate(context):
    """
    Validate for UI/operators.
    Returns (ok, msg, count, source, base_abs). Never raises.
    """
    s = settings(context)
    if not s:
        return False, "HW PBA not initialized (scene settings missing). Save/reload file or re-enable extension.", 0, "", ""
    base_abs = bpy.path.abspath(s.output_root) if s.output_root else ""
    try:
        objs, source = find_parts(context)
    except Exception as e:
        return False, f"Error while scanning scene: {e}", 0, "", base_abs
    if not base_abs:
        return False, "Choose Output Folder in the panel", 0, source, base_abs
    if not objs:
        return False, "No parts found. Select an armature, or set Source Collection.", 0, source, base_abs
    return True, "", len(objs), source, base_abs

def gather_images_from_objects(objects):
    images, seen = [], set()
    for obj in objects:
        for slot in getattr(obj, "material_slots", []):
            mat = slot.material
            if not mat or not mat.use_nodes or not mat.node_tree:
                continue
            for node in mat.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image and node.image.name not in seen:
                    seen.add(node.image.name)
                    images.append(node.image)
    return images

def hash_file(path: str):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def save_or_copy_image_to(img, dest_dir: str, existing_by_name: dict):
    """
    Write an image next to FBXs (in 3dModels), deduping by filename (basename+ext).
    Prefer copying from original filepath; otherwise save packed buffer.
    Returns final path or None.
    """
    base = clean(os.path.splitext(img.name)[0]) or "Image"
    src_path = bpy.path.abspath(img.filepath) if img.filepath else ""
    ext = os.path.splitext(src_path or "")[1].lower() or ".png"
    final_name = base + ext
    target = os.path.join(dest_dir, final_name)

    if final_name in existing_by_name:
        return existing_by_name[final_name]

    if src_path and os.path.exists(src_path):
        import shutil as _sh
        _sh.copy2(src_path, target)
        existing_by_name[final_name] = target
        return target

    try:
        img.save(filepath=target)
        existing_by_name[final_name] = target
        return target
    except Exception:
        return None

def write_instructions(root_dir: str, animations_filename: str):
    """
    Minimal instructions, matching new structure and Text Asset flow.
    animations_filename: e.g. 'Goblin_Animations.json'
    """
    path = os.path.join(root_dir, "instructions.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines([
            "HWPBA Output\n",
            "\n",
            "Upload to Horizon Worlds:\n",
            "1) Open Horizon Worlds Creator Portal.\n",
            "2) Upload everything under 'HWPBA_Output/assetsToUpload'.\n",
            "   - '3dModels' contains all FBX parts and textures.\n",
            f"   - '{animations_filename}' is a Text Asset; import it into your world.\n",
        ])
    return path
