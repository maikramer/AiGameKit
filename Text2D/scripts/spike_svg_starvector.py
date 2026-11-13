#!/usr/bin/env python3
"""Spike do motor StarVector-1B (image→SVG) no Text2D.

Compara o VTracer (CPU) com o StarVector no mesmo ícone e valida o SVG final
pelo mesmo caminho do CLI (extract → repair → sanitize → write). Serve de
gate: se o StarVector não carregar no transformers do venv, o resto do
text2svg fica só com VTracer.

Uso:
    Text2D/.venv/bin/python scripts/spike_svg_starvector.py ICON.png \
        -o /tmp/spike.svg --device cuda --max-tokens 4000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image  # noqa: E402

from text2d.svg.api import save_svg  # noqa: E402
from text2d.svg.options import SvgOptions  # noqa: E402
from text2d.svg.tracer import trace_image  # noqa: E402
from text2d.svg.validate import count_svg_shapes  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Spike StarVector-1B image→SVG")
    parser.add_argument("image", type=Path)
    parser.add_argument("--output", "-o", type=Path, default=Path("/tmp/spike-starvector.svg"))
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="cuda")
    parser.add_argument("--max-tokens", type=int, default=4000)
    parser.add_argument("--colors", type=int, default=8)
    parser.add_argument("--style", default="flat")
    parser.add_argument("--detail", default="medium")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--skip-vtracer", action="store_true")
    args = parser.parse_args()

    if not args.image.is_file():
        parser.error(f"imagem não encontrada: {args.image}")

    image = Image.open(args.image)
    image.load()
    width, height = image.size
    report: dict[str, object] = {
        "image": str(args.image),
        "size": [width, height],
        "mode": image.mode,
    }

    common = SvgOptions(
        engine="vtracer",
        style=args.style,
        colors=args.colors,
        detail=args.detail,
        max_tokens=args.max_tokens,
        seed=args.seed,
        device=args.device,
        preview=False,
        transparent=False,
        meta=False,
        strict=False,
        max_kb=4096,
    )

    if not args.skip_vtracer:
        started = time.perf_counter()
        vtracer_svg = trace_image(image, common)
        vtracer_elapsed = time.perf_counter() - started
        report["vtracer"] = {
            "seconds": round(vtracer_elapsed, 2),
            "paths": count_svg_shapes(vtracer_svg),
            "kb": round(len(vtracer_svg.encode("utf-8")) / 1024, 1),
        }

    from text2d.svg.starvector import StarVectorEngine

    engine = StarVectorEngine(
        device=args.device,
        max_tokens=args.max_tokens,
        verbose=True,
    )
    try:
        started = time.perf_counter()
        engine.warmup()
        load_elapsed = time.perf_counter() - started

        started = time.perf_counter()
        raw_svg = engine.vectorize(image, common)
        generate_elapsed = time.perf_counter() - started

        result = save_svg(
            raw_svg,
            args.output,
            SvgOptions(
                engine="starvector",
                style=args.style,
                colors=args.colors,
                detail=args.detail,
                max_tokens=args.max_tokens,
                seed=args.seed,
                device=args.device,
                preview=args.preview,
                meta=False,
                strict=False,
                max_kb=4096,
            ),
            source_image=args.image,
            prompt=args.prompt,
            seed=args.seed,
            engine_meta={
                "engine": "starvector",
                "model_id": engine.model_id,
                "device": engine.device,
                "dtype": str(engine.dtype),
            },
            width=width,
            height=height,
            seconds=generate_elapsed,
        )
        report["starvector"] = {
            "seconds": round(generate_elapsed, 2),
            "load_seconds": round(load_elapsed, 2),
            "paths": result.path_count,
            "kb": round(result.bytes / 1024, 1),
            "output": str(result.path),
            "warning": result.warning,
        }
        try:
            import torch

            if torch.cuda.is_available():
                report["vram_peak_gib"] = round(torch.cuda.max_memory_allocated() / 1024**3, 2)
        except Exception:
            pass
        report["ok"] = True
    except Exception as exc:
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        engine.unload()

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
