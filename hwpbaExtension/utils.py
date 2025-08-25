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

def hash_file(path: str):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

# ---------------------------------------------------------------------------
# Image gathering + saving (Horizon-friendly naming)
# ---------------------------------------------------------------------------

def gather_images_from_objects(objects):
    """
    Return a list of (image, preferred_base_name) pairs.
    For each *material* seen, choose one representative image:
      - Prefer the TEX_IMAGE node feeding Principled BSDF 'Base Color'
      - Else first TEX_IMAGE in the material node tree
    Filename base is derived from the material name:
      <MaterialName up to first '_'> + '_BR'
      e.g., 'Glass_Transparent' -> 'Glass_BR'
    """
    result = []
    seen_materials = set()

    for obj in objects:
        for slot in getattr(obj, "material_slots", []):
            mat = slot.material
            if not mat or mat.name in seen_materials:
                continue
            seen_materials.add(mat.name)

            img = None
            if mat.use_nodes and mat.node_tree:
                nt = mat.node_tree
                # Prefer image plugged into Principled Base Color
                principled_nodes = [n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED']
                if principled_nodes:
                    pset = set(principled_nodes)
                    for link in nt.links:
                        if link.to_node in pset and getattr(link.to_socket, "name", "") == "Base Color":
                            if getattr(link.from_node, "type", "") == 'TEX_IMAGE' and getattr(link.from_node, "image", None):
                                img = link.from_node.image
                                break
                # Fallback: first image texture node
                if img is None:
                    for node in nt.nodes:
                        if node.type == 'TEX_IMAGE' and node.image:
                            img = node.image
                            break

            if img:
                base_token = mat.name.split("_", 1)[0]
                base = clean(base_token) + "_BR"
                result.append((img, base))

    return result

def save_or_copy_image_to(img, dest_dir: str, existing_by_name: dict, preferred_base: str = None):
    """
    Write a PNG next to FBXs (in 3dModels), deduping by final filename.
    - File is ALWAYS saved as .png (converts if needed).
    - Name is based on preferred_base if provided (e.g., 'MyMaterial_BR'),
      otherwise falls back to a cleaned image name.
    Returns final path or None.
    """
    # Decide final basename
    if preferred_base:
        base = clean(preferred_base)
    else:
        base = clean(os.path.splitext(img.name)[0]) or "Image"

    final_name = base + ".png"
    target = os.path.join(dest_dir, final_name)

    # Dedup
    if final_name in existing_by_name:
        return existing_by_name[final_name]

    src_path = bpy.path.abspath(img.filepath) if img.filepath else ""
    try:
        if src_path and os.path.exists(src_path) and os.path.splitext(src_path)[1].lower() == ".png":
            shutil.copy2(src_path, target)
        else:
            # Force PNG save (works for packed or non-PNG external sources)
            img.save(filepath=target)
    except Exception as e:
        print(f"[HWPBA] Texture write failed for '{img.name}': {e}")
        return None

    existing_by_name[final_name] = target
    return target

# ---------------------------------------------------------------------------

# def write_instructions(root_dir: str, animations_filename: str):
#     """
#     Minimal instructions, matching new structure and Text Asset flow.
#     animations_filename: e.g. 'Goblin_Animations.json'
#     """
#     path = os.path.join(root_dir, "instructions.txt")
#     with open(path, "w", encoding="utf-8") as f:
#         f.writelines([
#             "HWPBA Output\n",
#             "\n",
#             "Upload to Horizon Worlds:\n",
#             "1) Open Horizon Worlds Creator Portal.\n",
#             "2) Upload everything under 'HWPBA_Output/assetsToUpload'.\n",
#             "   - '3dModels' contains all FBX parts and textures.\n",
#             f"   - '{animations_filename}' is a Text Asset; import it into your world.\n",
#         ])
#     return path
