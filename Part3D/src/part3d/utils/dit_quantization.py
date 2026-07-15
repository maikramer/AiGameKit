"""
Pré-quantização e carregamento do DiT (X-Part) com optimum-quanto (qint8 weight-only).

Os artefactos ficam junto ao snapshot HuggingFace do modelo::

    <model_dir>/model/model-dit-qint8.safetensors
    <model_dir>/model/model-dit-qint8-quantization_map.json

Isto reduz o footprint do DiT na VRAM em runtime (~metade vs FP16), permitindo
carregar o modelo na GPU em GPUs ~6 GB sem depender só de fallback a CPU.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import torch

from part3d.utils.transformers_pkg_mapping_fix import apply as _transformers_mapping_fix

# Nomes fixos (não mudar sem migrar caches existentes)
DIT_QUANT_SAFETENSORS = "model-dit-qint8.safetensors"
DIT_QUANT_MAP_JSON = "model-dit-qint8-quantization_map.json"


def dit_quantized_paths(model_dir: str | Path) -> tuple[Path, Path]:
    base = Path(model_dir) / "model"
    return base / DIT_QUANT_SAFETENSORS, base / DIT_QUANT_MAP_JSON


def quantized_dit_artifacts_exist(model_dir: str | Path) -> bool:
    st, jm = dit_quantized_paths(model_dir)
    return st.is_file() and st.stat().st_size > 0 and jm.is_file() and jm.stat().st_size > 0


def want_quantized_dit(device: str, model_dir: str | Path) -> bool:
    """Se deve tentar carregar DiT quantizado em runtime (auto + env)."""
    env = os.environ.get("PART3D_USE_QUANTIZED_DIT", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if not quantized_dit_artifacts_exist(model_dir):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    # auto: CUDA com VRAM total < 8 GB
    if device != "cuda":
        return False
    if not torch.cuda.is_available():
        return False
    try:
        vram_gb = float(torch.cuda.get_device_properties(0).total_memory) / (1024**3)
    except Exception:
        return False
    return vram_gb < 8.0


def load_dit_quantized(model: torch.nn.Module, model_dir: str | Path) -> bool:
    """Carrega pesos quantizados no ``model`` já instanciado (estrutura FP)."""
    _transformers_mapping_fix()
    st_path, map_path = dit_quantized_paths(model_dir)
    if not st_path.is_file() or not map_path.is_file():
        return False
    # Import directo (evita ``optimum.quanto`` package __init__ → transformers/flash_attn).
    from optimum.quanto.quantize import requantize
    from safetensors.torch import load_file

    with open(map_path, encoding="utf-8") as f:
        qmap: dict[str, Any] = json.load(f)
    state_dict = load_file(str(st_path), device="cpu")
    requantize(model, state_dict, qmap, device=torch.device("cpu"))
    model.eval()
    return True


def quantize_and_save_dit(
    *,
    repo_id: str = "tencent/Hunyuan3D-Part",
    force: bool = False,
    log: Any | None = None,
) -> bool:
    """
    Descarrega o modelo HF (se necessário), quantiza o DiT em CPU e grava artefactos.

    Returns:
        True se os ficheiros quantizados existem no fim (novos ou já presentes).
    """
    _transformers_mapping_fix()

    from easydict import EasyDict
    from huggingface_hub import snapshot_download
    from safetensors.torch import load_file, save_file

    def _log(msg: str) -> None:
        if log is not None:
            log(msg)
        else:
            print(f"[quantize_dit] {msg}", flush=True)

    model_dir = snapshot_download(repo_id=repo_id, repo_type="model")
    st_out, map_out = dit_quantized_paths(model_dir)
    # Interrupção entre safetensors e JSON deixa só metade — runtime ignora quantizado.
    if (st_out.is_file() or map_out.is_file()) and not quantized_dit_artifacts_exist(model_dir):
        _log("Artefactos incompletos — a remover antes de regenerar.")
        st_out.unlink(missing_ok=True)
        map_out.unlink(missing_ok=True)
    if quantized_dit_artifacts_exist(model_dir) and not force:
        _log(f"Artefactos já existem em {st_out.parent}; usa --force para regenerar.")
        return True

    st_out.parent.mkdir(parents=True, exist_ok=True)

    # Importa código X-Part (Space) — mesmo caminho que o pipeline
    from part3d.pipeline import _setup_xpart_imports

    _setup_xpart_imports(model_dir)

    from optimum.quanto.quantize import freeze, quantization_map, quantize
    from optimum.quanto.tensor.qtype import qint8
    from partgen.utils.misc import instantiate_from_config

    cfg_path = Path(model_dir) / "model" / "config.json"
    with open(cfg_path, encoding="utf-8") as f:
        model_cfg = json.load(f)
    _log("A instanciar DiT e carregar FP16 (CPU)...")
    dit = instantiate_from_config(EasyDict(model_cfg))
    ckpt = load_file(str(Path(model_dir) / "model" / "model.safetensors"), device="cpu")
    dit.load_state_dict(ckpt)
    del ckpt
    dit.to(dtype=torch.float16)
    dit.eval()

    _log("A quantizar pesos (qint8 weight-only)...")
    quantize(dit, weights=qint8, activations=None)
    freeze(dit)
    qmap = quantization_map(dit)

    _log(f"A gravar {st_out.name} ({len(qmap)} módulos quantizados)...")
    try:
        save_file(dit.state_dict(), str(st_out))
        with open(map_out, "w", encoding="utf-8") as f:
            json.dump(qmap, f, indent=2)
    except Exception as e:
        _log(f"Erro ao gravar artefactos quantizados: {e}")
        for p in (st_out, map_out):
            if p.is_file():
                p.unlink(missing_ok=True)
        return False

    dit.cpu()
    del dit
    torch.cuda.empty_cache() if torch.cuda.is_available() else None
    _log("Concluído.")
    return quantized_dit_artifacts_exist(model_dir)
