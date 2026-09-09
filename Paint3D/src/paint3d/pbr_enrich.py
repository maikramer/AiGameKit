"""Enriquecimento PBR: normal + AO derivados do albedo baked via Materialize.

O Hunyuan-Paint 2.1 só gera branches ``albedo`` + ``mr`` — o bake não produz
normal, occlusion nem emissive (normal/position maps são condicionamento do
modelo, não output). Este módulo corre o crate **Materialize** (wgpu compute,
segundos por asset) sobre o albedo baked para derivar ``normal`` e ``ao``; o
metallicRoughness do modelo fica — é melhor do que qualquer heurística de
luminância. Emissive não é derivável do albedo (propriedade criativa) e fica
deliberadamente de fora.

Skip gracioso por design: binário ausente, GPU sem adapter wgpu ou falha do
processo → warning e ``None`` — o asset sai albedo+MR como antes, o paint
nunca quebra por causa do enriquecimento.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

_TIMEOUT_S = 600


def pbr_enrich_enabled() -> bool:
    """``PAINT3D_PBR_ENRICH`` — default ON; ``0`` desliga o enriquecimento."""
    return os.environ.get("PAINT3D_PBR_ENRICH", "").strip() != "0"


def resolve_materialize_bin() -> str | None:
    """Resolve o binário: ``MATERIALIZE_BIN`` → checkout monorepo → ``PATH``.

    Mesma convenção de ``aigamekit_shared.env`` para ferramentas ``kind=rust``.
    """
    env_bin = os.environ.get("MATERIALIZE_BIN", "").strip()
    if env_bin and Path(env_bin).is_file():
        return env_bin
    repo_root = Path(__file__).resolve().parents[3]
    for name in ("materialize", "materialize-cli"):
        cand = repo_root / "Materialize" / "target" / "release" / name
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return shutil.which("materialize") or shutil.which("materialize-cli")


def enrich_maps_from_albedo(
    albedo: np.ndarray,
    *,
    preset: str | None = None,
    logger: Any = None,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Deriva ``(normal, ao)`` do albedo baked com o Materialize.

    Args:
        albedo: ``(H, W, 3)`` uint8 RGB (o atlas já dilatado nas seams).
        preset: Preset do Materialize (``default``, ``stone``, ``wood``, …);
            env ``PAINT3D_PBR_PRESET`` sobrepõe; default ``default``.
        logger: Logger opcional (avisos de skip).

    Returns:
        ``(normal, ao)`` como ``(H, W, 3)`` uint8 RGB na resolução do albedo,
        ou ``None`` quando desligado/indisponível — o caller segue com
        albedo+MR.
    """
    from PIL import Image as PILImage

    if not pbr_enrich_enabled():
        return None
    preset = os.environ.get("PAINT3D_PBR_PRESET", "").strip() or (preset or "default")
    materialize_bin = resolve_materialize_bin()
    if materialize_bin is None:
        if logger is not None:
            logger.warning(
                "PBR enrich: binário materialize não encontrado "
                "(MATERIALIZE_BIN / Materialize/target/release) — asset sai sem normal/AO"
            )
        return None

    h, w = albedo.shape[:2]
    with tempfile.TemporaryDirectory(prefix="paint3d_pbr_enrich_") as td_raw:
        tdir = Path(td_raw)
        src = tdir / "albedo.png"
        PILImage.fromarray(np.ascontiguousarray(albedo[..., :3]), mode="RGB").save(src)
        cmd = [
            materialize_bin,
            str(src),
            "-o",
            str(tdir),
            "-p",
            preset,
            # Só o que o modelo não gera: metallic/smoothness vêm do branch mr.
            "--only",
            "normal,ao",
            # Atlas de UV não é tileable — sem wrap sampling.
            "--no-seamless",
            # glTF é convenção OpenGL (+Y para cima).
            "--normal-format",
            "opengl",
            "-f",
            "png",
        ]
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                timeout=_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            if logger is not None:
                logger.warning(f"PBR enrich: materialize falhou ({exc}) — asset sai sem normal/AO")
            return None
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace").strip().splitlines()
            tail = stderr[-1] if stderr else f"rc={proc.returncode}"
            if logger is not None:
                logger.warning(f"PBR enrich: materialize rc={proc.returncode} ({tail}) — asset sai sem normal/AO")
            return None

        stem = src.stem
        normal_path = tdir / f"{stem}_normal.png"
        ao_path = tdir / f"{stem}_ao.png"
        if not normal_path.is_file() or not ao_path.is_file():
            if logger is not None:
                logger.warning("PBR enrich: mapas esperados ausentes — asset sai sem normal/AO")
            return None

        def _load_rgb(path: Path) -> np.ndarray:
            arr = np.asarray(PILImage.open(path).convert("RGB"), dtype=np.uint8)
            if arr.shape[:2] != (h, w):
                arr = np.asarray(
                    PILImage.fromarray(arr).resize((w, h), PILImage.Resampling.LANCZOS),
                    dtype=np.uint8,
                )
            return arr

        return _load_rgb(normal_path), _load_rgb(ao_path)
