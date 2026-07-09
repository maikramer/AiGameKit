"""Bancada full batch GameAssets: varre configs de quantização."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from gamedev_shared.subprocess_utils import resolve_binary
from gamedev_shared.vram_monitor import VRAMMonitor


@dataclass
class BatchBenchConfig:
    name: str
    paint_quantization: str
    paint_max_views: int
    paint_view_resolution: int
    paint_tiny_vae: bool
    paint_vae_tiling: bool
    paint_vae_tile_size: int
    part3d_quantization: str
    part3d_steps: int
    part3d_octree: int
    text3d_phased: bool = True


BATCH_TEST_CONFIGS: list[BatchBenchConfig] = [
    BatchBenchConfig(
        name="sdnq-uint8-4views-512",
        paint_quantization="sdnq-uint8",
        paint_max_views=4,
        paint_view_resolution=512,
        paint_tiny_vae=True,
        paint_vae_tiling=True,
        paint_vae_tile_size=128,
        part3d_quantization="sdnq-uint8",
        part3d_steps=50,
        part3d_octree=256,
    ),
    BatchBenchConfig(
        name="quanto-int8-4views-512",
        paint_quantization="quanto-int8",
        paint_max_views=4,
        paint_view_resolution=512,
        paint_tiny_vae=True,
        paint_vae_tiling=True,
        paint_vae_tile_size=128,
        part3d_quantization="quanto-int8",
        part3d_steps=50,
        part3d_octree=256,
    ),
    BatchBenchConfig(
        name="sdnq-int4-6views-512",
        paint_quantization="sdnq-int4",
        paint_max_views=6,
        paint_view_resolution=512,
        paint_tiny_vae=True,
        paint_vae_tiling=True,
        paint_vae_tile_size=128,
        part3d_quantization="sdnq-int4",
        part3d_steps=50,
        part3d_octree=256,
    ),
]


def generate_game_yaml(config: BatchBenchConfig, output_path: Path) -> None:
    yaml_content = f"""# Configuração de teste: {config.name}
# Gerado por gamedev-lab bench batch

title: "Test Batch - {config.name}"
genre: "fantasia contemporânea com materiais credíveis"
tone: "cores saturadas tipo jewel, luz de estúdio quente"

style_preset: vibrant_realistic

negative_keywords:
  - "pedestal"
  - "ground plane"
  - "base slab"
  - "floating"

output_dir: .
path_layout: split
images_subdir: images
meshes_subdir: meshes
image_ext: png

image_source: text2d

seed_base: 20260330

text2d:
  width: 512
  height: 512

text3d:
  preset: fast
  texture: true
  phased_batch: {str(config.text3d_phased).lower()}
  gpu_kill_others: true
  allow_shared_gpu: false
  mesh_smooth: 1
  paint_max_views: {config.paint_max_views}
  paint_view_resolution: {config.paint_view_resolution}
  paint_quantization: {config.paint_quantization}
  paint_tiny_vae: {str(config.paint_tiny_vae).lower()}
  paint_torch_compile: false
  paint_vae_tiling: {str(config.paint_vae_tiling).lower()}
  paint_vae_tile_size: {config.paint_vae_tile_size}

rigging3d:
  output_suffix: "_rigged"

part3d:
  steps: {config.part3d_steps}
  octree_resolution: {config.part3d_octree}
  num_chunks: 10000
  segment_only: true
  no_cpu_offload: false
  quantization: {config.part3d_quantization}
  torch_compile: false
  no_attention_slicing: false
  parts_suffix: "_parts"
  segmented_suffix: "_segmented"
"""
    output_path.write_text(yaml_content, encoding="utf-8")


def _gameassets_bin() -> str:
    return resolve_binary("GAMEASSETS_BIN", "gameassets")


def run_batch_test(
    config: BatchBenchConfig,
    base_dir: Path,
    manifest_path: Path,
    *,
    dry_run: bool = False,
    timeout_sec: float = 1800.0,
) -> dict[str, Any]:
    print(f"\n{'=' * 70}")
    print(f"TESTE: {config.name}")
    print(f"{'=' * 70}")

    test_dir = base_dir / f"test_{config.name}"
    test_dir.mkdir(exist_ok=True)

    yaml_path = test_dir / "game.yaml"
    generate_game_yaml(config, yaml_path)

    if not manifest_path.is_file():
        print(f"Manifest não encontrado: {manifest_path}", file=sys.stderr)
        return {"status": "error", "error": "manifest_missing", "config": asdict(config)}

    manifest_dst = test_dir / "manifest.csv"
    manifest_dst.write_text(manifest_path.read_text(encoding="utf-8"), encoding="utf-8")

    if dry_run:
        print(f"  [DRY RUN] Config em: {yaml_path}")
        return {"status": "dry_run", "config": asdict(config)}

    ga = _gameassets_bin()
    cmd = [
        ga,
        "batch",
        "--profile",
        str(yaml_path),
        "--manifest",
        str(manifest_dst),
        "--with-3d",
        "--with-parts",
        "--with-rig",
        "--skip-audio",
        "--skip-text2d",
        "--log",
        str(test_dir / "batch_log.jsonl"),
        "--profile-tools",
        "--profile-log",
        str(test_dir / "profile.jsonl"),
    ]

    env = os.environ.copy()
    env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True,max_split_size_mb:64"

    print(f"  Executando: {' '.join(cmd)} (cwd={test_dir})")

    start_time = time.time()
    vram_monitor = VRAMMonitor(interval_sec=1.0)

    try:
        vram_monitor.start()
        result = subprocess.run(
            cmd,
            cwd=str(test_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        vram_stats = vram_monitor.stop()
        elapsed = time.time() - start_time

        meshes_dir = test_dir / "meshes"
        generated_meshes = list(meshes_dir.glob("**/*.glb")) if meshes_dir.exists() else []

        metrics: dict[str, Any] = {
            "status": "success" if result.returncode == 0 else "failed",
            "exit_code": result.returncode,
            "elapsed_seconds": elapsed,
            "vram_peak_mb": vram_stats.peak_allocated_mb if vram_stats else None,
            "vram_min_free_mb": vram_stats.min_free_mb if vram_stats else None,
            "generated_meshes": len(generated_meshes),
            "config": asdict(config),
        }
        if result.returncode != 0:
            metrics["stderr"] = result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr

        print(f"  Tempo: {elapsed:.1f}s")
        if metrics.get("vram_peak_mb"):
            print(f"  VRAM pico: {metrics['vram_peak_mb']:.0f} MB")
        print(f"  Meshes: {metrics['generated_meshes']}")
        print(f"  {'SUCESSO' if result.returncode == 0 else 'FALHOU'}")
        return metrics

    except subprocess.TimeoutExpired:
        vram_monitor.stop()
        return {"status": "timeout", "config": asdict(config)}
    except Exception as e:
        vram_monitor.stop()
        return {"status": "error", "error": str(e), "config": asdict(config)}


def analyze_results(results: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 70)
    print("ANÁLISE DOS RESULTADOS")
    print("=" * 70)

    successes = [r for r in results if r.get("status") == "success"]
    failures = [r for r in results if r.get("status") != "success"]

    print(f"\nSucessos: {len(successes)}/{len(results)}")
    if failures:
        print(f"Falhas: {len(failures)}/{len(results)}")
        for f in failures:
            c = f.get("config") or {}
            print(f"   - {c.get('name', '?')}: {f.get('status')}")

    if not successes:
        print("\nNenhum teste teve sucesso.")
        return

    by_time = sorted(successes, key=lambda x: x.get("elapsed_seconds", float("inf")))
    by_vram = sorted(
        [s for s in successes if s.get("vram_peak_mb")],
        key=lambda x: x.get("vram_peak_mb", float("inf")),
    )

    print("\nTOP 3 MAIS RÁPIDOS:")
    for i, r in enumerate(by_time[:3], 1):
        c = r["config"]
        print(f"   {i}. {c['name']}: {r['elapsed_seconds']:.1f}s")

    print("\nTOP 3 MENOR VRAM:")
    for i, r in enumerate(by_vram[:3], 1):
        c = r["config"]
        vram = r.get("vram_peak_mb", 0)
        print(f"   {i}. {c['name']}: {vram:.0f} MB pico")


def run_batch_bench_cli(
    mode: str,
    config_name: str | None,
    output_dir: Path,
    project_dir: Path,
    manifest: Path,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    configs = BATCH_TEST_CONFIGS

    if mode == "test":
        if not config_name:
            print("--config é obrigatório para --mode test", file=sys.stderr)
            return 1
        cfg = next((c for c in configs if c.name == config_name), None)
        if not cfg:
            print(f"Config '{config_name}' não encontrada.", file=sys.stderr)
            return 1
        result = run_batch_test(cfg, output_dir, manifest, dry_run=False)
        print(json.dumps(result, indent=2))
        return 0 if result.get("status") == "success" else 1

    if mode == "dry-run":
        print("DRY-RUN: gerando configs...")
        for c in configs:
            run_batch_test(c, output_dir, manifest, dry_run=True)
        print(f"Configs geradas sob: {output_dir}")
        return 0

    results: list[dict[str, Any]] = []
    for c in configs:
        results.append(run_batch_test(c, output_dir, manifest, dry_run=False))
        partial = output_dir / "results_partial.json"
        partial.write_text(json.dumps(results, indent=2), encoding="utf-8")

    analyze_results(results)
    (output_dir / "results_final.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0 if all(r.get("status") == "success" for r in results) else 1
