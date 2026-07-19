from collections import Counter
from contextlib import contextmanager

import pytorch_lightning as pl
import torch
from hy3dshape.models.utils.misc import instantiate_from_config, instantiate_non_trainable_model
from pytorch_lightning.utilities import rank_zero_info, rank_zero_only
from torch.optim import lr_scheduler


class Diffuser(pl.LightningModule):
    def __init__(
        self,
        *,
        first_stage_config,
        cond_stage_config,
        denoiser_cfg,
        scheduler_cfg,
        optimizer_cfg,
        pipeline_cfg=None,
        image_processor_cfg=None,
        lora_config=None,
        init_cross_layer=False,
        ema_config=None,
        first_stage_key: str = "surface",
        cond_stage_key: str = "image",
        scale_by_std: bool = False,
        z_scale_factor: float = 1.0,
        ckpt_path: str | None = None,
        ignore_keys: tuple[str] | list[str] = [],
        torch_compile: bool = False,
        log_rms_norm_scale_every_n_steps: int = -1,
    ):
        super().__init__()
        self.first_stage_key = first_stage_key
        self.cond_stage_key = cond_stage_key

        # ========= init optimizer config ========= #
        self.optimizer_cfg = optimizer_cfg

        # ========= init diffusion scheduler ========= #
        self.scheduler_cfg = scheduler_cfg
        self.sampler = None
        if "transport" in scheduler_cfg:
            self.transport = instantiate_from_config(scheduler_cfg.transport)
            self.sampler = instantiate_from_config(scheduler_cfg.sampler, transport=self.transport)
            self.sample_fn = self.sampler.sample_ode(**scheduler_cfg.sampler.ode_params)

        # ========= init the model ========= #
        self.denoiser_cfg = denoiser_cfg
        self.model = instantiate_from_config(denoiser_cfg, device=None, dtype=None)
        self.cond_stage_model = instantiate_from_config(cond_stage_config)

        self.ckpt_path = ckpt_path
        if init_cross_layer:
            # set trainable model
            import re

            print("training model setting")
            for k, p in self.named_parameters():
                # print(k)
                if re.match(r".*blocks\.\d+\.attn2.*", k):
                    # nn.init.constant_(k, 0)
                    print(f"ignore key:{k}")
                    ignore_keys.append(k)

        if ckpt_path is not None:
            self.init_from_ckpt(ckpt_path, ignore_keys=ignore_keys)

        # ========= config lora model ========= #
        if lora_config is not None:
            from peft import LoraConfig, get_peft_model

            print([(n, type(m)) for n, m in self.model.named_modules()])
            loraconfig = LoraConfig(
                r=lora_config.rank, lora_alpha=lora_config.rank, target_modules=lora_config.get("target_modules")
            )
            self.model = get_peft_model(self.model, loraconfig)
            self.model.print_trainable_parameters()

        # ========= init vae at last to prevent it is overridden by loaded ckpt ========= #
        self.first_stage_model = instantiate_non_trainable_model(first_stage_config)
        self.first_stage_model.set_shape_model_only()
        self.scale_by_std = scale_by_std
        if scale_by_std:
            self.register_buffer("z_scale_factor", torch.tensor(z_scale_factor))
        else:
            self.z_scale_factor = z_scale_factor

        # ========= init pipeline for inference ========= #
        self.image_processor_cfg = image_processor_cfg
        self.image_processor = None
        if self.image_processor_cfg is not None:
            self.image_processor = instantiate_from_config(self.image_processor_cfg)
        self.pipeline_cfg = pipeline_cfg
        self.pipeline = instantiate_from_config(
            pipeline_cfg,
            vae=self.first_stage_model,
            model=self.model,
            scheduler=self.sampler,
            cond_encoder=self.cond_stage_model,
            image_processor=self.image_processor,
        )

        # ========= torch compile to accelerate ========= #
        self.torch_compile = torch_compile
        if self.torch_compile:
            torch.nn.Module.compile(self.model)
            torch.nn.Module.compile(self.first_stage_model)
            torch.nn.Module.compile(self.cond_stage_model)
            print("*" * 100)
            print("Compile model for acceleration")
            print("*" * 100)

        # ========= log rms norm ========= #
        self.log_rms_norm_scale_every_n_steps = log_rms_norm_scale_every_n_steps
        if self.log_rms_norm_scale_every_n_steps > 0:
            print(f"Log RMS Norm Scale every {self.log_rms_norm_scale_every_n_steps} steps")

    @contextmanager
    def ema_scope(self, context=None):
        if self.ema_config is not None and self.ema_config.get("ema_inference", False):
            self.model_ema.store(self.model)
            self.model_ema.copy_to(self.model)
            if context is not None:
                print(f"{context}: Switched to EMA weights")
        try:
            yield None
        finally:
            if self.ema_config is not None and self.ema_config.get("ema_inference", False):
                self.model_ema.restore(self.model)
                if context is not None:
                    print(f"{context}: Restored training weights")

    def init_from_ckpt(self, path, ignore_keys=()):
        try:
            ckpt = torch.load(path, map_location="cpu", weights_only=True)
        except Exception:
            ckpt = torch.load(path, map_location="cpu", weights_only=False)
        if "state_dict" not in ckpt:
            # deepspeed ckpt
            state_dict = {}
            for k in ckpt.keys():
                new_k = k.replace("_forward_module.", "")
                state_dict[new_k] = ckpt[k]
        else:
            state_dict = ckpt["state_dict"]

        keys = list(state_dict.keys())
        for k in keys:
            # print(f"k:{k}")
            for ik in ignore_keys:
                # print(f"ik:{ik}")
                if ik in k:
                    print(f"Deleting key {k} from state_dict.")
                    del state_dict[k]

        missing, unexpected = self.load_state_dict(state_dict, strict=False)
        print(f"Restored from {path} with {len(missing)} missing and {len(unexpected)} unexpected keys")
        if len(missing) > 0:
            # print(f"Missing Keys: {missing}")
            print(f"Missing Keys: {Counter([s.split('.')[0] for s in missing])}")
        if len(unexpected) > 0:
            # print(f"Unexpected Keys: {unexpected}")
            print(f"Unexpected Keys: {Counter([s.split('.')[0] for s in unexpected])}")

    def on_load_checkpoint(self, checkpoint):
        """
        The pt_model is trained separately, so we already have access to its
        checkpoint and load it separately with `self.set_pt_model`.

        However, the PL Trainer is strict about
        checkpoint loading (not configurable), so it expects the loaded state_dict
        to match exactly the keys in the model state_dict.

        So, when loading the checkpoint, before matching keys, we add all pt_model keys
        from self.state_dict() to the checkpoint state dict, so that they match
        """
        for key in self.state_dict().keys():
            if key.startswith("model_ema") and key not in checkpoint["state_dict"]:
                checkpoint["state_dict"][key] = self.state_dict()[key]

    def configure_optimizers(self) -> tuple[list, list]:
        lr = self.learning_rate

        params_list = []
        trainable_parameters = list(self.model.parameters())
        params_list.append({"params": trainable_parameters, "lr": lr})

        # trainable_parameters = list(self.cond_stage_model.point_encoder.parameters())
        # params_list.append({'params': trainable_parameters, 'lr': lr})

        no_decay = ["bias", "norm.weight", "norm.bias", "norm1.weight", "norm1.bias", "norm2.weight", "norm2.bias"]

        if self.optimizer_cfg.get("train_image_encoder", False):
            image_encoder_parameters = list(self.cond_stage_model.named_parameters())
            image_encoder_parameters_decay = [
                param
                for name, param in image_encoder_parameters
                if not any((no_decay_name in name) for no_decay_name in no_decay)
            ]
            image_encoder_parameters_nodecay = [
                param
                for name, param in image_encoder_parameters
                if any((no_decay_name in name) for no_decay_name in no_decay)
            ]
            # filter trainable params
            image_encoder_parameters_decay = [param for param in image_encoder_parameters_decay if param.requires_grad]
            image_encoder_parameters_nodecay = [
                param for param in image_encoder_parameters_nodecay if param.requires_grad
            ]

            print(f"trainable parameters with decay: {image_encoder_parameters_decay}")
            print(f"trainable parameters without decay: {image_encoder_parameters_nodecay}")
            print(f"Image Encoder Params: {len(image_encoder_parameters_decay)} decay, ")
            print(f"Image Encoder Params: {len(image_encoder_parameters_nodecay)} nodecay, ")
            # exit(0)

            image_encoder_lr = self.optimizer_cfg["image_encoder_lr"]
            image_encoder_lr_multiply = self.optimizer_cfg.get("image_encoder_lr_multiply", 1.0)
            image_encoder_lr = image_encoder_lr if image_encoder_lr is not None else lr * image_encoder_lr_multiply
            params_list.append({"params": image_encoder_parameters_decay, "lr": image_encoder_lr, "weight_decay": 0.05})
            params_list.append(
                {"params": image_encoder_parameters_nodecay, "lr": image_encoder_lr, "weight_decay": 0.0}
            )

        optimizer = instantiate_from_config(self.optimizer_cfg.optimizer, params=params_list, lr=lr)
        if hasattr(self.optimizer_cfg, "scheduler"):
            scheduler_func = instantiate_from_config(
                self.optimizer_cfg.scheduler, max_decay_steps=self.trainer.max_steps, lr_max=lr
            )
            scheduler = {
                "scheduler": lr_scheduler.LambdaLR(optimizer, lr_lambda=scheduler_func.schedule),
                "interval": "step",
                "frequency": 1,
            }
            schedulers = [scheduler]
        else:
            schedulers = []
        optimizers = [optimizer]

        return optimizers, schedulers

    @rank_zero_only
    @torch.no_grad()
    def on_train_batch_start(self, batch, batch_idx):
        # only for very first batch
        if (
            self.scale_by_std
            and self.current_epoch == 0
            and self.global_step == 0
            and batch_idx == 0
            and self.ckpt_path is None
        ):
            # set rescale weight to 1./std of encodings
            print("### USING STD-RESCALING ###")

            z_q = self.encode_first_stage(batch[self.first_stage_key])
            z = z_q.detach()

            del self.z_scale_factor
            self.register_buffer("z_scale_factor", 1.0 / z.flatten().std())
            print(f"setting self.z_scale_factor to {self.z_scale_factor}")

            print("### USING STD-RESCALING ###")

        if (
            self.scaleup_cfg is not None
            and "scale_up_warm_up_steps" in self.scaleup_cfg
            and self.global_step == self.scaleup_cfg.scale_up_warm_up_steps
        ):
            self.model.requires_grad_(True)

    def on_train_batch_end(self, *args, **kwargs):
        if self.ema_config is not None:
            self.model_ema(self.model)

    def on_train_epoch_start(self) -> None:
        pl.seed_everything(self.trainer.global_rank)

    def forward(self, batch):
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            pose = batch.get("pose", None)
            bbox = batch.get("bbox", None)
            contexts = self.cond_stage_model(
                image=batch.get("image"), surface=batch.get("surface"), pose=pose, bbox=bbox
            )
        with torch.autocast(device_type="cuda", dtype=torch.float16), torch.no_grad():
            latents = self.first_stage_model.encode(batch[self.first_stage_key], sample_posterior=True)
            latents = self.z_scale_factor * latents
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            loss = self.transport.training_losses(self.model, latents, dict(contexts=contexts))["loss"].mean()
        return loss

    def training_step(self, batch, batch_idx, optimizer_idx=0):
        loss = self.forward(batch)
        split = "train"
        loss_dict = {
            f"{split}/simple": loss.detach(),
            f"{split}/total_loss": loss.detach(),
            f"{split}/lr_abs": self.optimizers().param_groups[0]["lr"],
        }
        self.log_dict(loss_dict, prog_bar=True, logger=True, sync_dist=False, rank_zero_only=True)

        if self.log_rms_norm_scale_every_n_steps > 0:
            if self.global_step % self.log_rms_norm_scale_every_n_steps == 0 and self.trainer.is_global_zero:
                self.log_rms_norm_scale("train")

        return loss

    def validation_step(self, batch, batch_idx, optimizer_idx=0):
        loss = self.forward(batch)
        split = "val"
        loss_dict = {
            f"{split}/simple": loss.detach(),
            f"{split}/total_loss": loss.detach(),
            f"{split}/lr_abs": self.optimizers().param_groups[0]["lr"],
        }
        self.log_dict(loss_dict, prog_bar=True, logger=True, sync_dist=False, rank_zero_only=True)

        if self.trainer.is_global_zero:
            self.log_rms_norm_scale("val")

        return loss

    def log_rms_norm_scale(self, phase=None):
        assert phase in ["train", "val"], "phase should be 'train' or 'val'"
        max_query_scale, max_key_scale = float("-inf"), float("-inf")
        for name, module in self.model.named_modules():
            if "query_norm" in name and max_query_scale < module.scale.max().item():
                max_query_scale = module.scale.max().item()
            if "key_norm" in name and max_key_scale < module.scale.max().item():
                max_key_scale = module.scale.max().item()
        if max_query_scale > float("-inf") and max_key_scale > float("-inf"):
            self.log_dict(
                {f"{phase}/query_scale": max_query_scale, f"{phase}/key_scale": max_key_scale},
                prog_bar=True,
                logger=True,
                sync_dist=False,
                rank_zero_only=True,
            )
        rank_zero_info(f"RMS Norm Scale: {phase} query_scale: {max_query_scale} key_scale: {max_key_scale}")

    @torch.no_grad()
    def sample(self, batch, **kwargs):
        self.cond_stage_model.disable_drop = True

        generator = None
        if "seed" in self.pipeline_cfg.params:
            generator = torch.Generator().manual_seed(self.pipeline_cfg.params.seed)

        with self.ema_scope("Sample"), torch.amp.autocast(device_type="cuda"):
            try:
                self.pipeline.device = self.device
                self.pipeline.dtype = self.dtype
                print("### USING PIPELINE ###")
                print(f"device: {self.device} dtype : {self.dtype}")
                additional_params = self.pipeline_cfg.params
                additional_params.update(kwargs)
                image = batch["image"]
                mask = batch["mask"]
                surface = batch["surface"]
                pose = batch.get("pose", None)
                bbox = batch.get("bbox", None)
                outputs = self.pipeline(
                    image=image,
                    mask=mask,
                    surface=surface,
                    pose=pose,
                    bbox=bbox,
                    generator=generator,
                    **additional_params,
                )
            except Exception:
                import traceback

                traceback.print_exc()
                outputs = [None]
        self.cond_stage_model.disable_drop = False
        return outputs
