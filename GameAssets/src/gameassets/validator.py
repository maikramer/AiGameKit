"""Validação de assets gerados pelo batch."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .manifest import ManifestRow
from .profile import GameProfile


@dataclass
class ValidationResult:
    """Resultado da validação de uma linha do manifest."""

    row_id: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def _resolve_mesh_path(profile: GameProfile, manifest_dir: Path, row: ManifestRow) -> Path:
    """Resolve o path esperado do mesh GLB (inline para evitar import circular com cli.py)."""
    root = Path(profile.output_dir)
    rid = row.id
    if profile.path_layout == "flat":
        parts = rid.split("/")
        if len(parts) >= 2:
            dir_ = root / Path(*parts[:-1])
            base = parts[-1]
        else:
            dir_ = root
            base = rid
        mesh = dir_ / f"{base}.glb"
    else:
        mesh = root / profile.meshes_subdir / f"{rid}.glb"
    if mesh.is_absolute():
        return mesh.resolve()
    return (manifest_dir / mesh).resolve()


def _resolve_audio_path(profile: GameProfile, manifest_dir: Path, row: ManifestRow) -> Path:
    """Resolve o path esperado do áudio (inline para evitar import circular com cli.py)."""
    ts = profile.text2sound
    ext = (ts.audio_format or "wav").lower().strip().lstrip(".") if ts else "wav"
    root = Path(profile.output_dir)
    rid = row.id
    if profile.path_layout == "flat":
        parts = rid.split("/")
        if len(parts) >= 2:
            dir_ = root / Path(*parts[:-1])
            base = parts[-1]
        else:
            dir_ = root
            base = rid
        audio = dir_ / f"{base}.{ext}"
    else:
        audio = root / profile.audio_subdir / f"{rid}.{ext}"
    if audio.is_absolute():
        return audio.resolve()
    return (manifest_dir / audio).resolve()


def validate_row(
    row: ManifestRow,
    profile: GameProfile,
    manifest_dir: Path,
    *,
    max_poly_count: int = 100_000,
    max_file_size_mb: float = 50.0,
) -> ValidationResult:
    """Valida uma única linha do manifest.

    Verifica:
    - Existência e integridade do GLB (se generate_3d)
    - Poly count (número de faces)
    - Presença de textura no GLB
    - Tamanho do ficheiro
    - LODs presentes (se generate_lod)
    - Collision mesh presente (se generate_collision)
    - Áudio presente (se generate_audio)

    Args:
        row: Linha do manifest.
        profile: Perfil de jogo.
        manifest_dir: Diretório do manifest.
        max_poly_count: Máximo de faces permitido.
        max_file_size_mb: Tamanho máximo em MB.

    Returns:
        ValidationResult com erros e avisos.
    """
    result = ValidationResult(row_id=row.id)

    if row.generate_3d:
        mesh_path = _resolve_mesh_path(profile, manifest_dir, row)
        if not mesh_path.is_file():
            result.errors.append(f"GLB não encontrado: {mesh_path}")
        else:
            # File size check
            size_mb = mesh_path.stat().st_size / (1024 * 1024)
            if size_mb > max_file_size_mb:
                result.errors.append(f"GLB demasiado grande: {size_mb:.1f} MB (máx {max_file_size_mb} MB)")
            if size_mb > max_file_size_mb * 0.8:
                result.warnings.append(f"GLB grande: {size_mb:.1f} MB")

            # Poly count + texture check (requires trimesh)
            try:
                import trimesh

                m = trimesh.load(str(mesh_path), force="mesh")
                if isinstance(m, trimesh.Scene):
                    meshes = list(m.geometry.values())
                    if not meshes:
                        result.errors.append("GLB sem geometria")
                    else:
                        m = meshes[0]
                n_faces = len(m.faces)
                if n_faces > max_poly_count:
                    result.errors.append(f"Poly count elevado: {n_faces} faces (máx {max_poly_count})")
                elif n_faces > max_poly_count * 0.8:
                    result.warnings.append(f"Poly count alto: {n_faces} faces")

                # Texture check
                if hasattr(m, "visual") and hasattr(m.visual, "material"):
                    mat = m.visual.material
                    if hasattr(mat, "image") and mat.image is None:
                        result.warnings.append("GLB sem textura")
            except ImportError:
                result.warnings.append("trimesh não instalado — validação de geometria ignorada")
            except Exception as e:
                result.warnings.append(f"Não foi possível validar geometria: {e}")

        # LOD check
        if row.generate_lod:
            basename = row.id.replace("/", "_")
            for level in range(3):
                lod_path = mesh_path.parent / f"{basename}_lod{level}.glb"
                if not lod_path.is_file():
                    result.errors.append(f"LOD{level} não encontrado: {lod_path}")

        # Collision check
        if row.generate_collision:
            coll_path = mesh_path.parent / f"{mesh_path.stem}_collision.glb"
            if not coll_path.is_file():
                result.errors.append(f"Collision mesh não encontrada: {coll_path}")

    if row.generate_audio:
        audio_path = _resolve_audio_path(profile, manifest_dir, row)
        if not audio_path.is_file():
            result.errors.append(f"Áudio não encontrado: {audio_path}")
        else:
            size_mb = audio_path.stat().st_size / (1024 * 1024)
            if size_mb > 20:
                result.warnings.append(f"Áudio grande: {size_mb:.1f} MB")

    return result
