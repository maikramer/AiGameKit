"""Stage 8 — transferência de skin (wrapper → ``gamedev_shared.skin_transfer``).

API pública estável para o CLI e testes. Lógica canónica vive em Shared:
weights (KDTree) + skeleton + animations.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from gamedev_shared.skin_transfer import SkinTransferResult, transfer_skin_to_mesh, transfer_skin_to_targets

log = logging.getLogger(__name__)

# Back-compat alias (tests + callers).
TransferResult = SkinTransferResult


def _decompress_glb(src: Path, dst: Path) -> bool:
    """Descompressa GLB via ``gltf-transform copy`` (local p/ patches de teste)."""
    if shutil.which("npx") is None:
        return False
    try:
        r = subprocess.run(
            ["npx", "--yes", "@gltf-transform/cli", "copy", str(src), str(dst)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning("transfer-weights decompress falhou: %s", exc)
        return False
    if r.returncode != 0:
        log.warning("transfer-weights decompress retornou %d: %s", r.returncode, (r.stderr or "")[-300:])
        return False
    return dst.is_file()


@contextmanager
def _bpy_readable_glb(path: Path):
    """Yields a path to ``path`` that bpy's GLTF importer can read."""
    src = Path(path).resolve()
    try:
        from gamedev_shared.bpy_mesh import gltf_import_supports_meshopt

        if gltf_import_supports_meshopt():
            yield src
            return
    except ImportError:
        pass

    with tempfile.NamedTemporaryFile(suffix=".glb", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        if _decompress_glb(src, tmp_path):
            yield tmp_path
        else:
            yield src
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _transfer_one(
    source_glb: Path,
    target_glb: Path,
    output_glb: Path,
    *,
    animation_source: Path | None = None,
) -> SkinTransferResult:
    """Compat: delega em ``transfer_skin_to_mesh`` (Shared)."""
    try:
        return transfer_skin_to_mesh(
            source_glb,
            target_glb,
            output_glb,
            animation_source=animation_source,
        )
    except ValueError as exc:
        msg = str(exc)
        if "no armature" in msg.lower() or "sem armature" in msg.lower():
            raise ValueError(f"Source GLB sem armature: {source_glb}") from exc
        raise


def transfer_weights(
    source_glb: Path,
    targets: list[Path],
    *,
    output_dir: Path | None = None,
    output_suffix: str = "_rigged",
    targets_out: list[Path] | None = None,
    apply_finish: bool = True,
    animation_source: Path | None = None,
) -> list[SkinTransferResult]:
    """Transfere skin (+ skeleton + anims) do source para cada target."""
    source_glb = Path(source_glb)
    if targets_out and len(targets_out) != len(targets):
        raise ValueError("targets_out deve ter o mesmo tamanho de targets")

    results: list[SkinTransferResult] = []
    for i, t in enumerate(targets):
        t_path = Path(t)
        if targets_out is not None:
            out = Path(targets_out[i])
        else:
            base = output_dir if output_dir is not None else t_path.parent
            out = Path(base) / f"{t_path.stem}{output_suffix}{t_path.suffix}"
        log.info("transfer-weights: %s → %s", t_path.name, out.name)
        try:
            results.append(
                _transfer_one(
                    source_glb,
                    t_path,
                    out,
                    animation_source=animation_source,
                )
            )
        except Exception as exc:
            log.error("transfer-weights falhou para %s: %s", t_path, exc)
            raise

    if apply_finish:
        try:
            from text3d.utils.gltf_finish import gltf_transform_finish
        except ImportError:
            log.warning("transfer-weights: gltf_finish indisponível — outputs sem KTX2/meshopt")
        else:
            for r in results:
                try:
                    gltf_transform_finish(r.target_out, r.target_out)
                except Exception as exc:
                    log.warning("transfer-weights finish falhou em %s: %s", r.target_out, exc)
    return results


# Re-export Shared batch helper for callers that want it directly.
__all__ = [
    "TransferResult",
    "SkinTransferResult",
    "transfer_weights",
    "transfer_skin_to_targets",
    "_transfer_one",
    "_decompress_glb",
    "_bpy_readable_glb",
]
