# pyright: reportInvalidTypeForm=false
import json, os, struct
from datetime import datetime

_COMPONENT_SIZE = {5126: 4}  # FLOAT
_NUM_COMPONENTS = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}

def _read_accessor_arrays(gltf, buffers_bytes, accessor_index):
    acc = gltf["accessors"][accessor_index]
    comp_type = acc["componentType"]
    type_str  = acc["type"]
    count     = acc["count"]
    if comp_type != 5126 or type_str not in _NUM_COMPONENTS:
        return []

    num_comps = _NUM_COMPONENTS[type_str]
    bv = gltf["bufferViews"][acc["bufferView"]]
    buf_index = bv["buffer"]
    buf = buffers_bytes[buf_index]

    byte_offset = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    byte_stride = bv.get("byteStride", 0)

    elem_size = _COMPONENT_SIZE[comp_type] * num_comps
    out = []
    if byte_stride and byte_stride != elem_size:
        ptr = byte_offset
        for _ in range(count):
            if type_str == "SCALAR":
                (v,) = struct.unpack_from("<f", buf, ptr); out.append(v)
            elif type_str == "VEC3":
                out.append(struct.unpack_from("<fff", buf, ptr))
            elif type_str == "VEC4":
                out.append(struct.unpack_from("<ffff", buf, ptr))
            ptr += byte_stride
    else:
        arr = memoryview(buf)[byte_offset: byte_offset + count * elem_size]
        if type_str == "SCALAR":
            out = list(struct.unpack("<" + "f"*count, arr))
        elif type_str == "VEC3":
            out = [struct.unpack_from("<fff", arr, i*12) for i in range(count)]
        elif type_str == "VEC4":
            out = [struct.unpack_from("<ffff", arr, i*16) for i in range(count)]
    return out

def _load_buffers_bytes(gltf_dir, gltf):
    buffers_bytes = []
    for buf in gltf.get("buffers", []):
        uri = buf.get("uri", "")
        path = os.path.join(gltf_dir, uri)
        with open(path, "rb") as f:
            buffers_bytes.append(f.read())
    return buffers_bytes

# ---- Axis conversions (GLTF -> Horizon) ------------------------------------
# Anim translations:    (x, y, z) -> (-x,  y,  z)
# Anim quaternions:     (x, y, z, w) -> ( x, -y, -z, w)
def _to_hw_anim_vec3(v):
    return [-float(v[0]), float(v[1]), float(v[2])]

def _to_hw_anim_quat(q):
    return [float(q[0]), -float(q[1]), -float(q[2]), float(q[3])]

def convert_gltf_to_json(
    gltf_path: str,
    json_out_path: str,
    initial_positions: dict,
    name_prefix: str = "",
    **_ignored_kwargs,
):
    """
    Write <Name>_Animations.json with Horizon-native data:
      - animations.translations pre-mapped to (-x, y, z)
      - animations.rotations  pre-mapped to (x, -y, -z, w)
      - initialPositions      already provided as Horizon placement (see export_parts.py)
    """
    gltf_dir = os.path.dirname(gltf_path)
    with open(gltf_path, "r", encoding="utf-8") as f:
        gltf = json.load(f)

    buffers_bytes = _load_buffers_bytes(gltf_dir, gltf)
    nodes = gltf.get("nodes", [])
    animations = gltf.get("animations", [])

    result_animations = {}

    for ai, anim in enumerate(animations):
        name = anim.get("name") or f"Animation{ai}"
        channels = anim.get("channels", [])
        samplers = anim.get("samplers", [])

        shared_times = []
        rotations = {}
        positions = {}

        # Longest time array across channels
        for ch in channels:
            sidx = ch.get("sampler")
            if sidx is None:
                continue
            s = samplers[sidx]
            in_idx = s.get("input")
            if in_idx is None:
                continue
            times = _read_accessor_arrays(gltf, buffers_bytes, in_idx)
            if isinstance(times, list) and len(times) > len(shared_times):
                shared_times = [float(t) for t in times]

        # Per-node tracks (convert to Horizon-native here)
        for ch in channels:
            sidx = ch.get("sampler")
            tgt = ch.get("target", {})
            node_idx = tgt.get("node")
            path = tgt.get("path")   # "rotation" | "translation"

            if sidx is None or node_idx is None or path not in ("rotation", "translation"):
                continue

            s = samplers[sidx]
            out_idx = s.get("output")
            if out_idx is None:
                continue

            node_name = nodes[node_idx].get("name") or f"node_{node_idx}"
            values = _read_accessor_arrays(gltf, buffers_bytes, out_idx)

            if path == "rotation":
                rotations[node_name] = [_to_hw_anim_quat(v) for v in values]
            elif path == "translation":
                positions[node_name] = [_to_hw_anim_vec3(v) for v in values]

        result_animations[name] = {
            "times": shared_times,
            "rotations": rotations,
            "positions": positions,
        }

    wrapper = {
        "animations": result_animations,
        "initialPositions": initial_positions,  # already Horizon-native (see exporter)
        "namePrefix": name_prefix or "",
        "meta": {
            "source": os.path.basename(gltf_path),
            "generated": datetime.now().isoformat(timespec='seconds'),
        },
    }

    with open(json_out_path, "w", encoding="utf-8") as f:
        json.dump(wrapper, f, separators=(",", ":"), ensure_ascii=False)

    return True, f"Wrote {os.path.basename(json_out_path)}"
