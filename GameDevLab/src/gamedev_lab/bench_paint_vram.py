"""Bancada Paint3D: sweet spot de quantização com VRAM."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from gamedev_lab.paths import gamedev_repo_root


def _ensure_paths() -> None:
    root = gamedev_repo_root()
    shared = root / "Shared" / "src"
    paint = root / "Paint3D" / "src"
    text3d = root / "Text3D" / "src"
    for p in (shared, paint, text3d):
        sp = str(p)
        if sp not in sys.path:
            sys.path.insert(0, sp)


def run_paint_quantization_bench(
    *,
    image_path: Path | None,
    target_vram_mb: float,
    output_json: Path,
) -> int:
    _ensure_paths()

    try:
        import torch

        from gamedev_shared.bpy_mesh import clear_scene
        from gamedev_shared.quantization import format_quantization_info, get_quantization_config, is_sdnq_available
        from gamedev_shared.vram_monitor import find_quantization_sweet_spot
        from paint3d.painter import apply_hunyuan_paint
    except ImportError as e:
        print(
            f'Dependências em falta. Instale o extra: pip install -e ".[bench]" (Paint3D no monorepo). Erro: {e}',
            file=sys.stderr,
        )
        return 1

    if not torch.cuda.is_available():
        print("CUDA não disponível.", file=sys.stderr)
        return 1

    if image_path is None:
        image_path = (
            gamedev_repo_root()
            / "GameAssets"
            / "examples"
            / "batch_realista_colorido"
            / "images"
            / "boa_mesa"
            / "tigela_ceramica.png"
        )
    if not image_path.is_file():
        print(f"Imagem não encontrada: {image_path}", file=sys.stderr)
        return 1

    def test_paint_quantization(mode: str) -> object:
        print(f"  Carregando Paint3D com quantização: {mode}")

        import bpy

        clear_scene()
        bpy.ops.mesh.primitive_cube_add(size=1.0)
        mesh_objects = [o for o in bpy.context.scene.objects if o.type == "MESH"]
        quant_config = get_quantization_config(mode)
        if quant_config:
            print(f"    Config: {format_quantization_info(quant_config)}")
        return apply_hunyuan_paint(
            mesh=mesh_objects,
            image=str(image_path),
            quantization_mode=mode,
            max_num_view=2,
            view_resolution=256,
            use_tiny_vae=True,
            enable_vae_tiling=True,
            vae_tile_size=128,
            verbose=True,
        )

    sdnq_ok = is_sdnq_available()
    print(f"SDNQ disponível: {sdnq_ok}")

    modes_to_test: list[str] = []
    if sdnq_ok:
        modes_to_test.extend(["sdnq-uint8", "sdnq-int8", "sdnq-int4"])
    modes_to_test.extend(
        ["quanto-int8", "quanto-int4", "int8", "int4"],
    )

    print(f"Modos a testar: {modes_to_test}")
    print(f"Target VRAM: {target_vram_mb:.0f} MB")

    results = find_quantization_sweet_spot(
        test_load_model_fn=test_paint_quantization,
        quant_modes=modes_to_test,
        target_vram_mb=target_vram_mb,
    )

    serializable: dict[str, object] = {}
    for mode, stats in results.items():
        if stats:
            serializable[mode] = {
                "peak_allocated_mb": stats.peak_allocated_mb,
                "peak_reserved_mb": stats.peak_reserved_mb,
                "min_free_mb": stats.min_free_mb,
                "avg_allocated_mb": stats.avg_allocated_mb,
                "num_snapshots": len(stats.snapshots),
            }
        else:
            serializable[mode] = None

    output_json.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    print(f"Resultados salvos em: {output_json}")
    return 0
