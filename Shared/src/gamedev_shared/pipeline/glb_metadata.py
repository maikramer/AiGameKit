"""Extract metadata from GLB files for pipeline→engine handoff.

NOTE: This module is not currently used by any downstream package.
Candidate for removal in a future cleanup pass.
"""

from __future__ import annotations

import json
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast


@dataclass
class GlbMetadata:
    """Metadata extracted from a GLB file."""

    file: str
    animations: list[str] = field(default_factory=list)
    bounds: dict[str, list[float]] = field(default_factory=dict)
    vertex_count: int = 0
    triangle_count: int = 0
    file_size_bytes: int = 0
    source_pipeline: str | None = None
    pbr_textures: list[str] = field(default_factory=list)


def _read_glb_json_chunk(glb_path: Path) -> dict[str, Any] | None:
    """Parse the JSON chunk from a GLB file (GLB spec: first chunk after 12-byte header)."""
    with open(glb_path, "rb") as f:
        magic = f.read(4)
        if magic != b"glTF":
            return None
        f.read(8)  # skip version + total length

        chunk_length = struct.unpack("<I", f.read(4))[0]
        chunk_type = f.read(4)
        if chunk_type != b"JSON":
            return None
        json_data = f.read(chunk_length).decode("utf-8", errors="replace")

    try:
        return cast(dict[str, Any], json.loads(json_data))
    except json.JSONDecodeError:
        return None


def _extract_animations(gltf_json: dict) -> list[str]:
    """Extract animation clip names from glTF JSON."""
    animations = []
    for anim in gltf_json.get("animations", []):
        name = anim.get("name", "")
        if name:
            animations.append(name)
    return animations


def _extract_pbr_textures(gltf_json: dict) -> list[str]:
    """Extract texture names referenced in PBR materials."""
    textures = set()
    for mat in gltf_json.get("materials", []):
        pbr = mat.get("pbrMetallicRoughness", {})
        for key in ("baseColorTexture", "metallicRoughnessTexture"):
            tex_idx = pbr.get(key, {}).get("index")
            if tex_idx is not None:
                all_textures = gltf_json.get("textures", [])
                if tex_idx < len(all_textures):
                    tex_name = all_textures[tex_idx].get("name", f"texture_{tex_idx}")
                    textures.add(tex_name)
        # Normal map
        normal = mat.get("normalTexture", {})
        tex_idx = normal.get("index")
        if tex_idx is not None:
            all_textures = gltf_json.get("textures", [])
            if tex_idx < len(all_textures):
                tex_name = all_textures[tex_idx].get("name", f"texture_{tex_idx}")
                textures.add(tex_name)
    return sorted(textures)


def _extract_geometry_info(gltf_json: dict) -> tuple[int, int]:
    """Extract vertex and triangle counts from glTF accessors."""
    vertex_count = 0
    triangle_count = 0

    accessors = gltf_json.get("accessors", [])

    for mesh in gltf_json.get("meshes", []):
        for prim in mesh.get("primitives", []):
            # Position accessor → vertex count
            pos_idx = prim.get("attributes", {}).get("POSITION")
            if pos_idx is not None and pos_idx < len(accessors):
                vertex_count += accessors[pos_idx].get("count", 0)

            # Index accessor → triangle count
            idx_accessor_idx = prim.get("indices")
            if idx_accessor_idx is not None and idx_accessor_idx < len(accessors):
                idx_count = accessors[idx_accessor_idx].get("count", 0)
                triangle_count += idx_count // 3
            else:
                # No indices: triangles = vertices / 3
                pos_count = 0
                if pos_idx is not None and pos_idx < len(accessors):
                    pos_count = accessors[pos_idx].get("count", 0)
                triangle_count += pos_count // 3

    return vertex_count, triangle_count


def extract_glb_metadata(glb_path: Path, source_pipeline: str | None = None) -> GlbMetadata:
    """Extract metadata from a GLB file."""
    gltf_json = _read_glb_json_chunk(glb_path)

    metadata = GlbMetadata(
        file=str(glb_path),
        file_size_bytes=glb_path.stat().st_size,
        source_pipeline=source_pipeline,
    )

    if gltf_json:
        metadata.animations = _extract_animations(gltf_json)
        metadata.pbr_textures = _extract_pbr_textures(gltf_json)
        v, t = _extract_geometry_info(gltf_json)
        metadata.vertex_count = v
        metadata.triangle_count = t

    # Try to get bounds via trimesh (optional dependency)
    try:
        import trimesh

        mesh = trimesh.load(str(glb_path), force="mesh")
        if hasattr(mesh, "bounds"):
            bounds = mesh.bounds
            metadata.bounds = {
                "min": [float(bounds[0][0]), float(bounds[0][1]), float(bounds[0][2])],
                "max": [float(bounds[1][0]), float(bounds[1][1]), float(bounds[1][2])],
            }
    except Exception:
        pass  # trimesh not available or load failed

    return metadata


def write_metadata_sidecar(glb_path: Path, metadata: GlbMetadata) -> Path:
    """Write .metadata.json next to the GLB file."""
    output = glb_path.with_suffix(glb_path.suffix + ".metadata.json")
    output.write_text(
        json.dumps(asdict(metadata), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return output


def main() -> None:
    """CLI: python -m gamedev_shared.pipeline.glb_metadata <file.glb> [--pipeline ...]"""
    import argparse

    parser = argparse.ArgumentParser(description="Extract GLB metadata for pipeline handoff")
    parser.add_argument("file", type=Path, help="GLB file to analyze")
    parser.add_argument("--pipeline", type=str, default=None, help="Source pipeline description")
    args = parser.parse_args()

    metadata = extract_glb_metadata(args.file, source_pipeline=args.pipeline)
    output = write_metadata_sidecar(args.file, metadata)
    print(f"✅ Metadata written to {output}")
    print(f"   Vertices: {metadata.vertex_count}, Triangles: {metadata.triangle_count}")
    print(f"   Animations: {metadata.animations}")
    print(f"   PBR textures: {metadata.pbr_textures}")
    if metadata.bounds:
        print(f"   Bounds: {metadata.bounds}")
