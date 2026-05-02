"""Entrypoint de inferência (equivalente ao run.py upstream).

Invocado pelos scripts Bash em launch/inference/ com cwd nesta pasta
e PYTHONPATH a incluir esta raiz (para imports ``from src.*``).
"""

from __future__ import annotations

import os
import platform
import warnings

# Suprimir warnings de depreciação de libs externas (timm, lightning, torch)
warnings.filterwarnings("ignore", message="Importing from timm.models.layers")
warnings.filterwarnings("ignore", message="`isinstance.*LeafSpec.*deprecated")
warnings.filterwarnings("ignore", message="Implicit dimension choice for softmax")
warnings.filterwarnings("ignore", message=".*tensorboardX.*removed.*")
warnings.filterwarnings("ignore", message=".*litlogger.*")
warnings.filterwarnings("ignore", message=".*litmodels.*")
warnings.filterwarnings("ignore", message=".*bottleneck.*")
warnings.filterwarnings("ignore", message=".*callbacks used to create.*")
warnings.filterwarnings("ignore", message="To copy construct from a tensor.*")
warnings.filterwarnings("ignore", category=FutureWarning, module="torch")
# Silenciar tips do lightning (litlogger/litmodels) e warnings de tracing do torch
os.environ.setdefault("LIGHTNING_LOGGING_LEVEL", "ERROR")
import logging

logging.getLogger("lightning").setLevel(logging.ERROR)
logging.getLogger("lightning.pytorch").setLevel(logging.ERROR)
logging.getLogger("lightning_fabric").setLevel(logging.ERROR)

if platform.system() == "Linux":
    os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
    os.environ.setdefault("__NV_PRIME_RENDER_OFFLOAD", "1")
    os.environ.setdefault("__GLX_VENDOR_LIBRARY_NAME", "nvidia")

import argparse
from math import ceil

import lightning as L
import torch
import yaml
from box import Box
from lightning.pytorch.callbacks import ModelCheckpoint
from lightning.pytorch.loggers import WandbLogger
from lightning.pytorch.strategies import FSDPStrategy
from src.data.datapath import Datapath
from src.data.dataset import DatasetConfig, UniRigDatasetModule
from src.data.extract import get_files
from src.data.transform import TransformConfig
from src.inference.download import download
from src.model.parse import get_model
from src.system.parse import get_system, get_writer
from src.tokenizer.parse import get_tokenizer
from src.tokenizer.spec import TokenizerConfig


def _load_yaml(label: str, path: str) -> Box:
    if path.endswith(".yaml"):
        path = path.removesuffix(".yaml")
    path += ".yaml"
    print(f"\033[92mload {label} config: {path}\033[0m")
    with open(path, encoding="utf-8") as f:
        return Box(yaml.safe_load(f))


def _nullable(val: str | None) -> str | None:
    return val or None


def main() -> None:
    torch.set_float32_matmul_precision("high")

    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--seed", type=int, default=123)
    ap.add_argument("--input", type=_nullable, default=None)
    ap.add_argument("--input_dir", type=_nullable, default=None)
    ap.add_argument("--output", type=_nullable, default=None)
    ap.add_argument("--output_dir", type=_nullable, default=None)
    ap.add_argument("--npz_dir", type=_nullable, default="tmp")
    ap.add_argument("--cls", type=_nullable, default=None)
    ap.add_argument("--data_name", type=_nullable, default=None)
    args = ap.parse_args()

    L.seed_everything(args.seed, workers=True)

    task = _load_yaml("task", args.task)
    mode = task.mode
    assert mode in ("predict",), f"só inferência é suportada, modo={mode}"

    if args.input is not None or args.input_dir is not None:
        assert args.output_dir is not None or args.output is not None
        assert args.npz_dir is not None
        files = get_files(
            data_name=task.components.data_name,
            inputs=args.input,
            input_dataset_dir=args.input_dir,
            output_dataset_dir=args.npz_dir,
            force_override=True,
            warning=False,
        )
        files = [f[1] for f in files]
        datapath = Datapath(files=files, cls=args.cls)
    else:
        datapath = None

    data_config = _load_yaml("data", os.path.join("configs/data", task.components.data))
    transform_config = _load_yaml("transform", os.path.join("configs/transform", task.components.transform))

    tokenizer_config = task.components.get("tokenizer", None)
    if tokenizer_config is not None:
        tokenizer_config = _load_yaml("tokenizer", os.path.join("configs/tokenizer", task.components.tokenizer))
        tokenizer_config = TokenizerConfig.parse(config=tokenizer_config)

    data_name = task.components.get("data_name", "raw_data.npz")
    if args.data_name is not None:
        data_name = args.data_name

    predict_dataset_config = data_config.get("predict_dataset_config", None)
    if predict_dataset_config is not None:
        predict_dataset_config = DatasetConfig.parse(config=predict_dataset_config).split_by_cls()

    predict_transform_config = transform_config.get("predict_transform_config", None)
    if predict_transform_config is not None:
        predict_transform_config = TransformConfig.parse(config=predict_transform_config)

    model_config = task.components.get("model", None)
    if model_config is not None:
        model_config = _load_yaml("model", os.path.join("configs/model", model_config))
        tokenizer = get_tokenizer(config=tokenizer_config) if tokenizer_config is not None else None
        model = get_model(tokenizer=tokenizer, **model_config)
    else:
        model = None

    data = UniRigDatasetModule(
        process_fn=None if model is None else model._process_fn,
        train_dataset_config=None,
        predict_dataset_config=predict_dataset_config,
        predict_transform_config=predict_transform_config,
        validate_dataset_config=None,
        train_transform_config=None,
        validate_transform_config=None,
        tokenizer_config=tokenizer_config,
        debug=False,
        data_name=data_name,
        datapath=datapath,
        cls=args.cls,
    )

    callbacks = []
    writer_config = task.get("writer", None)
    if writer_config is not None:
        assert predict_transform_config is not None
        if args.output_dir is not None or args.output is not None:
            if args.output is not None:
                assert args.output.lower().endswith((".fbx", ".glb", ".gltf")), (
                    "output must be .fbx, .glb ou .gltf"
                )
            writer_config["npz_dir"] = args.npz_dir
            writer_config["output_dir"] = args.output_dir
            writer_config["output_name"] = args.output
            writer_config["user_mode"] = True
        callbacks.append(get_writer(**writer_config, order_config=predict_transform_config.order_config))

    trainer_config = task.get("trainer", {})

    system_config = task.components.get("system", None)
    if system_config is not None:
        system_config = _load_yaml("system", os.path.join("configs/system", system_config))
        system = get_system(
            **system_config,
            model=model,
            optimizer_config=None,
            loss_config=None,
            scheduler_config=None,
            steps_per_epoch=1,
        )
    else:
        system = None

    ckpt = task.get("resume_from_checkpoint", None)
    ckpt = download(ckpt)

    if trainer_config.get("strategy", None) == "fsdp":
        trainer_config["strategy"] = FSDPStrategy(
            auto_wrap_policy={torch.nn.MultiheadAttention},
            activation_checkpointing_policy={torch.nn.Linear, torch.nn.MultiheadAttention},
        )

    trainer = L.Trainer(callbacks=callbacks, logger=None, **trainer_config)
    assert ckpt is not None, "resume_from_checkpoint necessário para inferência"
    # PyTorch ≥2.6: checkpoints UniRig/HF incluem hyperparams serializados (box.Box, etc.).
    trainer.predict(
        system,
        datamodule=data,
        ckpt_path=ckpt,
        return_predictions=False,
        weights_only=False,
    )


if __name__ == "__main__":
    main()
