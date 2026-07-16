#!/usr/bin/env python3
"""Bench kernel opts Text2D/Text2Icon/Texture2D/Skymap2D/Text3D/Paint3D/Part3D.

Uso::

    python docs/scripts/bench_kernel_opts.py --tool text2d
    python docs/scripts/bench_kernel_opts.py --tool text2icon
    python docs/scripts/bench_kernel_opts.py --tool texture2d
    python docs/scripts/bench_kernel_opts.py --tool skymap2d
    python docs/scripts/bench_kernel_opts.py --tool text3d --image path.png
    python docs/scripts/bench_kernel_opts.py --tool paint3d --mesh path.glb --image path.png
    python docs/scripts/bench_kernel_opts.py --tool part3d --mesh path.glb
    python docs/scripts/bench_kernel_opts.py --tool all

Text2Icon/Skymap2D: subprocesso por config (VRAM presa no mesmo PID).
"""

from __future__ import annotations

import argparse
import contextlib
import gc
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
MD_PATH = REPO / "docs" / "KERNEL_OPTS_BENCH.md"
OUT_DIR = REPO / "docs" / "bench_kernel_opts"
PROMPT = "a red wooden crate, stylized game prop, white background, centered"
SEED = 42


@dataclass
class RunResult:
    tool: str
    config: str
    load_s: float
    cold_s: float
    hot_s: float
    notes: str = ""


def _sync_cuda() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
    except Exception:
        pass


def _clear_gpu() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            # ipc_collect ajuda a libertar handles entre configs no mesmo processo.
            with contextlib.suppress(Exception):
                torch.cuda.ipc_collect()
            gc.collect()
            torch.cuda.empty_cache()
    except Exception:
        pass


def _append_md_row(row: RunResult) -> None:
    text = MD_PATH.read_text(encoding="utf-8")
    start = "<!-- BENCH_TABLE_START -->"
    end = "<!-- BENCH_TABLE_END -->"
    if start not in text or end not in text:
        raise SystemExit(f"Marcadores em falta em {MD_PATH}")
    before, rest = text.split(start, 1)
    table, after = rest.split(end, 1)
    cleaned = [ln for ln in table.strip().splitlines() if "(pendente)" not in ln]
    n = 1 + sum(1 for ln in cleaned if re.match(r"^\|\s*\d+\s*\|", ln))
    cold = f"{row.cold_s:.1f}" if row.cold_s == row.cold_s else "FAIL"
    hot = f"{row.hot_s:.1f}" if row.hot_s == row.hot_s else "FAIL"
    notes = re.sub(r"\s+", " ", row.notes).strip()
    if len(notes) > 120:
        notes = notes[:117] + "..."
    new_line = f"| {n} | {row.tool} | `{row.config}` | {row.load_s:.1f} | {cold} | {hot} | {notes} |"
    if not any(re.match(r"^\|\s*\d+\s*\|", ln) for ln in cleaned):
        body = "\n".join(
            [
                "| # | Tool | Config | Load (s) | Cold (s) | Hot (s) | Notas |",
                "|---|------|--------|----------|----------|---------|-------|",
                new_line,
            ]
        )
    else:
        body = "\n".join(cleaned).rstrip() + "\n" + new_line
    MD_PATH.write_text(before + start + "\n\n" + body + "\n\n" + end + after, encoding="utf-8")
    print(f"  → anotado em {MD_PATH.name}: {new_line}")


def _run_text2d_config(
    name: str,
    kwargs: dict[str, Any],
    *,
    width: int,
    height: int,
    steps: int,
) -> RunResult:
    from text2d.generator import KleinFluxGenerator

    print(f"\n=== Text2D [{name}] ===")
    gen = KleinFluxGenerator(
        verbose=True,
        memory_efficient=True,
        torch_compile=kwargs.get("torch_compile", False),
        torch_compile_mode=kwargs.get("torch_compile_mode", "default"),
        step_cache=kwargs.get("step_cache", "off"),
        channels_last=kwargs.get("channels_last", False),
    )
    t0 = time.perf_counter()
    gen.warmup()
    _sync_cuda()
    load_s = time.perf_counter() - t0

    times: dict[str, float] = {}
    for tag in ("cold", "hot"):
        t1 = time.perf_counter()
        img, _meta = gen.generate(
            PROMPT,
            width=width,
            height=height,
            num_inference_steps=steps,
            guidance_scale=1.0,
            seed=SEED,
        )
        _sync_cuda()
        times[tag] = time.perf_counter() - t1
        out = OUT_DIR / f"text2d_{name}_{tag}.png"
        gen.save_image(img, out)
        print(f"  {tag}: {times[tag]:.1f}s → {out}")

    plan = getattr(gen, "_plan", None)
    notes = plan.summary() if plan is not None and hasattr(plan, "summary") else "ok"
    gen.unload()
    del gen
    _clear_gpu()
    return RunResult("text2d", name, load_s, times["cold"], times["hot"], notes)


def bench_text2d(configs: list[tuple[str, dict]], *, width: int, height: int, steps: int) -> list[RunResult]:
    sys.path.insert(0, str(REPO / "Shared" / "src"))
    sys.path.insert(0, str(REPO / "Text2D" / "src"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return [_run_text2d_config(name, kwargs, width=width, height=height, steps=steps) for name, kwargs in configs]


ICON_PROMPT = "red wooden crate icon, stylized game prop, white background, centered"


def _run_text2icon_config(
    name: str,
    kwargs: dict[str, Any],
    *,
    width: int,
    height: int,
    steps: int,
) -> RunResult:
    from text2icon.generator import SanaIconGenerator

    print(f"\n=== Text2Icon [{name}] ===")
    gen = SanaIconGenerator(
        verbose=True,
        low_vram=True,
        torch_compile=kwargs.get("torch_compile", False),
        torch_compile_mode=kwargs.get("torch_compile_mode", "default"),
        step_cache=kwargs.get("step_cache", "off"),
        channels_last=kwargs.get("channels_last", False),
    )
    t0 = time.perf_counter()
    gen.warmup()
    _sync_cuda()
    load_s = time.perf_counter() - t0

    times: dict[str, float] = {}
    try:
        for tag in ("cold", "hot"):
            t1 = time.perf_counter()
            img, _meta = gen.generate(
                ICON_PROMPT,
                width=width,
                height=height,
                num_inference_steps=steps,
                guidance_scale=4.5,
                seed=SEED,
                remove_background=False,
            )
            _sync_cuda()
            times[tag] = time.perf_counter() - t1
            out = OUT_DIR / f"text2icon_{name}_{tag}.png"
            gen.save_image(img, out)
            print(f"  {tag}: {times[tag]:.1f}s → {out}")
        notes = "ok"
    except Exception as exc:
        times.setdefault("cold", float("nan"))
        times.setdefault("hot", float("nan"))
        notes = f"FAIL:{type(exc).__name__}:{exc}"
        print(f"  FAIL: {exc}")
    finally:
        gen.unload()
        del gen
        _clear_gpu()

    return RunResult(
        "text2icon",
        name,
        load_s,
        times.get("cold", float("nan")),
        times.get("hot", float("nan")),
        notes,
    )


def bench_text2icon(configs: list[tuple[str, dict]], *, width: int, height: int, steps: int) -> list[RunResult]:
    """Corre cada config em subprocesso — Sana/group_offload deixa VRAM presa no mesmo PID."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve()
    py = sys.executable
    results: list[RunResult] = []
    for name, _kwargs in configs:
        print(f"\n=== Text2Icon subprocess [{name}] ===", flush=True)
        cmd = [
            py,
            "-u",
            str(script),
            "--tool",
            "text2icon",
            "--only",
            name,
            "--width",
            str(width),
            "--height",
            str(height),
            "--t2d-steps",
            str(steps),
            "--append",
            "--inprocess",
        ]
        proc = subprocess.run(cmd, cwd=str(REPO), check=False)
        if proc.returncode != 0:
            results.append(RunResult("text2icon", name, float("nan"), float("nan"), float("nan"), "FAIL:subprocess"))
        else:
            # Linha já anotada pelo filho; placeholder para o resumo do pai.
            results.append(RunResult("text2icon", name, float("nan"), float("nan"), float("nan"), "subprocess-ok"))
    return results


def bench_text2icon_inprocess(
    configs: list[tuple[str, dict]], *, width: int, height: int, steps: int
) -> list[RunResult]:
    sys.path.insert(0, str(REPO / "Shared" / "src"))
    sys.path.insert(0, str(REPO / "Text2Icon" / "src"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return [_run_text2icon_config(name, kwargs, width=width, height=height, steps=steps) for name, kwargs in configs]


TEXTURE_PROMPT = "seamless red wooden crate texture, stylized game prop, tileable"


def _run_texture2d_config(
    name: str,
    kwargs: dict[str, Any],
    *,
    width: int,
    height: int,
    steps: int,
) -> RunResult:
    from texture2d.generator import TextureGenerator

    print(f"\n=== Texture2D [{name}] ===")
    gen = TextureGenerator(
        verbose=True,
        torch_compile=kwargs.get("torch_compile", False),
        torch_compile_mode=kwargs.get("torch_compile_mode", "default"),
        channels_last=kwargs.get("channels_last", False),
    )
    t0 = time.perf_counter()
    gen.warmup()
    _sync_cuda()
    load_s = time.perf_counter() - t0

    times: dict[str, float] = {}
    try:
        for tag in ("cold", "hot"):
            t1 = time.perf_counter()
            img, _meta = gen.generate(
                TEXTURE_PROMPT,
                width=width,
                height=height,
                num_inference_steps=steps,
                guidance_scale=7.0,
                seed=SEED,
                ground="off",
            )
            _sync_cuda()
            times[tag] = time.perf_counter() - t1
            out = OUT_DIR / f"texture2d_{name}_{tag}.png"
            gen.save_image(img, out)
            print(f"  {tag}: {times[tag]:.1f}s → {out}")
        notes = "ok"
        if kwargs.get("torch_compile"):
            notes = f"compile={kwargs.get('torch_compile_mode', 'default')}"
        if kwargs.get("channels_last"):
            notes = (notes + " | channels_last") if notes != "ok" else "channels_last"
    except Exception as exc:
        times.setdefault("cold", float("nan"))
        times.setdefault("hot", float("nan"))
        notes = f"FAIL:{type(exc).__name__}:{exc}"
        print(f"  FAIL: {exc}")
    finally:
        gen.unload()
        del gen
        _clear_gpu()

    return RunResult(
        "texture2d",
        name,
        load_s,
        times.get("cold", float("nan")),
        times.get("hot", float("nan")),
        notes,
    )


def bench_texture2d(configs: list[tuple[str, dict]], *, width: int, height: int, steps: int) -> list[RunResult]:
    sys.path.insert(0, str(REPO / "Shared" / "src"))
    sys.path.insert(0, str(REPO / "Texture2D" / "src"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return [_run_texture2d_config(name, kwargs, width=width, height=height, steps=steps) for name, kwargs in configs]


SKY_PROMPT = "clear blue sky with soft clouds, equirectangular 360 skymap, game environment"


def _run_skymap2d_config(
    name: str,
    kwargs: dict[str, Any],
    *,
    width: int,
    height: int,
    steps: int,
) -> RunResult:
    from skymap2d.generator import SkymapGenerator

    print(f"\n=== Skymap2D [{name}] ===")
    gen = SkymapGenerator(
        verbose=True,
        memory_efficient=True,
        torch_compile=kwargs.get("torch_compile", False),
        torch_compile_mode=kwargs.get("torch_compile_mode", "default"),
        step_cache=kwargs.get("step_cache", "off"),
        channels_last=kwargs.get("channels_last", False),
    )
    t0 = time.perf_counter()
    gen.warmup()
    _sync_cuda()
    load_s = time.perf_counter() - t0

    times: dict[str, float] = {}
    try:
        for tag in ("cold", "hot"):
            t1 = time.perf_counter()
            img, _meta = gen.generate(
                SKY_PROMPT,
                width=width,
                height=height,
                num_inference_steps=steps,
                guidance_scale=3.5,
                seed=SEED,
            )
            _sync_cuda()
            times[tag] = time.perf_counter() - t1
            out = OUT_DIR / f"skymap2d_{name}_{tag}.png"
            gen.save_image(img, out)
            print(f"  {tag}: {times[tag]:.1f}s → {out}")
        notes_bits = ["mem-eff"]
        if kwargs.get("torch_compile"):
            notes_bits.append(f"compile={kwargs.get('torch_compile_mode', 'default')}")
        if kwargs.get("channels_last"):
            notes_bits.append("channels_last")
        if kwargs.get("step_cache") and kwargs.get("step_cache") != "off":
            notes_bits.append(f"step-cache={kwargs['step_cache']}")
        notes = " | ".join(notes_bits)
    except Exception as exc:
        times.setdefault("cold", float("nan"))
        times.setdefault("hot", float("nan"))
        notes = f"FAIL:{type(exc).__name__}:{exc}"
        print(f"  FAIL: {exc}")
    finally:
        gen.unload()
        del gen
        _clear_gpu()

    return RunResult(
        "skymap2d",
        name,
        load_s,
        times.get("cold", float("nan")),
        times.get("hot", float("nan")),
        notes,
    )


def bench_skymap2d_inprocess(
    configs: list[tuple[str, dict]], *, width: int, height: int, steps: int
) -> list[RunResult]:
    sys.path.insert(0, str(REPO / "Shared" / "src"))
    sys.path.insert(0, str(REPO / "Skymap2D" / "src"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return [_run_skymap2d_config(name, kwargs, width=width, height=height, steps=steps) for name, kwargs in configs]


def bench_skymap2d(configs: list[tuple[str, dict]], *, width: int, height: int, steps: int) -> list[RunResult]:
    """Subprocesso por config — FLUX+offload deixa VRAM presa no mesmo PID."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve()
    py = sys.executable
    results: list[RunResult] = []
    for name, _kwargs in configs:
        print(f"\n=== Skymap2D subprocess [{name}] ===", flush=True)
        cmd = [
            py,
            "-u",
            str(script),
            "--tool",
            "skymap2d",
            "--only",
            name,
            "--width",
            str(width),
            "--height",
            str(height),
            "--t2d-steps",
            str(steps),
            "--append",
            "--inprocess",
        ]
        proc = subprocess.run(cmd, cwd=str(REPO), check=False)
        note = "subprocess-ok" if proc.returncode == 0 else "FAIL:subprocess"
        results.append(RunResult("skymap2d", name, float("nan"), float("nan"), float("nan"), note))
    return results


SOUND_PROMPT = "wooden crate impact thud, short game sfx, dry"


def _run_text2sound_config(name: str, kwargs: dict[str, Any], *, duration: float, steps: int) -> RunResult:
    from text2sound.generator import AudioGenerator
    from text2sound.models import MODEL_EFFECTS_ID

    print(f"\n=== Text2Sound [{name}] ===")
    AudioGenerator.reset_instance()
    gen = AudioGenerator(
        model_id=MODEL_EFFECTS_ID,
        auto_clear=False,
        torch_compile=kwargs.get("torch_compile", False),
        torch_compile_mode=kwargs.get("torch_compile_mode", "default"),
        channels_last=kwargs.get("channels_last", False),
    )
    t0 = time.perf_counter()
    gen.load()
    _sync_cuda()
    load_s = time.perf_counter() - t0

    times: dict[str, float] = {}
    try:
        for tag in ("cold", "hot"):
            t1 = time.perf_counter()
            result = gen.generate(
                SOUND_PROMPT,
                duration=duration,
                steps=steps,
                cfg_scale=4.0,
                seed=SEED,
                sampler_type="euler",
            )
            _sync_cuda()
            times[tag] = time.perf_counter() - t1
            out = OUT_DIR / f"text2sound_{name}_{tag}.wav"
            from text2sound.audio_processor import save_audio

            save_audio(result.audio, result.sample_rate, out, fmt="wav")
            print(f"  {tag}: {times[tag]:.1f}s → {out}")
        notes_bits = ["effects/small", f"d={duration}s", f"steps={steps}"]
        if kwargs.get("torch_compile"):
            notes_bits.append(f"compile={kwargs.get('torch_compile_mode', 'default')}")
        if kwargs.get("channels_last"):
            notes_bits.append("channels_last")
        notes = " | ".join(notes_bits)
    except Exception as exc:
        times.setdefault("cold", float("nan"))
        times.setdefault("hot", float("nan"))
        notes = f"FAIL:{type(exc).__name__}:{exc}"
        print(f"  FAIL: {exc}")
    finally:
        AudioGenerator.reset_instance()
        _clear_gpu()

    return RunResult(
        "text2sound",
        name,
        load_s,
        times.get("cold", float("nan")),
        times.get("hot", float("nan")),
        notes,
    )


def bench_text2sound_inprocess(configs: list[tuple[str, dict]], *, duration: float, steps: int) -> list[RunResult]:
    sys.path.insert(0, str(REPO / "Shared" / "src"))
    sys.path.insert(0, str(REPO / "Text2Sound" / "src"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return [_run_text2sound_config(name, kwargs, duration=duration, steps=steps) for name, kwargs in configs]


def bench_text2sound(configs: list[tuple[str, dict]], *, duration: float, steps: int) -> list[RunResult]:
    """Subprocesso por config — singleton + VRAM presa."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve()
    py = sys.executable
    results: list[RunResult] = []
    for name, _kwargs in configs:
        print(f"\n=== Text2Sound subprocess [{name}] ===", flush=True)
        cmd = [
            py,
            "-u",
            str(script),
            "--tool",
            "text2sound",
            "--only",
            name,
            "--t2d-steps",
            str(steps),
            "--append",
            "--inprocess",
        ]
        proc = subprocess.run(cmd, cwd=str(REPO), check=False)
        note = "subprocess-ok" if proc.returncode == 0 else "FAIL:subprocess"
        results.append(RunResult("text2sound", name, float("nan"), float("nan"), float("nan"), note))
    return results


def _run_text3d_config(
    name: str,
    kwargs: dict[str, Any],
    *,
    pil: Any,
    steps: int,
    octree: int,
    chunks: int,
) -> RunResult:
    from text3d.generator import HunyuanTextTo3DGenerator

    print(f"\n=== Text3D [{name}] ===")
    gen = HunyuanTextTo3DGenerator(
        verbose=True,
        compile_models=kwargs.get("compile_models", False),
        compile_mode=kwargs.get("compile_mode", "default"),
        volume_decoder=kwargs.get("volume_decoder", "vanilla"),
        sage_attention=kwargs.get("sage_attention", False),
        sdnq_quantized_matmul=kwargs.get("sdnq_matmul", False),
        allow_group_offload=kwargs.get("allow_group_offload", False),
        fp8_layerwise=kwargs.get("fp8_layerwise", False),
        channels_last=kwargs.get("channels_last", False),
        sdnq_preset=kwargs.get("sdnq_preset", "sdnq-int4"),
    )
    t0 = time.perf_counter()
    gen._load_hunyuan()
    _sync_cuda()
    load_s = time.perf_counter() - t0

    times: dict[str, float] = {}
    for tag in ("cold", "hot"):
        t1 = time.perf_counter()
        mesh = gen.generate_from_image(
            pil,
            num_inference_steps=steps,
            guidance_scale=5.0,
            octree_resolution=octree,
            num_chunks=chunks,
            hy_seed=SEED,
            remove_bg=False,
            keep_loaded=True,
        )
        _sync_cuda()
        times[tag] = time.perf_counter() - t1
        out = OUT_DIR / f"text3d_{name}_{tag}.glb"
        mesh.export(str(out))
        print(f"  {tag}: {times[tag]:.1f}s → {out}")

    plan = getattr(gen, "_offload_plan", None)
    notes = plan.summary() if plan is not None and hasattr(plan, "summary") else ""
    gen.unload_hunyuan()
    del gen
    _clear_gpu()
    return RunResult("text3d", name, load_s, times["cold"], times["hot"], notes)


def bench_text3d(
    configs: list[tuple[str, dict]],
    *,
    image: Path,
    steps: int,
    octree: int,
    chunks: int,
) -> list[RunResult]:
    sys.path.insert(0, str(REPO / "Shared" / "src"))
    sys.path.insert(0, str(REPO / "Text3D" / "src"))
    from PIL import Image

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pil = Image.open(image).convert("RGB")
    return [
        _run_text3d_config(name, kwargs, pil=pil, steps=steps, octree=octree, chunks=chunks) for name, kwargs in configs
    ]


def _run_paint3d_config(
    name: str,
    kwargs: dict[str, Any],
    *,
    mesh_path: Path,
    image_path: Path,
    memory_efficient: bool,
) -> RunResult:
    import os

    from paint3d.painter import PaintBatchProcessor
    from paint3d.utils.mesh_io import load_mesh_trimesh, save_glb

    print(f"\n=== Paint3D [{name}] ===")
    if kwargs.get("torch_compile"):
        os.environ.pop("TORCHDYNAMO_DISABLE", None)
    else:
        os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")

    notes_bits: list[str] = []
    if memory_efficient:
        notes_bits.append("mem-eff")
    if kwargs.get("torch_compile"):
        notes_bits.append(f"compile={kwargs.get('torch_compile_mode', 'default')}")
    if kwargs.get("channels_last"):
        notes_bits.append("channels_last")
    if kwargs.get("allow_group_offload"):
        notes_bits.append("group-offload")

    t0 = time.perf_counter()
    proc = PaintBatchProcessor(
        verbose=True,
        memory_efficient=memory_efficient,
        preserve_origin=False,
        torch_compile=kwargs.get("torch_compile", False),
        torch_compile_mode=kwargs.get("torch_compile_mode", "default"),
        channels_last=kwargs.get("channels_last", False),
        allow_group_offload=kwargs.get("allow_group_offload", False),
    )
    proc.__enter__()
    _sync_cuda()
    load_s = time.perf_counter() - t0

    times: dict[str, float] = {}
    try:
        for tag in ("cold", "hot"):
            # Reload mesh each paint — bpy Object RNA dies after save/export.
            mesh = load_mesh_trimesh(mesh_path)
            t1 = time.perf_counter()
            textured = proc.paint_mesh(mesh, image_path)
            _sync_cuda()
            times[tag] = time.perf_counter() - t1
            out = OUT_DIR / f"paint3d_{name}_{tag}.glb"
            save_glb(textured, out)
            print(f"  {tag}: {times[tag]:.1f}s → {out}")
    except Exception as exc:
        times.setdefault("cold", float("nan"))
        times.setdefault("hot", float("nan"))
        notes_bits.append(f"FAIL:{type(exc).__name__}:{exc}")
        print(f"  FAIL: {exc}")
    finally:
        proc.__exit__(None, None, None)
        del proc
        _clear_gpu()

    return RunResult(
        "paint3d",
        name,
        load_s,
        times.get("cold", float("nan")),
        times.get("hot", float("nan")),
        " | ".join(notes_bits) or "ok",
    )


def bench_paint3d(
    configs: list[tuple[str, dict]],
    *,
    mesh: Path,
    image: Path,
    memory_efficient: bool,
) -> list[RunResult]:
    sys.path.insert(0, str(REPO / "Shared" / "src"))
    sys.path.insert(0, str(REPO / "Paint3D" / "src"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return [
        _run_paint3d_config(name, kwargs, mesh_path=mesh, image_path=image, memory_efficient=memory_efficient)
        for name, kwargs in configs
    ]


def _run_part3d_config(
    name: str,
    kwargs: dict[str, Any],
    *,
    mesh_path: Path,
    memory_efficient: bool,
    quality: str,
) -> RunResult:
    from part3d.pipeline import Part3DPipeline

    print(f"\n=== Part3D [{name}] ===")
    notes_bits: list[str] = [f"q={quality}"]
    if memory_efficient:
        notes_bits.append("mem-eff")
    vd = kwargs.get("volume_decoder", "flashvdm")
    notes_bits.append(f"vd={vd}")
    if kwargs.get("enable_torch_compile"):
        notes_bits.append(f"compile={kwargs.get('torch_compile_mode', 'default')}")
    cl = kwargs.get("channels_last")
    if cl is False:
        notes_bits.append("no-channels-last")
    elif cl is True:
        notes_bits.append("channels_last")

    pipe = Part3DPipeline(
        verbose=True,
        memory_efficient=memory_efficient,
        cpu_offload=memory_efficient,
        quality=quality,
        volume_decoder=vd,
        channels_last=kwargs.get("channels_last", True),
        enable_torch_compile=kwargs.get("enable_torch_compile", False),
        torch_compile_mode=kwargs.get("torch_compile_mode", "default"),
        autotune=True,
    )
    t0 = time.perf_counter()
    pipe.load()
    _sync_cuda()
    load_s = time.perf_counter() - t0

    times: dict[str, float] = {}
    try:
        for tag in ("cold", "hot"):
            t1 = time.perf_counter()
            parts_scene, _face_ids, _clean = pipe(
                mesh_path,
                seed=SEED,
                postprocess=False,
            )
            _sync_cuda()
            times[tag] = time.perf_counter() - t1
            out = OUT_DIR / f"part3d_{name}_{tag}.glb"
            parts_scene.export(str(out))
            print(f"  {tag}: {times[tag]:.1f}s → {out}")
    except Exception as exc:
        times.setdefault("cold", float("nan"))
        times.setdefault("hot", float("nan"))
        notes_bits.append(f"FAIL:{type(exc).__name__}:{exc}")
        print(f"  FAIL: {exc}")
    finally:
        pipe.unload()
        del pipe
        _clear_gpu()

    return RunResult(
        "part3d",
        name,
        load_s,
        times.get("cold", float("nan")),
        times.get("hot", float("nan")),
        " | ".join(notes_bits),
    )


def bench_part3d(
    configs: list[tuple[str, dict]],
    *,
    mesh: Path,
    memory_efficient: bool,
    quality: str,
) -> list[RunResult]:
    sys.path.insert(0, str(REPO / "Shared" / "src"))
    sys.path.insert(0, str(REPO / "Part3D" / "src"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return [
        _run_part3d_config(
            name,
            kwargs,
            mesh_path=mesh,
            memory_efficient=memory_efficient,
            quality=quality,
        )
        for name, kwargs in configs
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--tool",
        choices=[
            "text2d",
            "text2icon",
            "texture2d",
            "skymap2d",
            "text2sound",
            "text3d",
            "paint3d",
            "part3d",
            "all",
        ],
        default="text2d",
    )
    ap.add_argument("--image", type=Path, default=None, help="PNG ref (Text3D / Paint3D)")
    ap.add_argument("--mesh", type=Path, default=None, help="GLB mesh para Paint3D / Part3D")
    ap.add_argument("--append", action="store_true", default=True, help="Escrever linhas no MD")
    ap.add_argument("--no-append", action="store_false", dest="append")
    ap.add_argument("--width", type=int, default=512)
    ap.add_argument("--height", type=int, default=512)
    ap.add_argument("--t2d-steps", type=int, default=4)
    ap.add_argument("--t3d-steps", type=int, default=20)
    ap.add_argument("--octree", type=int, default=256)
    ap.add_argument("--chunks", type=int, default=8000)
    ap.add_argument(
        "--paint-full-vram",
        action="store_true",
        help="Paint3D sem memory_efficient (default: mem-eff para 6GB)",
    )
    ap.add_argument(
        "--part3d-quality",
        default="fast",
        help="Quality tier Part3D bench (default: fast)",
    )
    ap.add_argument("--only", nargs="*", default=None, help="IDs de config a correr")
    ap.add_argument(
        "--inprocess",
        action="store_true",
        help="Text2Icon/Skymap2D: correr no processo actual (filho do subprocesso-pai)",
    )
    args = ap.parse_args()

    t2d_configs: list[tuple[str, dict]] = [
        ("t2d-baseline", {}),
        ("t2d-channels-last", {"channels_last": True}),
        ("t2d-compile", {"torch_compile": True, "torch_compile_mode": "default"}),
        ("t2d-compile-cl", {"torch_compile": True, "channels_last": True}),
        ("t2d-step-cache", {"step_cache": "auto"}),
    ]
    t2i_configs: list[tuple[str, dict]] = [
        ("t2i-baseline", {}),
        ("t2i-channels-last", {"channels_last": True}),
        ("t2i-compile", {"torch_compile": True, "torch_compile_mode": "default"}),
        ("t2i-compile-cl", {"torch_compile": True, "channels_last": True}),
    ]
    tex_configs: list[tuple[str, dict]] = [
        ("tex-baseline", {}),
        ("tex-channels-last", {"channels_last": True}),
        ("tex-compile", {"torch_compile": True, "torch_compile_mode": "default"}),
        ("tex-compile-cl", {"torch_compile": True, "channels_last": True}),
    ]
    sky_configs: list[tuple[str, dict]] = [
        ("sky-baseline", {}),
        ("sky-channels-last", {"channels_last": True}),
        ("sky-compile", {"torch_compile": True, "torch_compile_mode": "default"}),
        ("sky-compile-cl", {"torch_compile": True, "channels_last": True}),
    ]
    snd_configs: list[tuple[str, dict]] = [
        ("snd-baseline", {}),
        ("snd-channels-last", {"channels_last": True}),
        ("snd-compile", {"torch_compile": True, "torch_compile_mode": "default"}),
        ("snd-compile-cl", {"torch_compile": True, "channels_last": True}),
    ]
    t3d_configs: list[tuple[str, dict]] = [
        ("t3d-baseline", {}),
        ("t3d-flashvdm", {"volume_decoder": "flashvdm"}),
        ("t3d-compile", {"compile_models": True}),
        ("t3d-flashvdm-compile", {"volume_decoder": "flashvdm", "compile_models": True}),
        ("t3d-fp8", {"fp8_layerwise": True}),
        ("t3d-channels-last", {"channels_last": True}),
        ("t3d-group-offload", {"allow_group_offload": True}),
    ]
    p3d_configs: list[tuple[str, dict]] = [
        ("p3d-baseline", {}),
        ("p3d-channels-last", {"channels_last": True}),
        ("p3d-compile", {"torch_compile": True, "torch_compile_mode": "default"}),
        ("p3d-compile-cl", {"torch_compile": True, "channels_last": True}),
    ]
    # Part3D: CL default True; baseline = flashvdm (mem-eff path).
    pt3d_configs: list[tuple[str, dict]] = [
        ("pt3d-baseline", {"volume_decoder": "flashvdm", "channels_last": True}),
        ("pt3d-no-cl", {"volume_decoder": "flashvdm", "channels_last": False}),
        ("pt3d-hierarchical", {"volume_decoder": "hierarchical", "channels_last": True}),
        (
            "pt3d-compile",
            {
                "volume_decoder": "flashvdm",
                "channels_last": True,
                "enable_torch_compile": True,
                "torch_compile_mode": "default",
            },
        ),
    ]

    if args.only:
        t2d_configs = [c for c in t2d_configs if c[0] in args.only]
        t2i_configs = [c for c in t2i_configs if c[0] in args.only]
        tex_configs = [c for c in tex_configs if c[0] in args.only]
        sky_configs = [c for c in sky_configs if c[0] in args.only]
        snd_configs = [c for c in snd_configs if c[0] in args.only]
        t3d_configs = [c for c in t3d_configs if c[0] in args.only]
        p3d_configs = [c for c in p3d_configs if c[0] in args.only]
        pt3d_configs = [c for c in pt3d_configs if c[0] in args.only]

    all_rows: list[RunResult] = []

    if args.tool in ("text2d", "all"):
        all_rows.extend(bench_text2d(t2d_configs, width=args.width, height=args.height, steps=args.t2d_steps))

    if args.tool in ("text2icon", "all"):
        t2i_kw = dict(width=args.width, height=args.height, steps=max(args.t2d_steps, 8))
        if args.inprocess or (args.only and len(t2i_configs) == 1):
            # Filho (uma config) ou --inprocess explícito: medir no PID actual.
            all_rows.extend(bench_text2icon_inprocess(t2i_configs, **t2i_kw))
        else:
            all_rows.extend(bench_text2icon(t2i_configs, **t2i_kw))

    if args.tool in ("texture2d", "all"):
        # SD1.5: 20 steps no bench (default CLI=30) para cold/hot mais rápidos.
        all_rows.extend(
            bench_texture2d(tex_configs, width=args.width, height=args.height, steps=max(args.t2d_steps, 20))
        )

    if args.tool in ("skymap2d", "all"):
        # quality=fast defaults: 1024x512, 14 steps (6GB mem-eff).
        sky_w = args.width if args.width != 512 else 1024
        sky_h = args.height if args.height != 512 else 512
        sky_steps = max(args.t2d_steps, 14)
        sky_kw = dict(width=sky_w, height=sky_h, steps=sky_steps)
        if args.inprocess or (args.only and len(sky_configs) == 1):
            all_rows.extend(bench_skymap2d_inprocess(sky_configs, **sky_kw))
        else:
            all_rows.extend(bench_skymap2d(sky_configs, **sky_kw))

    if args.tool in ("text2sound", "all"):
        # effects/small, quality=fast steps=12, duração curta 2s.
        snd_steps = max(args.t2d_steps, 12)
        snd_kw = dict(duration=2.0, steps=snd_steps)
        if args.inprocess or (args.only and len(snd_configs) == 1):
            all_rows.extend(bench_text2sound_inprocess(snd_configs, **snd_kw))
        else:
            all_rows.extend(bench_text2sound(snd_configs, **snd_kw))

    if args.tool in ("text3d", "all"):
        img = args.image
        if img is None:
            cand = OUT_DIR / "text2d_t2d-baseline_hot.png"
            if not cand.is_file():
                cand = OUT_DIR / "text2d_t2d-baseline_cold.png"
            if not cand.is_file():
                raise SystemExit("Passa --image PNG ou corre --tool text2d primeiro")
            img = cand
        all_rows.extend(
            bench_text3d(
                t3d_configs,
                image=img,
                steps=args.t3d_steps,
                octree=args.octree,
                chunks=args.chunks,
            )
        )

    if args.tool in ("paint3d", "all"):
        mesh = args.mesh
        if mesh is None:
            mesh = OUT_DIR / "text3d_t3d-flashvdm_hot.glb"
            if not mesh.is_file():
                mesh = OUT_DIR / "text3d_t3d-flashvdm_cold.glb"
            if not mesh.is_file():
                raise SystemExit("Passa --mesh GLB ou corre --tool text3d flashvdm primeiro")
        img = args.image
        if img is None:
            img = OUT_DIR / "text2d_t2d-baseline_hot.png"
            if not img.is_file():
                img = OUT_DIR / "text2d_t2d-baseline_cold.png"
            if not img.is_file():
                raise SystemExit("Passa --image PNG ou corre --tool text2d primeiro")
        all_rows.extend(
            bench_paint3d(
                p3d_configs,
                mesh=mesh,
                image=img,
                memory_efficient=not args.paint_full_vram,
            )
        )

    if args.tool in ("part3d", "all"):
        mesh = args.mesh
        if mesh is None:
            # Prefer shape-only Text3D mesh; paint GLBs explode bpy part repair.
            mesh = OUT_DIR / "text3d_t3d-flashvdm_hot.glb"
            if not mesh.is_file():
                mesh = OUT_DIR / "text3d_t3d-flashvdm_cold.glb"
            if not mesh.is_file():
                mesh = OUT_DIR / "paint3d_p3d-baseline_hot.glb"
            if not mesh.is_file():
                raise SystemExit("Passa --mesh GLB ou corre text3d/paint3d bench primeiro")
        all_rows.extend(
            bench_part3d(
                pt3d_configs,
                mesh=mesh,
                memory_efficient=True,
                quality=args.part3d_quality,
            )
        )

    if args.append:
        for row in all_rows:
            # Filhos já anotaram; placeholders do pai (ok/FAIL) não vão para o MD.
            if row.notes.startswith("subprocess") or row.notes.startswith("FAIL:subprocess"):
                continue
            _append_md_row(row)

    print("\n=== Resumo ===")
    for r in all_rows:
        cold = f"{r.cold_s:6.1f}" if r.cold_s == r.cold_s else "  FAIL"
        hot = f"{r.hot_s:6.1f}" if r.hot_s == r.hot_s else "  FAIL"
        print(f"{r.config:28s} load={r.load_s:6.1f}s cold={cold}s hot={hot}s  {r.notes}")


if __name__ == "__main__":
    main()
