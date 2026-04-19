"""Compare model_index.json from two HuggingFace Diffusers models to confirm pipeline compatibility.

Downloads ONLY model_index.json + transformer/config.json (no weights) from both
repos and checks whether they share the same diffusers pipeline class (_class_name).

Usage:
    python scripts/validate_9b.py
"""

from __future__ import annotations

import json
import sys

from huggingface_hub import hf_hub_download

MODEL_CURRENT = "Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic"
MODEL_NEW = "Disty0/FLUX.2-klein-9B-SDNQ-4bit-dynamic-svd-r32"

INDEX_FIELDS = ["_class_name", "_diffusers_version", "_name_or_path"]
TRANSFORMER_FIELDS = ["num_attention_heads", "num_layers", "hidden_size", "in_channels", "patch_size"]


def _load_json(model_id: str, filename: str) -> dict:
    path = hf_hub_download(repo_id=model_id, filename=filename)
    with open(path) as f:
        return json.load(f)


def _pad(value: str, width: int = 45) -> str:
    return (value or "")[:width].ljust(width)


def _print_section(title: str, id_a: str, id_b: str, fields: list[str], cfg_a: dict, cfg_b: dict) -> None:
    print(f"\n=== {title} ===\n")
    print(f"{'Field':<28} {_pad(id_a)} {_pad(id_b)}")
    print("-" * (28 + 45 + 45))
    for key in fields:
        va = json.dumps(cfg_a.get(key)) if key in cfg_a else "<missing>"
        vb = json.dumps(cfg_b.get(key)) if key in cfg_b else "<missing>"
        marker = " *" if va != vb else ""
        print(f"{key:<28} {_pad(va)} {_pad(vb)}{marker}")

    extra = sorted(k for k in (set(cfg_a) | set(cfg_b)) if k not in fields and not k.startswith("_"))
    diffs = [k for k in extra if json.dumps(cfg_a.get(k), default=str) != json.dumps(cfg_b.get(k), default=str)]
    if diffs:
        print(f"\n  Differing extra fields: {', '.join(diffs)}")


def main() -> int:
    print(f"Fetching configs from:\n  A) {MODEL_CURRENT}\n  B) {MODEL_NEW}")

    idx_a = _load_json(MODEL_CURRENT, "model_index.json")
    idx_b = _load_json(MODEL_NEW, "model_index.json")
    print("  model_index.json ✓")

    trf_a = _load_json(MODEL_CURRENT, "transformer/config.json")
    trf_b = _load_json(MODEL_NEW, "transformer/config.json")
    print("  transformer/config.json ✓\n")

    _print_section("Pipeline index (model_index.json)", MODEL_CURRENT, MODEL_NEW, INDEX_FIELDS, idx_a, idx_b)
    _print_section("Transformer dimensions", MODEL_CURRENT, MODEL_NEW, TRANSFORMER_FIELDS, trf_a, trf_b)

    class_a = idx_a.get("_class_name", "")
    class_b = idx_b.get("_class_name", "")
    print()

    if class_a == class_b:
        print(f"COMPATIBLE — same pipeline class: {class_a}")
        return 0
    else:
        print(f"INCOMPATIBLE — pipeline classes differ:")
        print(f"  {MODEL_CURRENT}: {class_a}")
        print(f"  {MODEL_NEW}: {class_b}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
