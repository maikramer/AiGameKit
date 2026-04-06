"""Validate pipeline outputs before handoff to engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ValidationResult:
    """Result of validating a pipeline output."""

    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.valid = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def merge(self, other: ValidationResult) -> None:
        """Merge another result into this one."""
        for e in other.errors:
            self.add_error(e)
        for w in other.warnings:
            self.add_warning(w)


def validate_glb(path: Path) -> ValidationResult:
    """Validate a GLB file: valid header, geometry, reasonable size."""
    result = ValidationResult()

    if not path.is_file():
        result.add_error(f"File not found: {path}")
        return result

    # Check size
    size = path.stat().st_size
    if size == 0:
        result.add_error(f"Empty file: {path}")
        return result
    if size > 500 * 1024 * 1024:  # 500 MB
        result.add_warning(f"Very large file ({size / 1024 / 1024:.1f} MB): {path}")

    # Check GLB magic bytes
    with open(path, "rb") as f:
        magic = f.read(4)
    if magic != b"glTF":
        result.add_error(f"Invalid GLB magic bytes: {path}")
        return result

    # Try trimesh
    try:
        import trimesh

        mesh = trimesh.load(str(path), force="mesh")
        if hasattr(mesh, "vertices") and len(mesh.vertices) == 0:
            result.add_error(f"No vertices in mesh: {path}")
        if hasattr(mesh, "faces") and len(mesh.faces) == 0:
            result.add_warning(f"No faces in mesh: {path}")
    except ImportError:
        result.add_warning("trimesh not installed — skipping geometry validation")
    except Exception as e:
        result.add_error(f"Failed to load GLB: {e}")

    return result


def validate_texture(path: Path) -> ValidationResult:
    """Validate a texture: valid image, reasonable dimensions."""
    result = ValidationResult()

    if not path.is_file():
        result.add_error(f"File not found: {path}")
        return result

    try:
        from PIL import Image

        img = Image.open(path)
        w, h = img.size

        if w == 0 or h == 0:
            result.add_error(f"Zero-dimension texture: {path}")
        if w > 8192 or h > 8192:
            result.add_warning(f"Very large texture ({w}x{h}): {path}")
        if (w & (w - 1)) != 0 or (h & (h - 1)) != 0:
            result.add_warning(f"Non-power-of-2 dimensions ({w}x{h}): {path}")
    except ImportError:
        result.add_warning("Pillow not installed — skipping texture validation")
    except Exception as e:
        result.add_error(f"Failed to load texture: {e}")

    return result


def validate_manifest(manifest_path: Path, assets_dir: Path | None = None) -> ValidationResult:
    """Validate a gameassets_manifest.json: structure + referenced files."""
    result = ValidationResult()

    if not manifest_path.is_file():
        result.add_error(f"Manifest not found: {manifest_path}")
        return result

    import json

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        result.add_error(f"Invalid JSON: {e}")
        return result

    if not isinstance(data, dict):
        result.add_error("Manifest root must be an object")
        return result

    if data.get("version", 0) < 1:
        result.add_error("Invalid manifest version")

    assets = data.get("assets", {})
    if not isinstance(assets, dict):
        result.add_error("'assets' must be an object")
        return result

    # Check referenced files exist
    if assets_dir:
        for name, asset in assets.items():
            if not isinstance(asset, dict):
                result.add_error(f"Asset '{name}' must be an object")
                continue
            for field in ("model", "audio"):
                url = asset.get(field)
                if url and isinstance(url, str):
                    file_path = assets_dir / url.lstrip("/")
                    if not file_path.is_file():
                        result.add_warning(f"Referenced file missing: {url} (asset '{name}')")

    result.add_warning(f"Manifest has {len(assets)} assets")
    return result


def validate_directory(output_dir: Path) -> list[ValidationResult]:
    """Validate all pipeline outputs in a directory."""
    results: list[ValidationResult] = []

    for glb in sorted(output_dir.rglob("*.glb")):
        results.append(validate_glb(glb))

    for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        for tex in sorted(output_dir.rglob(ext)):
            results.append(validate_texture(tex))

    for manifest in sorted(output_dir.rglob("*manifest*.json")):
        results.append(validate_manifest(manifest, output_dir))

    return results


def main() -> None:
    """CLI: python -m gamedev_shared.pipeline.validate <path> [--type glb|texture|manifest|dir]"""
    import argparse

    parser = argparse.ArgumentParser(description="Validate pipeline outputs")
    parser.add_argument("path", type=Path, help="File or directory to validate")
    parser.add_argument(
        "--type",
        choices=["glb", "texture", "manifest", "dir"],
        default=None,
        help="Validation type (auto-detected if omitted)",
    )
    args = parser.parse_args()

    vtype = args.type
    if vtype is None:
        if args.path.is_dir():
            vtype = "dir"
        elif args.path.suffix == ".glb":
            vtype = "glb"
        elif args.path.suffix in (".png", ".jpg", ".jpeg", ".webp"):
            vtype = "texture"
        else:
            vtype = "manifest"

    if vtype == "glb":
        result = validate_glb(args.path)
    elif vtype == "texture":
        result = validate_texture(args.path)
    elif vtype == "manifest":
        result = validate_manifest(args.path)
    else:
        results = validate_directory(args.path)
        total = len(results)
        passed = sum(1 for r in results if r.valid)
        failed = total - passed
        print(f"✅ {passed}/{total} passed, {failed} failed")
        for r in results:
            if not r.valid:
                print(f"  ❌ {r.errors}")
        return

    if result.valid:
        print(f"✅ Valid: {args.path}")
        if result.warnings:
            for w in result.warnings:
                print(f"  ⚠️  {w}")
    else:
        print(f"❌ Invalid: {args.path}")
        for e in result.errors:
            print(f"  🔴 {e}")
