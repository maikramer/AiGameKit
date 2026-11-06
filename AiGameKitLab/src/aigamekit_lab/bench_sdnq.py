"""Bench SDNQ recipes on real weight tensors without loading a full model.

O objetivo é comparar receitas de quantização (presets candidatos) em camadas
reais dos checkpoints usados pelas tools. Os tensores são lidos diretamente
dos shards ``safetensors`` (ou de ``.bin`` com mmap), quantizados com
``sdnq_quantize_layer_weight`` e avaliados por:

- erro relativo de Frobenius dos pesos (``w_rel``);
- erro relativo de saída ``||WX - WqX|| / ||WX||`` com ``X`` aleatório — proxy
  mais próximo do que a geração vê do que o erro dos pesos;
- bytes/peso exatos (peso empacotado + escalas + zero-points + SVD);
- tempo de quantização (impacto no load).

Uso típico::

    aigamekit-lab bench sdnq --layers 4 --json /tmp/sdnq.json
    aigamekit-lab bench sdnq --checkpoint path/to/model.safetensors --candidate uint4-proposed

Notas:
- Não requer GPU: ``--device cpu`` (default) funciona em qualquer máquina.
- ``sdnq`` tem de estar instalado no ambiente que corre o comando.
- As receitas em :data:`CANDIDATES` são deliberadamente explícitas (kwargs de
  ``SDNQConfig``) para poderem ser medidas antes de virarem presets oficiais.
"""

from __future__ import annotations

import glob
import json
import os
import random
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Globs (relativos a ``HF_HOME/hub`` ou ``~/.cache/huggingface/hub``) usados
# quando o utilizador não passa ``--checkpoint``. Só entram os que existirem.
DEFAULT_CHECKPOINT_GLOBS: dict[str, str] = {
    "flux3-klein-transformer": ("models--black-forest-labs--flux-3-image-klein/snapshots/*/transformer/*.safetensors"),
    "sd15-unet": ("models--stable-diffusion-v1-5--stable-diffusion-v1-5/snapshots/*/unet/*.safetensors"),
    "hunyuan3d-paint-unet": ("models--tencent--Hunyuan3D-2.1/snapshots/*/hunyuan3d-paintpbr-v2-1/unet/*.bin"),
    "hunyuan3d-omni-dit": ("models--tencent--Hunyuan3D-Omni/snapshots/*/model/*.bin"),
}


@dataclass(frozen=True)
class Candidate:
    """Receita de quantização candidata.

    ``kwargs`` são passados diretamente a
    ``sdnq.quantizer.sdnq_quantize_layer_weight`` (ou ao variante dynamic,
    conforme ``dynamic``).
    """

    name: str
    description: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    dynamic: bool = False


# Receitas comparadas por defeito. ``current-*`` reproduzem os presets que o
# monorepo usa hoje; ``*-proposed`` são as receitas novas (SDNQ 0.2.8/0.2.9).
CANDIDATES: dict[str, Candidate] = {
    "current-uint8": Candidate(
        "current-uint8",
        "sdnq-uint8 atual — uint8 + Lloyd-Max codebook (group auto=8192)",
        {
            "weights_dtype": "uint8",
            "group_size": 8192,
            "use_codebook": True,
        },
    ),
    "uint8-plain": Candidate(
        "uint8-plain",
        "uint8 sem codebook (group auto) — ~5% menos VRAM e load muito mais rápido",
        {
            "weights_dtype": "uint8",
            "group_size": 0,
        },
    ),
    "current-int4": Candidate(
        "current-int4",
        "sdnq-int4 atual — int4 signed + SVD, group 32, escalas fp32",
        {
            "weights_dtype": "int4",
            "group_size": 32,
            "use_svd": True,
        },
    ),
    "uint4-proposed": Candidate(
        "uint4-proposed",
        "uint4 + SVD + Hadamard N4 + escalas double-quant (uint8), group 32",
        {
            "weights_dtype": "uint4",
            "group_size": 32,
            "use_svd": True,
            "use_hadamard": True,
            "hadamard_group_size": 256,
            "scale_dtype": "uint8",
        },
    ),
    "uint4-hq-proposed": Candidate(
        "uint4-hq-proposed",
        "uint4 + SVD + Hadamard + double-quant scales, group 16 (tier de qualidade)",
        {
            "weights_dtype": "uint4",
            "group_size": 16,
            "use_svd": True,
            "use_hadamard": True,
            "hadamard_group_size": 256,
            "scale_dtype": "uint8",
        },
    ),
    "uint3-proposed": Candidate(
        "uint3-proposed",
        "uint3 + Hadamard + double-quant scales, group 32 (3-bit real)",
        {
            "weights_dtype": "uint3",
            "group_size": 32,
            "use_hadamard": True,
            "hadamard_group_size": 256,
            "scale_dtype": "uint8",
        },
    ),
    "current-int3": Candidate(
        "current-int3",
        "sdnq-int3 atual — uint3 + Hadamard + codebook (group 32)",
        {
            "weights_dtype": "uint3",
            "group_size": 32,
            "use_hadamard": True,
            "hadamard_group_size": 256,
            "use_codebook": True,
        },
    ),
    "uint2-proposed": Candidate(
        "uint2-proposed",
        "uint2 + Hadamard + double-quant scales, group 32 (2-bit real)",
        {
            "weights_dtype": "uint2",
            "group_size": 32,
            "use_hadamard": True,
            "hadamard_group_size": 256,
            "scale_dtype": "uint8",
        },
    ),
    "current-int2": Candidate(
        "current-int2",
        "sdnq-int2 atual — uint2 + Hadamard + codebook (group 32)",
        {
            "weights_dtype": "uint2",
            "group_size": 32,
            "use_hadamard": True,
            "hadamard_group_size": 256,
            "use_codebook": True,
        },
    ),
    "int4-dyn-proposed": Candidate(
        "int4-dyn-proposed",
        "uint4 dinâmico (piso 4-bit, upgrades por camada), SVD+Hadamard+dq",
        {
            "weights_dtype": "uint4",
            "group_size": 32,
            "use_svd": True,
            "use_hadamard": True,
            "hadamard_group_size": 256,
            "scale_dtype": "uint8",
        },
        dynamic=True,
    ),
}


@dataclass
class LayerResult:
    """Métricas de uma camada para um candidato."""

    checkpoint: str
    candidate: str
    layer: str
    shape: list[int]
    weight_rel: float
    output_rel: float
    bits_per_weight: float
    quant_seconds: float
    weights_dtype: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint": self.checkpoint,
            "candidate": self.candidate,
            "layer": self.layer,
            "shape": self.shape,
            "weight_rel": self.weight_rel,
            "output_rel": self.output_rel,
            "bits_per_weight": self.bits_per_weight,
            "quant_seconds": self.quant_seconds,
            "weights_dtype": self.weights_dtype,
        }


# ---------------------------------------------------------------------------
# Checkpoint discovery / tensor loading
# ---------------------------------------------------------------------------


def _hub_dir() -> Path:
    """Return the Hugging Face hub cache directory."""
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        return Path(hf_home).expanduser() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def discover_default_checkpoints() -> list[tuple[str, Path]]:
    """Find cached checkpoints matching :data:`DEFAULT_CHECKPOINT_GLOBS`.

    Returns:
        List of ``(name, path)`` for every glob that resolves. A checkpoint
        with multiple shards returns one entry per shard.
    """
    hub = _hub_dir()
    found: list[tuple[str, Path]] = []
    for name, pattern in DEFAULT_CHECKPOINT_GLOBS.items():
        matches = sorted(glob.glob(str(hub / pattern)))
        for match in matches:
            found.append((name, Path(match)))
    return found


def _expand_checkpoint_arg(value: str) -> list[tuple[str, Path]]:
    """Expand a ``--checkpoint`` argument into ``(name, path)`` shards."""
    path = Path(value).expanduser()
    if path.is_file():
        return [(path.stem, path)]
    if path.is_dir():
        files: list[Path] = []
        for suffix in ("*.safetensors", "*.bin"):
            files.extend(sorted(path.rglob(suffix)))
        return [(path.name, f) for f in files]
    raise FileNotFoundError(f"Checkpoint não encontrado: {value}")


def _tensor_shapes(path: Path) -> dict[str, tuple[int, ...]]:
    """Return ``{tensor_name: shape}`` for a safetensors or .bin checkpoint."""
    if path.suffix == ".safetensors":
        from safetensors import safe_open

        with safe_open(str(path), framework="pt", device="cpu") as handle:
            # ``safe_open`` não é iterável; ``.keys()`` não pode virar ``in handle``.
            keys = handle.keys()
            return {key: tuple(handle.get_slice(key).get_shape()) for key in keys}

    state = _load_bin_state(path)
    return {key: tuple(value.shape) for key, value in state.items() if hasattr(value, "shape")}


def _load_bin_state(path: Path) -> dict[str, Any]:
    """Load a ``.bin`` state dict lazily (mmap) so only read tensors cost RAM."""
    import torch

    try:
        return torch.load(str(path), map_location="cpu", mmap=True, weights_only=True)
    except TypeError:
        # torch < 2.0 não tem mmap; aceita o custo de carregar o ficheiro.
        return torch.load(str(path), map_location="cpu")


def _load_tensor(path: Path, key: str) -> Any:
    """Load a single tensor from a checkpoint."""
    if path.suffix == ".safetensors":
        from safetensors import safe_open

        with safe_open(str(path), framework="pt", device="cpu") as handle:
            return handle.get_tensor(key)

    state = _load_bin_state(path)
    tensor = state[key]
    # ``mmap`` devolve views do ficheiro; copiar liberta o handle ao sair.
    return tensor.clone() if hasattr(tensor, "clone") else tensor


def select_layers(
    shapes: dict[str, tuple[int, ...]],
    *,
    max_layers: int = 4,
    min_numel: int = 16384,
    min_dim: int = 32,
    seed: int = 0,
) -> list[str]:
    """Pick representative 2-D linear-like tensors, stratified by size.

    Amostra aleatória determinística dentro de três estratos de tamanho
    (pequeno/médio/grande) para não medir só as camadas gigantes.

    Args:
        shapes: ``{name: shape}`` do checkpoint.
        max_layers: Número máximo de camadas a devolver.
        min_numel: Ignora tensores com menos elementos (SDNQ também os salta).
        min_dim: Ignora tensores com alguma dimensão abaixo disto.
        seed: Semente para amostragem reprodutível.

    Returns:
        Nomes das camadas selecionadas, ordenados.
    """
    candidates = [
        (name, shape[0] * shape[1])
        for name, shape in shapes.items()
        if len(shape) == 2 and min(shape) >= min_dim and shape[0] * shape[1] >= min_numel
    ]
    if not candidates:
        return []
    candidates.sort(key=lambda item: (item[1], item[0]))

    picked: list[str] = []
    per_bucket = max(1, max_layers // 3)
    total = len(candidates)
    for bucket in range(3):
        lo = total * bucket // 3
        hi = total * (bucket + 1) // 3
        chunk = candidates[lo:hi] or candidates
        rng = random.Random(seed + bucket)
        take = min(per_bucket, len(chunk))
        picked.extend(name for name, _ in rng.sample(chunk, take))

    # Completa se a estratificação rendeu menos do que o pedido.
    if len(picked) < max_layers:
        remaining = [name for name, _ in candidates if name not in picked]
        rng = random.Random(seed + 99)
        picked.extend(rng.sample(remaining, min(max_layers - len(picked), len(remaining))))

    return sorted(dict.fromkeys(picked))[:max_layers]


# ---------------------------------------------------------------------------
# Quantization + metrics
# ---------------------------------------------------------------------------


def _tensor_bytes(payload: dict[str, Any]) -> int:
    """Exact storage bytes of the tensors produced by quantization."""
    import torch

    return sum(value.numel() * value.element_size() for value in payload.values() if isinstance(value, torch.Tensor))


def measure_candidate(
    weight: Any,
    candidate: Candidate,
    *,
    device: str = "cpu",
    seed: int = 0,
) -> tuple[float, float, float, float, str]:
    """Quantize one weight tensor and measure weight/output error and size.

    Args:
        weight: 2-D float tensor (fp16/bf16/fp32).
        candidate: Recipe to evaluate.
        device: ``cpu`` or ``cuda`` for the quantization step.
        seed: Seed for the random probe input.

    Returns:
        ``(weight_rel, output_rel, bits_per_weight, seconds, weights_dtype)``.
    """
    import torch
    from sdnq.quantizer import sdnq_quantize_layer_weight, sdnq_quantize_layer_weight_dynamic

    w = weight.to(device=device, dtype=torch.float32)
    kwargs = dict(candidate.kwargs)
    kwargs.pop("use_dynamic_quantization", None)

    started = time.perf_counter()
    if candidate.dynamic:
        result = sdnq_quantize_layer_weight_dynamic(w, layer_class_name="Linear", **kwargs)
        if isinstance(result[0], tuple):
            (dequantizer, payload), _ = result
        else:
            dequantizer, payload = result
    else:
        dequantizer, payload = sdnq_quantize_layer_weight(w, layer_class_name="Linear", **kwargs)
    seconds = time.perf_counter() - started

    dequantized = dequantizer(**payload)
    weight_rel = ((dequantized - w).norm() / w.norm()).item()

    generator = torch.Generator(device=device).manual_seed(seed)
    probe = torch.randn(w.shape[1], 64, device=device, generator=generator)
    reference = w @ probe
    output_rel = ((dequantized @ probe - reference).norm() / reference.norm()).item()

    bits_per_weight = _tensor_bytes(payload) * 8 / w.numel()
    return weight_rel, output_rel, bits_per_weight, seconds, str(dequantizer.weights_dtype)


def _group_checkpoints(resolved: list[tuple[str, Path]]) -> list[tuple[str, list[Path]]]:
    """Group shards by checkpoint name and drop alternative-format duplicates.

    Shards of the same checkpoint keep names like ``model-00001-of-00002``;
    alternate serializations (``.fp16.safetensors`` vs ``.safetensors``) don't.
    Nesses casos fica só o primeiro ficheiro (ordem lexical prefere ``.fp16``).
    """
    grouped: dict[str, list[Path]] = {}
    for name, path in resolved:
        grouped.setdefault(name, []).append(path)

    result: list[tuple[str, list[Path]]] = []
    for name, paths in grouped.items():
        paths = sorted(dict.fromkeys(paths))
        sharded = any("-of-" in path.name for path in paths)
        result.append((name, paths if sharded else paths[:1]))
    return result


def run_candidate_on_checkpoint(
    name: str,
    shards: list[Path],
    candidate: Candidate,
    *,
    max_layers: int,
    device: str,
    seed: int,
) -> list[LayerResult]:
    """Run one candidate on the sampled layers of a checkpoint."""
    shapes: dict[str, tuple[int, ...]] = {}
    shard_by_key: dict[str, Path] = {}
    bin_state: dict[str, Any] | None = None

    if len(shards) == 1 and shards[0].suffix == ".bin":
        # ``.bin`` (Paint/Omni) fica mapeado uma vez por checkpoint — abrir o
        # ficheiro de vários GB por camada seria desperdício.
        bin_state = _load_bin_state(shards[0])
        for key, value in bin_state.items():
            if hasattr(value, "shape"):
                shapes[key] = tuple(value.shape)
                shard_by_key[key] = shards[0]
    else:
        for shard in shards:
            for key, shape in _tensor_shapes(shard).items():
                shapes[key] = shape
                shard_by_key[key] = shard

    layers = select_layers(shapes, max_layers=max_layers, seed=seed)
    results: list[LayerResult] = []
    for key in layers:
        tensor = bin_state[key] if bin_state is not None else _load_tensor(shard_by_key[key], key)
        try:
            weight_rel, output_rel, bits, seconds, dtype = measure_candidate(
                tensor, candidate, device=device, seed=seed
            )
        finally:
            del tensor
        results.append(
            LayerResult(
                checkpoint=name,
                candidate=candidate.name,
                layer=key,
                shape=list(shapes[key]),
                weight_rel=weight_rel,
                output_rel=output_rel,
                bits_per_weight=bits,
                quant_seconds=seconds,
                weights_dtype=dtype,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Aggregation / report
# ---------------------------------------------------------------------------


def _percentile(values: list[float], fraction: float) -> float:
    """Simple nearest-rank percentile (no numpy dependency)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def aggregate(results: list[LayerResult]) -> list[dict[str, Any]]:
    """Aggregate per-layer metrics by ``(checkpoint, candidate)``."""
    grouped: dict[tuple[str, str], list[LayerResult]] = {}
    for result in results:
        grouped.setdefault((result.checkpoint, result.candidate), []).append(result)

    rows: list[dict[str, Any]] = []
    for (checkpoint, candidate), items in sorted(grouped.items()):
        weights = [item.weight_rel for item in items]
        outputs = [item.output_rel for item in items]
        bits = [item.bits_per_weight for item in items]
        seconds = [item.quant_seconds for item in items]
        rows.append(
            {
                "checkpoint": checkpoint,
                "candidate": candidate,
                "layers": len(items),
                "weight_rel_mean": statistics.fmean(weights),
                "weight_rel_p95": _percentile(weights, 0.95),
                "output_rel_mean": statistics.fmean(outputs),
                "bits_per_weight_mean": statistics.fmean(bits),
                "quant_seconds_mean": statistics.fmean(seconds),
                "weights_dtypes": sorted({item.weights_dtype for item in items}),
            }
        )
    return rows


def format_table(rows: list[dict[str, Any]]) -> str:
    """Render the aggregate rows as a fixed-width text table."""
    header = (
        f"{'checkpoint':26s} {'candidate':22s} {'n':>2s} {'w_rel':>8s} {'out_rel':>8s} {'b/w':>6s} {'t(s)':>6s} dtypes"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row['checkpoint'][:26]:26s} {row['candidate'][:22]:22s} {row['layers']:2d} "
            f"{row['weight_rel_mean']:8.5f} {row['output_rel_mean']:8.5f} "
            f"{row['bits_per_weight_mean']:6.2f} {row['quant_seconds_mean']:6.2f} "
            f"{','.join(row['weights_dtypes'])}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def run_sdnq_bench(
    *,
    checkpoints: list[str] | None = None,
    candidates: list[str] | None = None,
    max_layers: int = 4,
    device: str = "cpu",
    output_json: Path | None = None,
    seed: int = 0,
    list_only: bool = False,
) -> int:
    """Run the SDNQ recipe benchmark and optionally write JSON.

    Args:
        checkpoints: Paths/globs to checkpoints; ``None`` uses cached defaults.
        candidates: Candidate names from :data:`CANDIDATES`; ``None`` uses all.
        max_layers: Layers sampled per checkpoint.
        device: ``cpu``, ``cuda`` or ``auto``.
        output_json: Optional path for the machine-readable report.
        seed: Sampling/probe seed.
        list_only: Print candidates/checkpoints and exit without quantizing.

    Returns:
        Process exit code (0 success, 1 error/no data).
    """
    try:
        import sdnq  # noqa: F401
    except ImportError:
        print("SDNQ não instalado — instala com `pip install 'sdnq>=0.2.9'`.")
        return 1

    names = list(candidates) if candidates else list(CANDIDATES)
    unknown = [name for name in names if name not in CANDIDATES]
    if unknown:
        print(f"Candidato desconhecido: {', '.join(unknown)}. Disponíveis: {', '.join(CANDIDATES)}")
        return 1

    resolved: list[tuple[str, Path]] = []
    if checkpoints:
        for value in checkpoints:
            try:
                resolved.extend(_expand_checkpoint_arg(value))
            except FileNotFoundError as exc:
                print(str(exc))
                return 1
    else:
        resolved = discover_default_checkpoints()

    if not resolved:
        print(
            "Nenhum checkpoint encontrado. Passa --checkpoint <ficheiro|diretório> "
            "ou descarrega um dos modelos default."
        )
        return 1

    if device == "auto":
        try:
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"

    if list_only:
        print("Candidatos:")
        for candidate in CANDIDATES.values():
            print(f"  {candidate.name:22s} {candidate.description}")
        print("\nCheckpoints:")
        for name, path in resolved:
            print(f"  {name:26s} {path}")
        return 0

    print(f"Device: {device} | layers/checkpoint: {max_layers} | candidatos: {len(names)}")
    results: list[LayerResult] = []
    for name, shards in _group_checkpoints(resolved):
        label = ", ".join(str(path) for path in shards)
        print(f"\n== {name}: {label}")
        for candidate_name in names:
            candidate = CANDIDATES[candidate_name]
            print(f"   - {candidate_name} ...", end="", flush=True)
            try:
                partial = run_candidate_on_checkpoint(
                    name,
                    shards,
                    candidate,
                    max_layers=max_layers,
                    device=device,
                    seed=seed,
                )
            except Exception as exc:  # bench deve reportar e continuar
                print(f" falhou ({exc})")
                continue
            results.extend(partial)
            if partial:
                mean_bits = statistics.fmean(item.bits_per_weight for item in partial)
                mean_rel = statistics.fmean(item.weight_rel for item in partial)
                print(f" ok ({len(partial)} layers, {mean_bits:.2f} b/w, w_rel={mean_rel:.5f})")
            else:
                print(" sem camadas elegíveis")

    if not results:
        print("Sem resultados — nenhum tensor elegível (min 2-D, ≥16384 elementos).")
        return 1

    rows = aggregate(results)
    print("\n" + format_table(rows))

    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "device": device,
            "max_layers": max_layers,
            "seed": seed,
            "candidates": names,
            "aggregated": rows,
            "per_layer": [result.to_dict() for result in results],
        }
        output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nJSON: {output_json}")

    return 0
