#!/usr/bin/env python3
"""Migrate VibeGame declarative worlds (index.html ``<Scene>`` or modular .xml) to Viber XML.

Viber (``Viber/``) is the native Bevy engine; its world format uses Bevy naming
(``translation`` / ``euler`` / ``half-size`` / ``base-color``) while VibeGame
worlds use three.js naming (``pos``, radians on composition primitives, ``size``
total extents).  Deterministic, stdlib-only converter:

    python3 Viber/scripts/migrate_from_vibegame.py <input> [--public DIR] [-o OUTDIR]

``<input>``   an ``index.html`` (the ``<Scene>`` block is extracted) or a world
              ``.xml`` (bare fragment or ``<Scene>``-wrapped).
``--public``  root of the modular worlds tree; ``<Include src="/world/x.xml">``
              resolves against it and every included file is migrated too,
              mirrored into OUTDIR.
``-o``        output directory (default ``./viber-world/``).  The input file
              becomes ``world.xml``; included files keep their tree paths
              (``/world/cities/x.xml`` -> ``OUTDIR/world/cities/x.xml``).
              ``<Include>`` srcs keep the leading ``/`` — Viber resolves
              ``/x`` against the root file directory, which OUTDIR mirrors.

Conversion rules (the test suite next to this file is the executable spec):

* Root ``<Scene>`` becomes ``<world>``; ``sky`` becomes ``clear-color``.
* Mapped tags (case-insensitive input, PascalCase output):

    GameObject -> Entity          Group -> Group
    Composition -> Group (bridge) PointLight -> PointLight
    ThirdPersonCamera -> OrbitCamera
    Box -> Cuboid   Sphere -> Sphere   Cylinder -> Cylinder   Plane/Pad -> Plane

* Universal attrs: ``pos`` -> ``translation``; ``scale`` / ``name`` / ``tag``
  kept; ``script`` rewritten ``.ts`` -> ``.lua``; ``euler`` kept (degrees);
  ``rotation``/``rot`` with 3 components -> ``euler`` (degrees), with 4
  components -> ``rotation`` (quaternion, unchanged).
* DROPPED attrs (recorded in the per-file header comment):
  - root ``<Scene>``: ``canvas``, ``resume-audio-on-user-gesture``,
    ``postprocessing`` and every other unknown root attr;
  - every mapped tag: ``layer``, ``id``, ``postprocessing``;
  - PointLight: ``distance``, ``decay``, ``power``;
  - ThirdPersonCamera: everything except ``target`` / ``distance`` / ``height``
    / ``pitch`` (``fov``, ``near``, ``far``, ``mouse-sensitivity``,
    ``follow-lag``, ``turn-lag``, ...);
  - primitives: ``opacity`` (values < 1 lose transparency),
    ``texture-*``, ``normal-map-url``, ``roughness-map-url``;
  - Pad: ``edge-feather``/``feather``, ``corner-radius``, ``edge-noise``;
  - ``place``: the ``at: X Z`` vector becomes ``translation`` (Y=0); the
    remaining keys (``align-to-terrain`` and friends) are dropped.
* three.js -> Bevy unit scales:
    POINT_LIGHT_LM_SCALE = 800      (intensity -> lumens, rounded)
    DIRECTIONAL_LUX_SCALE = 10000   (intensity -> illuminance/lux)
    AMBIENT_BRIGHTNESS_SCALE = 500  (intensity -> brightness)
* Component attr-strings become child elements: ``renderer``/``mesh-renderer``
  -> ``<Cuboid>``/``<Sphere>`` (inherits the entity transform via the Viber
  hierarchy, so the primitive ends up with the same world transform as the
  entity); ``directional-light`` -> ``<DirectionalLight>``;
  ``ambient-light`` -> ``<AmbientLight>``; ``point-light`` fills a
  ``<PointLight>`` element.
* ``<Composition>`` becomes a ``<Group>`` bridge carrying the composition
  transform; primitive children keep LOCAL transforms with ``rotation``
  converted radians -> degrees; non-primitive children follow the normal
  rules.  The composition full flatten (parent rotation applied to child pos)
  is deliberately NOT done here: the engine hierarchy composes it.  When the
  composition rotates AND primitive children have a non-zero ``pos``, a
  warning comment is emitted in place.
* Unmapped tags (GLTFLoader, Terrain, spawners, HUD, ...) pass through
  VERBATIM (attrs and children kept) and no-op in the engine; they are listed
  in the per-file header.  Exception: ``<Include src>`` files are migrated and
  the tree is mirrored so include paths stay valid.
"""

from __future__ import annotations

import argparse
import copy
import math
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# --- three.js -> Bevy unit scales -------------------------------------------
POINT_LIGHT_LM_SCALE = 800
DIRECTIONAL_LUX_SCALE = 10000
AMBIENT_BRIGHTNESS_SCALE = 500

# --- tag maps (keys lowercased) ---------------------------------------------
TAG_MAP = {
    "gameobject": "Entity",
    "group": "Group",
    "composition": "Group",
    "box": "Cuboid",
    "sphere": "Sphere",
    "cylinder": "Cylinder",
    "plane": "Plane",
    "pad": "Plane",
    "pointlight": "PointLight",
    "thirdpersoncamera": "OrbitCamera",
}

# VibeGame composition primitives: rotation attr is in RADIANS, size = extents.
PRIMITIVES = {"box", "sphere", "cylinder", "plane", "pad"}

# Attrs dropped on every mapped tag (no Viber equivalent / engine-level only).
GLOBAL_DROP_ATTRS = {"layer", "id", "postprocessing"}

# Root <Scene> attr that survives (as clear-color); everything else drops.
ROOT_KEEP_ATTRS = {"sky": "clear-color"}

# ThirdPersonCamera attrs that survive; all others drop.
ORBIT_KEEP_ATTRS = {"target", "distance", "height"}

# PointLight attrs dropped (three.js falloff knobs without a Viber equivalent).
POINTLIGHT_DROP_ATTRS = {"distance", "decay", "power"}

# Pad-only attrs dropped (terrain-pad look knobs).
PAD_DROP_ATTRS = {"edge-feather", "feather", "corner-radius", "edge-noise"}

# Primitive material/texture attrs dropped (no transparency / no maps in Viber yet).
PRIMITIVE_TEXTURE_DROP_PREFIXES = ("texture-",)
PRIMITIVE_TEXTURE_DROP_ATTRS = {"normal-map-url", "roughness-map-url"}

_HEADER_NOTE = (
    "Migrated from VibeGame by scripts/migrate_from_vibegame.py; tags não",
    "implementadas passam verbatim e fazem no-op na engine.",
)


# ---------------------------------------------------------------------------
# small vector/quaternion helpers (three.js-compatible, Euler order "XYZ")
# ---------------------------------------------------------------------------
Quat = tuple[float, float, float, float]  # (w, x, y, z)
Vec3 = tuple[float, float, float]


def quat_from_euler_deg(x_deg: float, y_deg: float, z_deg: float) -> Quat:
    """Euler XYZ (degrees, three.js order) -> quaternion (w, x, y, z)."""
    xr, yr, zr = math.radians(x_deg), math.radians(y_deg), math.radians(z_deg)
    c1, s1 = math.cos(xr / 2.0), math.sin(xr / 2.0)
    c2, s2 = math.cos(yr / 2.0), math.sin(yr / 2.0)
    c3, s3 = math.cos(zr / 2.0), math.sin(zr / 2.0)
    return (
        c1 * c2 * c3 - s1 * s2 * s3,
        s1 * c2 * c3 + c1 * s2 * s3,
        c1 * s2 * c3 - s1 * c2 * s3,
        c1 * c2 * s3 + s1 * s2 * c3,
    )


def quat_mul(a: Quat, b: Quat) -> Quat:
    """Hamilton product a * b (apply b first, then a — parent * child)."""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def quat_rotate(q: Quat, v: Vec3) -> Vec3:
    """Rotate vector v by quaternion q (v' = v + 2w(u x v) + 2u x (u x v))."""
    w, x, y, z = q
    tx = 2.0 * (y * v[2] - z * v[1])
    ty = 2.0 * (z * v[0] - x * v[2])
    tz = 2.0 * (x * v[1] - y * v[0])
    return (
        v[0] + w * tx + (y * tz - z * ty),
        v[1] + w * ty + (z * tx - x * tz),
        v[2] + w * tz + (x * ty - y * tx),
    )


def quat_to_euler_deg(q: Quat) -> Vec3:
    """Quaternion (w, x, y, z) -> Euler XYZ degrees (three.js extraction)."""
    w, x, y, z = q
    sin_y = max(-1.0, min(1.0, 2.0 * (x * z + y * w)))
    if abs(sin_y) < 0.9999999:
        xr = math.atan2(2.0 * (w * x - y * z), 1.0 - 2.0 * (x * x + y * y))  # (-m23, m33)
        yr = math.asin(sin_y)
        zr = math.atan2(2.0 * (w * z - x * y), 1.0 - 2.0 * (y * y + z * z))  # (-m12, m11)
    else:  # gimbal lock: pitch at +/-90 degrees
        xr = math.atan2(2.0 * (y * z + w * x), 1.0 - 2.0 * (x * x + z * z))  # (m32, m22)
        yr = math.copysign(math.pi / 2.0, sin_y)
        zr = 0.0
    return (math.degrees(xr), math.degrees(yr), math.degrees(zr))


def quat_is_identity(q: Quat, eps: float = 1e-6) -> bool:
    return abs(q[0] - 1.0) < eps and all(abs(c) < eps for c in q[1:])


# ---------------------------------------------------------------------------
# number / vector formatting
# ---------------------------------------------------------------------------
def fmt_num(value: float, decimals: int = 6) -> str:
    """Format a float without trailing zeros and without float noise."""
    rounded = round(value + 0.0, decimals)  # +0.0 kills -0.0
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.{decimals}f}".rstrip("0").rstrip(".")


def fmt_vec(values: list[float], decimals: int = 6) -> str:
    return " ".join(fmt_num(v, decimals) for v in values)


def parse_floats(value: str) -> list[float]:
    return [float(part) for part in value.replace(",", " ").split()]


def broadcast3(value: str) -> list[float] | None:
    """Parse a size/vec attr: 1 number broadcasts to 3; 3 used as-is."""
    parts = parse_floats(value)
    if len(parts) == 1:
        return [parts[0], parts[0], parts[0]]
    if len(parts) == 3:
        return parts
    return None


def normalize_bool(value: str) -> str:
    """Normalize a boolean-ish attr value to ``true``/``false`` (bare already ran)."""
    low = value.strip().lower()
    if low in ("true", "1", "yes", "on"):
        return "true"
    if low in ("false", "0", "no", "off"):
        return "false"
    return value.strip()


def parse_prop_string(value: str) -> dict[str, str]:
    """Parse a VibeGame component attr string: ``shape: box; size: 2 4 6``."""
    props: dict[str, str] = {}
    for chunk in value.split(";"):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        key, val = chunk.split(":", 1)
        props[key.strip().lower().replace("_", "-")] = val.strip()
    return props


def rewrite_script(value: str) -> str:
    """VibeGame entity scripts are TypeScript; Viber runs Luau."""
    return re.sub(r"\.ts$", ".lua", value.strip())


# ---------------------------------------------------------------------------
# preprocessing: bare boolean attrs (`<Fog enabled>` -> `enabled="true"`)
# ---------------------------------------------------------------------------
def normalize_bare_bools(src: str) -> str:
    """Quote bare boolean attributes so the strict XML parser accepts the input.

    Comment-aware: text inside ``<!-- ... -->`` is copied untouched (world
    comments legitimately mention tags like ``<Include src=``).
    """
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        if src.startswith("<!--", i):
            end = src.find("-->", i + 4)
            end = n if end == -1 else end + 3
            out.append(src[i:end])
            i = end
            continue
        if src[i] != "<" or i + 1 >= n or not src[i + 1].isalpha():
            out.append(src[i])
            i += 1
            continue
        # tag start: copy the name
        j = i + 1
        while j < n and not src[j].isspace() and src[j] not in "/>":
            j += 1
        out.append("<")
        out.append(src[i + 1 : j])
        i = j
        # scan attributes until the tag ends
        while i < n and src[i] != ">":
            ws = i
            while i < n and src[i].isspace():
                i += 1
            out.append(src[ws:i])
            if i >= n or src[i] == ">":
                break
            if src[i] == "/":
                out.append("/")
                i += 1
                continue
            start = i
            while i < n and not src[i].isspace() and src[i] not in "=/>":
                i += 1
            attr = src[start:i]
            while i < n and src[i].isspace():
                i += 1
            if i < n and src[i] == "=":
                out.append(attr + "=")
                i += 1
                while i < n and src[i].isspace():
                    i += 1
                if i < n and src[i] in "\"'":
                    quote = src[i]
                    vstart = i
                    i += 1
                    while i < n and src[i] != quote:
                        i += 1
                    i = min(i + 1, n)
                    out.append(src[vstart:i])
                else:  # unquoted value
                    vstart = i
                    while i < n and not src[i].isspace() and src[i] != ">":
                        i += 1
                    out.append('"' + src[vstart:i] + '"')
            else:  # bare boolean attribute
                out.append(attr + '="true"')
        out.append(">")
        i += 1
    return "".join(out)


def parse_wrapped(content: str) -> ET.Element:
    """Parse (possibly multi-root) world content inside a synthetic wrapper."""
    normalized = normalize_bare_bools(content)
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    return ET.fromstring(f"<__viber_wrap__>{normalized}</__viber_wrap__>", parser)


# ---------------------------------------------------------------------------
# migration context (per output file)
# ---------------------------------------------------------------------------
class MigrationContext:
    """Collects per-file drop/verbatim stats for the output header comment."""

    def __init__(self) -> None:
        self.dropped_attrs: Counter[str] = Counter()
        self.verbatim_tags: Counter[str] = Counter()
        self.notes: list[str] = []

    def drop_attr(self, name: str) -> None:
        self.dropped_attrs[name] += 1

    def note_verbatim(self, tag: str) -> None:
        self.verbatim_tags[tag] += 1

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    def header_comment(self) -> str:
        """Header comment for the output file (XML comments forbid ``--``)."""
        clean = lambda s: s.replace("--", "-")  # noqa: E731  (keep lines tight)
        lines = [_HEADER_NOTE[0], _HEADER_NOTE[1]]
        if self.dropped_attrs:
            names = ", ".join(sorted(self.dropped_attrs))
            lines.append(f"Dropped attrs: {clean(names)}")
        if self.verbatim_tags:
            tags = ", ".join(f"{t} x{n}" for t, n in sorted(self.verbatim_tags.items()))
            lines.append(f"Verbatim (no-op) tags: {clean(tags)}")
        lines.extend(f"Note: {clean(note)}" for note in self.notes)
        return "\n".join(f"  {line}" for line in lines)


# ---------------------------------------------------------------------------
# element conversion
# ---------------------------------------------------------------------------
def is_comment(el: ET.Element) -> bool:
    return not isinstance(el.tag, str)


def attr_value(el: ET.Element, name: str) -> str | None:
    """Case-insensitive attribute lookup."""
    low = name.lower()
    for key, value in el.attrib.items():
        if key.lower() == low:
            return value
    return None


def get_prop(props: dict[str, str], *names: str) -> str | None:
    for name in names:
        if name in props:
            return props[name]
    return None


def map_universal_attr(low: str, value: str, out: ET.Element, ctx: MigrationContext, rot_rad: bool) -> bool:
    """Apply the universal attr renames; True when consumed.

    ``rot_rad``: inside a Composition the 3-component ``rotation`` is radians.
    """
    if low == "pos":
        out.set("translation", value.strip())
        return True
    if low == "scale":
        out.set("scale", value.strip())
        return True
    if low in ("name", "tag"):
        out.set(low, value)
        return True
    if low == "script":
        out.set("script", rewrite_script(value))
        return True
    if low == "euler":
        out.set("euler", value.strip())
        return True
    if low in ("rotation", "rot"):
        parts = value.split()
        if len(parts) == 4:
            out.set("rotation", value.strip())
        elif len(parts) == 3:
            if rot_rad:  # composition children: rotation is in radians
                value = fmt_vec([math.degrees(float(p)) for p in parts], 4)
            out.set("euler", value.strip())
        else:
            ctx.drop_attr(low)
            ctx.note(f"rotation com {len(parts)} componente(s) descartado (esperado 3 ou 4)")
        return True
    if low == "place":
        apply_place_attr(value, out, ctx)
        return True
    if low in GLOBAL_DROP_ATTRS:
        ctx.drop_attr(low)
        return True
    return False


def apply_place_attr(value: str, out: ET.Element, ctx: MigrationContext) -> None:
    """``place="at: X Z; align-to-terrain: 0"`` -> ``translation="X 0 Z"``."""
    props = parse_prop_string(value)
    at = props.get("at")
    if at is None:
        ctx.drop_attr("place")
        ctx.note(f"place sem 'at' descartado: {value!r}")
        return
    if out.get("translation") is not None:
        ctx.drop_attr("place")
        ctx.note("place ignorado: pos/translation já definido no elemento")
        return
    parts = parse_floats(at)
    if len(parts) == 2:
        out.set("translation", fmt_vec([parts[0], 0.0, parts[1]]))
    elif len(parts) == 3:
        out.set("translation", fmt_vec(parts))
    else:
        ctx.drop_attr("place")
        ctx.note(f"place 'at' com {len(parts)} componentes descartado (esperado 2 ou 3)")
        return
    extra = sorted(k for k in props if k != "at")
    for key in extra:
        ctx.drop_attr(f"place:{key}")


def apply_light_prop_string(el: ET.Element, out: ET.Element, ctx: MigrationContext) -> None:
    """Fill a <PointLight> from its ``point-light`` component attr string.

    Element attrs win; the string only fills what the element does not set.
    """
    raw = attr_value(el, "point-light")
    if raw is None:
        return
    props = parse_prop_string(raw)
    if out.get("color") is None and "color" in props:
        out.set("color", props["color"])
    if out.get("intensity") is None and "intensity" in props:
        out.set("intensity", str(round(float(props["intensity"]) * POINT_LIGHT_LM_SCALE)))
    if out.get("shadows") is None:
        shadow = get_prop(props, "cast-shadow", "castshadow")
        if shadow is not None:
            out.set("shadows", normalize_bool(shadow))
    for key in POINTLIGHT_DROP_ATTRS:
        if key in props:
            ctx.drop_attr(f"point-light:{key}")


def convert_pointlight(el: ET.Element, ctx: MigrationContext, rot_rad: bool) -> ET.Element:
    out = ET.Element("PointLight")
    for name, value in el.attrib.items():
        low = name.lower()
        if low == "point-light":
            continue  # handled after the loop
        if low == "color":
            out.set("color", value)
        elif low == "intensity":
            out.set("intensity", str(round(float(value) * POINT_LIGHT_LM_SCALE)))
        elif low in ("cast-shadow", "castshadow"):
            out.set("shadows", normalize_bool(value))
        elif low in POINTLIGHT_DROP_ATTRS:
            ctx.drop_attr(low)
        elif not map_universal_attr(low, value, out, ctx, rot_rad):
            out.set(name, value)  # unknown (e.g. radius) passes verbatim
    apply_light_prop_string(el, out, ctx)
    convert_children_into(el, out, ctx, rot_rad=False)
    return out


def convert_orbit_camera(el: ET.Element, ctx: MigrationContext, rot_rad: bool) -> ET.Element:
    out = ET.Element("OrbitCamera")
    for name, value in el.attrib.items():
        low = name.lower()
        if low == "pitch":  # three.js radians -> Bevy degrees
            out.set("pitch", fmt_num(round(math.degrees(float(value)), 1), 1))
        elif low in ORBIT_KEEP_ATTRS:
            out.set(low, value)
        elif not map_universal_attr(low, value, out, ctx, rot_rad):
            ctx.drop_attr(name)
    convert_children_into(el, out, ctx, rot_rad=False)
    return out


def convert_primitive(el: ET.Element, ctx: MigrationContext, rot_rad: bool) -> ET.Element:
    """Convert a VibeGame primitive (Box/Sphere/Cylinder/Plane/Pad).

    ``size`` holds TOTAL extents (Box/Sphere/Cylinder) with 1-value broadcast;
    ``rotation`` is in radians inside a Composition; ``color`` -> ``base-color``;
    ``metalness`` -> ``metallic``.
    """
    tag = el.tag.lower()
    out = ET.Element(TAG_MAP[tag])
    rot_degs: Vec3 | None = None
    for name, value in el.attrib.items():
        low = name.lower()
        if low == "size":
            continue  # shape-specific, handled below
        if low in ("rotation", "rot"):
            parts = value.split()
            if len(parts) == 3:
                rot_degs = tuple(math.degrees(float(p)) if rot_rad else float(p) for p in parts)
            else:
                ctx.drop_attr(low)
                ctx.note(f"rotação de primitiva com {len(parts)} componente(s) descartada")
        elif low == "pos":
            out.set("translation", value.strip())
        elif low == "color":
            out.set("base-color", value)
        elif low == "metalness":
            out.set("metallic", value)
        elif low == "roughness":
            out.set("roughness", value)
        elif low == "opacity":
            ctx.drop_attr("opacity")
            nums = parse_floats(value)
            if nums and nums[0] < 1:
                ctx.note("opacity < 1 descartado (transparência sem equivalente na Fase 0)")
        elif low == "script":
            out.set("script", rewrite_script(value))
        elif (
            low.startswith(PRIMITIVE_TEXTURE_DROP_PREFIXES)
            or low in PRIMITIVE_TEXTURE_DROP_ATTRS
            or (tag == "pad" and low in PAD_DROP_ATTRS)
        ):
            ctx.drop_attr(low)
        elif not map_universal_attr(low, value, out, ctx, rot_rad):
            out.set(name, value)  # unknown attr passes verbatim

    size_raw = attr_value(el, "size")
    dims = broadcast3(size_raw) if size_raw else None
    if tag == "box":
        if size_raw and dims is None:
            ctx.note(f"Box size com 2 componentes não suportado: {size_raw!r}")
        out.set("half-size", fmt_vec([d / 2.0 for d in (dims or [1.0, 1.0, 1.0])]))
    elif tag == "sphere":
        if size_raw and dims is None:
            ctx.note(f"Sphere size com 2 componentes não suportado: {size_raw!r}")
        out.set("radius", fmt_num(dims[0] / 2.0 if dims else 0.5))
    elif tag == "cylinder":
        if size_raw and dims is None:
            ctx.note(f"Cylinder size com 2 componentes não suportado: {size_raw!r}")
        top, bottom, height = dims if dims else (0.5, 0.5, 1.0)
        out.set("radius", fmt_num(top))
        out.set("half-height", fmt_num(height / 2.0))
        if abs(top - bottom) > 1e-9:
            out.insert(
                0,
                ET.Comment(f" Cylinder sem taper na Viber: raio do topo usado (base era {fmt_num(bottom)}). "),
            )
            ctx.note(
                f"Cylinder taperado (top {fmt_num(top)} / base {fmt_num(bottom)}) simplificado para o raio do topo"
            )
    elif tag in ("plane", "pad"):
        flat = parse_floats(size_raw) if size_raw else [1.0, 1.0]
        if len(flat) == 1:
            flat = [flat[0], flat[0]]
        if len(flat) < 2:
            ctx.note(f"{tag} size inválido: {size_raw!r}")
            flat = [1.0, 1.0]
        out.set("half-size", fmt_vec([flat[0] / 2.0, flat[1] / 2.0]))
        if tag == "plane":
            # VibeGame Plane faces +Z (XY); Viber Plane lies on XZ facing +Y.
            tilt = quat_from_euler_deg(-90.0, 0.0, 0.0)
            if rot_degs is not None:
                tilt = quat_mul(tilt, quat_from_euler_deg(*rot_degs))
            rot_degs = quat_to_euler_deg(tilt)

    if rot_degs is not None and any(abs(d) > 1e-9 for d in rot_degs):
        out.set("euler", fmt_vec(list(rot_degs), 4))
    for child in el:  # keep comments that document the primitive
        if is_comment(child):
            out.append(copy.deepcopy(child))
    return out


def convert_renderer_child(props: dict[str, str], ctx: MigrationContext) -> ET.Element | None:
    """``renderer``/``mesh-renderer`` attr string -> primitive child element.

    The child carries no transform: as a child of the Entity it inherits the
    entity transform, ending with the same world transform as the entity.
    """
    shape = (get_prop(props, "shape") or "box").strip().lower()
    size_raw = get_prop(props, "size") or "1 1 1"
    color = get_prop(props, "color")
    roughness = get_prop(props, "roughness")
    metalness = get_prop(props, "metalness")
    out: ET.Element
    if shape == "sphere":
        dims = broadcast3(size_raw) or [0.5, 0.5, 0.5]
        out = ET.Element("Sphere", {"radius": fmt_num(dims[0] / 2.0)})
    elif shape == "box":
        dims = broadcast3(size_raw)
        if dims is None:
            ctx.note(f"renderer size com 2 componentes não suportado: {size_raw!r}")
            dims = [1.0, 1.0, 1.0]
        out = ET.Element("Cuboid", {"half-size": fmt_vec([d / 2.0 for d in dims])})
    else:
        ctx.note(f"renderer shape desconhecido descartado: {shape!r}")
        return None
    if color is not None:
        out.set("base-color", color)
    if roughness is not None:
        out.set("roughness", roughness)
    if metalness is not None:
        out.set("metallic", metalness)
    return out


def apply_component_attrs(el: ET.Element, out: ET.Element, ctx: MigrationContext) -> None:
    """Emit child elements for component attr-strings on an entity container."""
    for name in ("renderer", "mesh-renderer"):
        raw = attr_value(el, name)
        if raw is None:
            continue
        child = convert_renderer_child(parse_prop_string(raw), ctx)
        if child is not None:
            out.append(child)
    raw = attr_value(el, "directional-light")
    if raw is not None:
        props = parse_prop_string(raw)
        light = ET.Element("DirectionalLight")
        color = props.get("color")
        if color is not None:
            light.set("color", color)
        if "intensity" in props:
            light.set("illuminance", str(round(float(props["intensity"]) * DIRECTIONAL_LUX_SCALE)))
        if "direction" in props:
            light.set("direction", props["direction"])
        for key in sorted(props):
            if key not in ("color", "intensity", "direction"):
                ctx.drop_attr(f"directional-light:{key}")
        out.append(light)
    raw = attr_value(el, "ambient-light")
    if raw is not None:
        props = parse_prop_string(raw)
        ambient = ET.Element("AmbientLight")
        color = get_prop(props, "sky-color", "skycolor")
        if color is not None:
            ambient.set("color", color)
        if "intensity" in props:
            ambient.set("brightness", str(round(float(props["intensity"]) * AMBIENT_BRIGHTNESS_SCALE)))
        ground = get_prop(props, "ground-color", "groundcolor")
        if ground is not None:
            ctx.drop_attr("ambient-light:ground-color")
        for key in sorted(props):
            if key not in ("sky-color", "skycolor", "ground-color", "groundcolor", "intensity"):
                ctx.drop_attr(f"ambient-light:{key}")
        out.append(ambient)


def convert_entity_or_group(
    el: ET.Element, out_tag: str, ctx: MigrationContext, rot_rad: bool, walk_children: bool = True
) -> ET.Element:
    out = ET.Element(out_tag)
    for name, value in el.attrib.items():
        low = name.lower()
        if low in ("renderer", "mesh-renderer", "directional-light", "ambient-light"):
            continue  # handled after the loop
        if not map_universal_attr(low, value, out, ctx, rot_rad):
            out.set(name, value)  # unknown entity attrs pass verbatim (engine warns)
    apply_component_attrs(el, out, ctx)
    if walk_children:
        convert_children_into(el, out, ctx, rot_rad=False)
    return out


def convert_composition(el: ET.Element, ctx: MigrationContext) -> ET.Element:
    """``<Composition>`` -> ``<Group>`` bridge.

    The Group carries the composition transform (rotation in DEGREES here);
    primitive children keep LOCAL transforms with rotation radians -> degrees;
    non-primitive children follow the normal rules.  The engine hierarchy
    composes parent rotation x child pos, so no manual flatten is done — when
    both are non-zero we emit a warning comment for manual review.
    """
    out = convert_entity_or_group(el, "Group", ctx, rot_rad=False, walk_children=False)
    comp_rot: Quat = (1.0, 0.0, 0.0, 0.0)
    rot_attr = out.get("rotation")
    euler_attr = out.get("euler")
    if rot_attr:
        parts = parse_floats(rot_attr)
        if len(parts) == 4:
            comp_rot = (parts[0], parts[1], parts[2], parts[3])
    elif euler_attr:
        parts = parse_floats(euler_attr)
        if len(parts) == 3:
            comp_rot = quat_from_euler_deg(*parts)
    if not quat_is_identity(comp_rot) and _composition_has_offset_primitives(el):
        out.insert(
            0,
            ET.Comment(
                " Composition com rotation != 0 e primitivas com pos != 0: as pos locais dos"
                " filhos são pré-rotação; a hierarquia do Group deve compor como no VibeGame,"
                " validar o resultado. "
            ),
        )
    for child in el:  # only direct PRIMITIVE children read radians (task rule)
        child_rot_rad = not is_comment(child) and child.tag.lower() in PRIMITIVES
        out.append(convert_element(child, ctx, rot_rad=child_rot_rad))
    return out


def _composition_has_offset_primitives(el: ET.Element) -> bool:
    for child in el:
        if is_comment(child):
            continue
        if child.tag.lower() in PRIMITIVES:
            pos = attr_value(child, "pos")
            if pos:
                try:
                    if any(abs(v) > 1e-9 for v in parse_floats(pos)):
                        return True
                except ValueError:
                    return True
    return False


def convert_include(el: ET.Element, ctx: MigrationContext) -> ET.Element:
    """Keep <Include> as-is; the file tree is mirrored so srcs stay valid."""
    out = ET.Element("Include")
    for name, value in el.attrib.items():
        out.set(name, value)
    for child in el:
        out.append(copy.deepcopy(child))
    return out


def convert_verbatim(el: ET.Element, ctx: MigrationContext) -> ET.Element:
    """Unknown tags pass through untouched (engine no-ops and warns)."""
    ctx.note_verbatim(el.tag)
    # Deep copy keeps attrs, children and comments; includes inside verbatim
    # subtrees keep working because the output tree mirrors the public tree.
    return copy.deepcopy(el)


def convert_element(el: ET.Element, ctx: MigrationContext, rot_rad: bool = False) -> ET.Element:
    """Dispatch one input element to its converted output element."""
    if is_comment(el):
        return copy.deepcopy(el)
    tag = el.tag.lower()
    if tag == "include":
        return convert_include(el, ctx)
    if tag == "composition":
        return convert_composition(el, ctx)
    if tag == "pointlight":
        return convert_pointlight(el, ctx, rot_rad)
    if tag == "thirdpersoncamera":
        return convert_orbit_camera(el, ctx, rot_rad)
    if tag in ("gameobject", "group"):
        return convert_entity_or_group(el, TAG_MAP[tag], ctx, rot_rad)
    if tag in PRIMITIVES:
        return convert_primitive(el, ctx, rot_rad)
    return convert_verbatim(el, ctx)


def convert_children_into(el: ET.Element, out: ET.Element, ctx: MigrationContext, rot_rad: bool) -> None:
    for child in el:
        out.append(convert_element(child, ctx, rot_rad))


# ---------------------------------------------------------------------------
# input extraction
# ---------------------------------------------------------------------------
_SCENE_RE = re.compile(r"<scene\b[^>]*>(.*)</scene\s*>", re.IGNORECASE | re.DOTALL)
_ATTR_RE = re.compile(r"([A-Za-z_][\w:.-]*)\s*=\s*\"([^\"]*)\"")
_DECL_RE = re.compile(r"<\?xml[^>]*\?>|<!DOCTYPE[^>\[]*(\[[^\]]*\])?>(\s*)", re.IGNORECASE)


def extract_input(text: str) -> tuple[dict[str, str] | None, str]:
    """Return (root attrs, inner content): <Scene> block if present, else whole file."""
    match = _SCENE_RE.search(text)
    if match:
        open_tag = match.group(0)[: match.group(0).index(">") + 1]
        attrs = {name: value for name, value in _ATTR_RE.findall(open_tag)}
        return attrs, match.group(1)
    return None, _DECL_RE.sub("", text, count=1)


def build_world(content: str, root_attrs: dict[str, str] | None, ctx: MigrationContext) -> ET.Element:
    """Convert world content (Scene inner or whole fragment) to a <world> element."""
    wrapper = parse_wrapped(content)
    children = list(wrapper)
    attrs = dict(root_attrs or {})
    elements = [c for c in children if isinstance(c.tag, str)]
    if not root_attrs and len(elements) == 1 and elements[0].tag.lower() in ("scene", "world"):
        scene = elements[0]
        attrs = dict(scene.attrib)
        idx = children.index(scene)
        children = children[:idx] + list(scene) + children[idx + 1 :]

    world = ET.Element("world")
    for name, value in attrs.items():
        low = name.lower()
        if low in ROOT_KEEP_ATTRS:
            world.set(ROOT_KEEP_ATTRS[low], value)
        else:
            ctx.drop_attr(name)  # canvas, resume-audio-on-user-gesture, postprocessing, ...
    for child in children:
        world.append(convert_element(child, ctx))
    return world


# ---------------------------------------------------------------------------
# tree migration (root file + mirrored include tree)
# ---------------------------------------------------------------------------
@dataclass
class MigratedFile:
    source: Path
    output: Path
    context: MigrationContext


@dataclass
class MigrationReport:
    files: list[MigratedFile] = field(default_factory=list)
    unresolved_includes: list[str] = field(default_factory=list)


def _resolve_include(src: str, src_file: Path, public_dir: Path | None) -> Path | None:
    """Resolve an include src against the INPUT tree."""
    if src.startswith("/"):
        if public_dir is None:
            return None
        return public_dir / src.lstrip("/")
    return src_file.parent / src


def migrate_tree(input_path: Path, public_dir: Path | None, out_dir: Path) -> MigrationReport:
    """Migrate the input file plus (recursively) every included world file."""
    report = MigrationReport()
    visited: set[Path] = set()

    def run(src_file: Path, out_file: Path) -> None:
        resolved_src = src_file.resolve()
        if resolved_src in visited:
            return
        visited.add(resolved_src)
        text = src_file.read_text(encoding="utf-8")
        root_attrs, content = extract_input(text)
        ctx = MigrationContext()
        world = build_world(content, root_attrs, ctx)

        # Migrate included files (mirrored tree keeps include srcs valid).
        for inc in list(world.iter("Include")):
            src = (inc.get("src") or "").strip()
            if not src:
                ctx.note("Include sem src descartado")
                continue
            target = _resolve_include(src, src_file, public_dir)
            if target is None:
                note = f"include não migrado (sem --public): {src}"
                ctx.note(note)
                report.unresolved_includes.append(note)
                continue
            target = Path(target)
            # Mirrored tree: absolute srcs resolve from OUTDIR root, relative
            # srcs from the including file's mirrored directory.
            target_out = out_dir / src.lstrip("/") if src.startswith("/") else Path(out_file.parent / src)
            if not target.is_file():
                note = f"include não encontrado: {src}"
                ctx.note(note)
                report.unresolved_includes.append(note)
                continue
            run(target, target_out)

        ET.indent(world, space="  ")
        body = ET.tostring(world, encoding="unicode")
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(f"<!--\n{ctx.header_comment()}\n-->\n{body}\n", encoding="utf-8")
        report.files.append(MigratedFile(source=src_file, output=out_file, context=ctx))

    run(input_path, out_dir / "world.xml")
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def print_report(report: MigrationReport, out_dir: Path) -> None:
    total_dropped = sum(sum(f.context.dropped_attrs.values()) for f in report.files)
    total_verbatim = sum(sum(f.context.verbatim_tags.values()) for f in report.files)
    print(f"Migrados {len(report.files)} ficheiro(s) para {out_dir}/:")
    for item in report.files:
        dropped = sum(item.context.dropped_attrs.values())
        verbatim = sum(item.context.verbatim_tags.values())
        print(f"  {item.output} (attrs descartados: {dropped}, tags verbatim: {verbatim})")
    print(f"Total: {total_dropped} attrs descartados, {total_verbatim} tags verbatim (no-op na engine).")
    for note in report.unresolved_includes:
        print(f"Aviso: {note}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Converte mundos VibeGame (index.html <Scene> ou .xml modular) para o formato Viber.",
    )
    parser.add_argument("input", help="index.html ou .xml de mundo VibeGame")
    parser.add_argument("--public", default=None, help="raiz dos mundos modulares (resolve <Include src='/...'>)")
    parser.add_argument("-o", "--out", default="viber-world", help="diretório de saída (default: ./viber-world/)")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.is_file():
        parser.error(f"input não encontrado: {args.input}")
    public_dir = Path(args.public).resolve() if args.public else None
    if args.public and not public_dir.is_dir():
        parser.error(f"--public não é um diretório: {args.public}")
    out_dir = Path(args.out)

    report = migrate_tree(input_path, public_dir, out_dir)
    print_report(report, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
