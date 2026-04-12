from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

JobType = Literal["text2d", "text3d", "skymap2d", "texture2d"]


class Text2DParams(BaseModel):
    prompt: str
    width: int = Field(default=1024, ge=64, le=4096)
    height: int = Field(default=1024, ge=64, le=4096)
    steps: int = Field(default=4, ge=1, le=100)
    guidance_scale: float = Field(default=1.0, ge=0.0, le=30.0)
    seed: int | None = None
    cpu: bool = False
    low_vram: bool = False
    model_id: str | None = None
    output_basename: str = "output"


class Text3DParams(BaseModel):
    prompt: str | None = None
    """Obrigatório se ``from_image_base64`` for None."""

    from_image_base64: str | None = None
    """PNG/JPEG em base64 (sem prefixo data: opcional)."""

    output_format: Literal["glb", "ply", "obj"] = "glb"
    cpu: bool = False
    low_vram: bool = False
    preset: Literal["fast", "balanced", "hq"] | None = None

    image_width: int = Field(default=768, ge=64, le=2048)
    image_height: int = Field(default=768, ge=64, le=2048)
    t2d_steps: int = Field(default=8, ge=1, le=100)
    t2d_guidance: float = Field(default=1.0, ge=0.0, le=30.0)
    text2d_model_id: str | None = None
    t2d_full_gpu: bool = False
    seed: int | None = None

    num_inference_steps: int | None = Field(default=None, ge=1, le=100)
    guidance_scale: float | None = Field(default=None, ge=0.0, le=30.0)
    octree_resolution: int | None = Field(default=None, ge=32, le=512)
    num_chunks: int | None = Field(default=None, ge=256, le=100000)
    mc_level: float = Field(default=0.0)

    max_retries: int = Field(default=1, ge=1, le=10)
    save_reference_image: bool = False
    optimize_prompt: bool = True

    export_origin: Literal["feet", "center", "none"] | None = None
    export_rotation_x_deg: float | None = None

    no_mesh_repair: bool = False
    no_ground_shadow_removal: bool = False
    ground_shadow_aggressive: bool = False
    ground_shadow_very_aggressive: bool = False
    mesh_smooth: int = Field(default=0, ge=0, le=5)
    remesh: bool = True
    remesh_resolution: int = Field(default=180, ge=10, le=500)
    remesh_iterations: int = Field(default=6, ge=1, le=30)
    remesh_max_surf_dist_factor: float = Field(default=0.38, gt=0.0, le=1.0)

    model_subfolder: str | None = None
    hunyuan_model_id: str | None = None

    output_basename: str = "mesh"

    @model_validator(mode="after")
    def _prompt_or_image(self) -> Text3DParams:
        has_prompt = self.prompt is not None and str(self.prompt).strip() != ""
        has_img = bool(self.from_image_base64 and str(self.from_image_base64).strip())
        if not has_prompt and not has_img:
            raise ValueError("Fornece prompt ou from_image_base64")
        return self


class Skymap2DParams(BaseModel):
    prompt: str
    width: int = Field(default=2048, ge=256, le=4096)
    height: int = Field(default=1024, ge=256, le=4096)
    steps: int = Field(default=40, ge=1, le=150)
    guidance_scale: float = Field(default=6.0, ge=0.0, le=30.0)
    seed: int | None = None
    negative_prompt: str = ""
    preset: str | None = None
    cfg_scale: float | None = None
    lora_strength: float = Field(default=1.0, ge=0.0, le=2.0)
    model_id: str | None = None
    image_format: Literal["png", "exr"] = "png"
    exr_scale: float = Field(default=1.0, gt=0.0)
    output_basename: str = "skymap"


class Texture2DParams(BaseModel):
    prompt: str
    width: int = Field(default=1024, ge=256, le=2048)
    height: int = Field(default=1024, ge=256, le=2048)
    steps: int = Field(default=50, ge=1, le=150)
    guidance_scale: float = Field(default=7.5, ge=0.0, le=30.0)
    seed: int | None = None
    negative_prompt: str = ""
    preset: str | None = None
    cfg_scale: float | None = None
    lora_strength: float = Field(default=1.0, ge=0.0, le=2.0)
    model_id: str | None = None
    output_basename: str = "texture"


class CreateJobRequest(BaseModel):
    type: JobType
    params: dict[str, Any]


class CreateJobResponse(BaseModel):
    job_id: str
    status: Literal["queued"] = "queued"


class JobStatusResponse(BaseModel):
    id: str
    type: JobType
    status: Literal["queued", "running", "succeeded", "failed"]
    error: str | None = None
    created_at: float
    updated_at: float
    started_at: float | None = None
    completed_at: float | None = None
    params: dict[str, Any]


class ArtifactsResponse(BaseModel):
    files: list[str]


class VersionResponse(BaseModel):
    inference_server_version: str
    job_types: list[str]
    optional_pipelines_installed: bool
