"""
Tencent is pleased to support the open source community by making Tencent Hunyuan 3D Omni available.

Copyright (C) 2025 Tencent.  All rights reserved. The below software and/or models in this
distribution may have been modified by Tencent ("Tencent Modifications"). All Tencent Modifications
are Copyright (C) Tencent.

Tencent Hunyuan 3D Omni is licensed under the TENCENT HUNYUAN 3D OMNI COMMUNITY LICENSE AGREEMENT
except for the third-party components listed below, which is licensed under different terms.
Tencent Hunyuan 3D Omni does not impose any additional limitations beyond what is outlined in the
respective licenses of these third-party components. Users must comply with all terms and conditions
of original licenses of these third-party components and must ensure that the usage of the third party
components adheres to all relevant laws and regulations.

For avoidance of doubts, Tencent Hunyuan 3D Omni means training code, inference-enabling code, parameters,
and/or weights of this Model, which are made publicly available by Tencent in accordance with TENCENT
HUNYUAN 3D OMNI COMMUNITY LICENSE AGREEMENT.
"""

import logging

import torch
import trimesh

from ..models.utils import synchronize_timer


def get_logger(name):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    return logger


logger = get_logger("miche_dev")


@synchronize_timer("Export to trimesh")
def export_to_trimesh(mesh_output):
    if isinstance(mesh_output, list):
        outputs = []
        for mesh in mesh_output:
            if mesh is None:
                outputs.append(None)
            else:
                mesh.mesh_f = mesh.mesh_f[:, ::-1]
                mesh_output = trimesh.Trimesh(mesh.mesh_v, mesh.mesh_f)
                outputs.append(mesh_output)
        return outputs
    else:
        mesh_output.mesh_f = mesh_output.mesh_f[:, ::-1]
        mesh_output = trimesh.Trimesh(mesh_output.mesh_v, mesh_output.mesh_f)
        return mesh_output


def init_from_ckpt(model, ckpt_path, use_ema=False):
    print("loading", ckpt_path)
    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=True)
    print("load done")
    if "state_dict" not in ckpt:
        # deepspeed ckpt
        state_dict = {}
        for k in ckpt.keys():
            new_k = k.replace("_forward_module.", "")
            state_dict[new_k] = ckpt[k]
    else:
        state_dict = ckpt["state_dict"]

    if use_ema:
        final_state_dict = {}
        for k, v in state_dict.items():
            if k.startswith("model_ema."):
                final_state_dict[k.replace("model_ema.", "").replace("_____", ".")] = v
    else:
        final_state_dict = {}
        for k, v in state_dict.items():
            if k.startswith("model."):
                final_state_dict[k.replace("model.", "")] = v
            else:
                final_state_dict[k] = v

    missing, unexpected = model.load_state_dict(final_state_dict, strict=False)
    print("unexpected keys:", unexpected)
    print("missing keys:", missing)
    return model
