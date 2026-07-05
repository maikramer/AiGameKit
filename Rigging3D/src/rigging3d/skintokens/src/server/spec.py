import io
from dataclasses import dataclass

import torch
from torch import Tensor

from ..model.tokenrig import TokenRig
from ..rig_package.info.asset import Asset

PORT = 59875
SERVER = f"http://localhost:{PORT}"
TMP_CKPT_DIR = "./tmp_ckpt"

BPY_PORT = 59876
BPY_SERVER = f"http://localhost:{BPY_PORT}"


@dataclass
class TensorPacket:
    """make sure stays on cpu"""

    validate: bool = False
    know_skeleton: bool = False
    learned_mesh_cond: Tensor | None = None
    cond_latents: Tensor | None = None
    mesh_cond: Tensor | None = None
    vertices: Tensor | None = None
    assets: list[Asset] | None = None
    output_ids: Tensor | None = None
    start_embed_list: list[Tensor] | None = None
    start_tokens_list: list[list[int]] | None = None

    def to_device(self, device):
        if self.learned_mesh_cond is not None:
            self.learned_mesh_cond = self.learned_mesh_cond.to(device)
        if self.cond_latents is not None:
            self.cond_latents = self.cond_latents.to(device)
        if self.mesh_cond is not None:
            self.mesh_cond = self.mesh_cond.to(device)
        if self.vertices is not None:
            self.vertices = self.vertices.to(device)
        if self.output_ids is not None:
            self.output_ids = self.output_ids.to(device)
        if self.start_embed_list is not None:
            self.start_embed_list = [x.to(device) for x in self.start_embed_list]

    @property
    def B(self):
        assert self.learned_mesh_cond is not None
        return self.learned_mesh_cond.shape[0]

    def to_bytes(self):
        return object_to_bytes(self)

    @classmethod
    def from_bytes(cls, bytes) -> "TensorPacket":
        return bytes_to_object(bytes)


def object_to_bytes(t):
    buffer = io.BytesIO()
    torch.save(t, buffer)
    return buffer.getvalue()


def bytes_to_object(b, map_location=None):
    return torch.load(io.BytesIO(b), weights_only=False, map_location=map_location)


def get_model(
    ckpt_path: str,
    hf_path: str | None = None,
    device="cuda",
) -> TokenRig:
    model = TokenRig.load_from_system_checkpoint(checkpoint_path=ckpt_path)
    if hf_path is not None:
        from transformers import AutoModel

        a = AutoModel.from_pretrained(
            hf_path,
            local_files_only=True,
            _attn_implementation="sdpa",
            dtype=torch.bfloat16,
        )
        model.transformer.model.load_state_dict(a.state_dict())

    model = model.to(device)
    return model
