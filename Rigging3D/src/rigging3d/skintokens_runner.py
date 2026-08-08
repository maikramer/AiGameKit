"""In-process rigging via SkinTokens (sucessor do UniRig, VAST-AI-Research).

Substitui ``oneshot.py`` (UniRig): um único modelo autoregressivo unificado
gera skeleton + skin numa única chamada, e a exportação/transferência bpy
corre no mesmo processo — sem o servidor HTTP ``bpy_server.py`` do repo
original (esse existe lá só como workaround para dataloaders multi-worker;
não é necessário aqui porque processamos um item de cada vez, in-process,
no mesmo padrão já usado por ``transfer_weights.py``).

Ver ``docs/RIGGING3D_SKINTOKENS_MIGRATION_PLAN.md`` no root do monorepo.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_PACKAGE_ROOT = Path(__file__).resolve().parent / "skintokens"

DEFAULT_CKPT_REPO = "VAST-AI/SkinTokens"
DEFAULT_TOKENRIG_CKPT = "experiments/articulation_xl_quantization_256_token_4/grpo_1400.ckpt"
DEFAULT_SKIN_VAE_CKPT = "experiments/skin_vae_2_10_32768/last.ckpt"

_model: Any = None
_tokenizer: Any = None
_transform: Any = None
_loaded_ckpt: str | None = None


def _checkpoints_home() -> Path:
    """Raiz onde os checkpoints (baixados do HF Hub) ficam com o layout
    relativo que o TokenRig espera (``experiments/<nome>/<ficheiro>.ckpt``).

    O config gravado dentro do ``.ckpt`` do TokenRig referencia o VAE por um
    caminho relativo (``cfg["pretrained_vae"]``) — por isso o carregamento
    precisa correr com ``cwd`` neste diretório (ver ``_load_model``).
    """
    env = os.environ.get("RIGGING3D_SKINTOKENS_HOME", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".cache" / "rigging3d" / "skintokens"


def ensure_checkpoints(home: Path | None = None) -> Path:
    """Baixa (se preciso) os checkpoints TokenRig + FSQ-CVAE do HF Hub.

    Idempotente: ``hf_hub_download`` já faz cache local por hash de conteúdo.
    Também garante um symlink para ``configs/`` (vendored no pacote) dentro da
    raiz de checkpoints — o config gravado no ``.ckpt`` referencia
    ``configs/skeleton/*.yaml`` por caminho relativo ao cwd (ver
    ``Order.parse``), e o cwd durante o load é esta raiz, não o pacote.
    """
    from huggingface_hub import hf_hub_download

    root = home if home is not None else _checkpoints_home()
    root.mkdir(parents=True, exist_ok=True)
    for filename in (DEFAULT_TOKENRIG_CKPT, DEFAULT_SKIN_VAE_CKPT):
        local_path = root / filename
        if local_path.is_file():
            continue
        hf_hub_download(repo_id=DEFAULT_CKPT_REPO, filename=filename, local_dir=str(root))

    configs_link = root / "configs"
    vendored_configs = _PACKAGE_ROOT / "configs"
    # Symlink dangling (checkout mudou de path) faz ``exists()`` = False mas a
    # entrada já existe — re-apontar em vez de rebentar com FileExistsError.
    if configs_link.is_symlink():
        try:
            if configs_link.resolve() != vendored_configs.resolve():
                configs_link.unlink()
        except OSError:
            configs_link.unlink()
    if not configs_link.exists():
        configs_link.symlink_to(vendored_configs, target_is_directory=True)
    return root


def _ensure_sys_path() -> None:
    root_s = str(_PACKAGE_ROOT)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)


def _load_model(ckpt_path: str, device: str = "cuda") -> tuple[Any, Any, Any]:
    global _model, _tokenizer, _transform, _loaded_ckpt
    if _model is not None and ckpt_path == _loaded_ckpt:
        return _model, _tokenizer, _transform

    _ensure_sys_path()
    from src.data.transform import Transform
    from src.server.spec import get_model
    from src.tokenizer.parse import get_tokenizer

    model = get_model(ckpt_path, hf_path=None, device=device)
    assert model.tokenizer_config is not None
    tokenizer = get_tokenizer(**model.tokenizer_config)
    transform = Transform.parse(**model.transform_config["predict_transform"])

    _model, _tokenizer, _transform = model, tokenizer, transform
    _loaded_ckpt = ckpt_path
    return model, tokenizer, transform


def run_rig_inprocess(
    input_path: str,
    output_path: str,
    *,
    checkpoints_home: Path | None = None,
    tokenrig_ckpt: str = DEFAULT_TOKENRIG_CKPT,
    device: str = "cuda",
    use_skeleton: bool = False,
    use_transfer: bool = True,
    use_postprocess: bool = False,
    top_k: int = 5,
    top_p: float = 0.95,
    temperature: float = 1.0,
    repetition_penalty: float = 2.0,
    num_beams: int = 10,
    group_per_vertex: int = 4,
    seed: int = 123,
) -> None:
    """Gera rig (skeleton+skin, um único passo autoregressivo) para ``input_path``.

    Args:
        use_skeleton: Se True, ``input_path`` já tem skeleton (GLB do UniRig
            legado ou de uma corrida anterior) e só o skin é gerado.
        use_transfer: Reanexa o resultado à mesh/textura/escala originais —
            equivalente ao antigo ``rigging3d merge``. Recomendado sempre que
            o objetivo é um GLB final pronto a usar (mantém materiais/UVs).
        use_postprocess: Suavização de skin baseada em voxel (``open3d``, não
            é dependência do pacote — instalar manualmente se necessário).
    """
    root = ensure_checkpoints(checkpoints_home)
    orig_cwd = os.getcwd()
    try:
        os.chdir(str(root))
        model, tokenizer, transform = _load_model(str(root / tokenrig_ckpt), device=device)

        # Seed reprodutível: o TokenRig é estocástico (sampling + beam search).
        # Sem isto, runs sucessivos produzem topologias diferentes para o mesmo
        # mesh, dificultando a classificação pós-processo (rename de bones).
        import random

        import numpy as np
        import torch

        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)

        _ensure_sys_path()
        from src.data.dataset import DatasetConfig, RigDatasetModule
        from src.model.tokenrig import TokenRigResult
        from src.rig_package.parser.bpy import BpyParser, transfer_rigging
        from torch import Tensor

        input_abs = str(Path(input_path).resolve())
        output_abs = Path(output_path).resolve()

        datapath = {
            "data_name": None,
            "loader": "bpy",  # in-process (sem bpy_server/HTTP)
            "filepaths": {"articulation": [input_abs]},
        }
        dataset_config = DatasetConfig.parse(
            shuffle=False,
            batch_size=1,
            num_workers=0,
            pin_memory=True,
            persistent_workers=False,
            datapath=datapath,
        ).split_by_cls()

        module = RigDatasetModule(
            predict_dataset_config=dataset_config,
            predict_transform=transform,
            tokenizer=tokenizer,
            process_fn=model._process_fn,
        )
        dataloader = module.predict_dataloader()["articulation"]

        batch = next(iter(dataloader))
        batch = {k: v.to(device) if isinstance(v, Tensor) else v for k, v in batch.items()}

        if not use_skeleton:
            batch.pop("skeleton_tokens", None)
            batch.pop("skeleton_mask", None)

        batch["generate_kwargs"] = dict(
            max_length=2048,
            top_k=int(top_k),
            top_p=float(top_p),
            temperature=float(temperature),
            repetition_penalty=float(repetition_penalty),
            num_return_sequences=1,
            num_beams=int(num_beams),
            do_sample=True,
        )

        if "skeleton_tokens" in batch and "skeleton_mask" in batch:
            mask = batch["skeleton_mask"][0] == 1
            skeleton_tokens = batch["skeleton_tokens"][0][mask].cpu().numpy()
        else:
            skeleton_tokens = None

        preds: list[TokenRigResult] = model.predict_step(
            batch,
            skeleton_tokens=[skeleton_tokens] if skeleton_tokens is not None else None,
            make_asset=True,
        )["results"]

        asset = preds[0].asset
        assert asset is not None

        if use_postprocess:
            from src.data.vertex_group import voxel_skin

            voxel = asset.voxel(resolution=196)
            asset.skin *= voxel_skin(
                grid=0,
                grid_coords=voxel.coords,
                joints=asset.joints,
                vertices=asset.vertices,
                faces=asset.faces,
                mode="square",
                voxel_size=voxel.voxel_size,
            )
            asset.normalize_skin()

        output_abs.parent.mkdir(parents=True, exist_ok=True)
        if use_transfer:
            transfer_rigging(
                source_asset=asset,
                target_path=asset.path,
                export_path=str(output_abs),
                group_per_vertex=group_per_vertex,
            )
        else:
            BpyParser.export(asset, str(output_abs), group_per_vertex=group_per_vertex)
    finally:
        os.chdir(orig_cwd)
