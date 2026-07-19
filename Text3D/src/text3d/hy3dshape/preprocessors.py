# -*- coding: utf-8 -*-
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


"""
This module provides image preprocessing utilities,
including image normalization, recentering, and format conversion functions.

The main component is ImageProcessorV2, which handles:
- Image resizing and padding
- Background removal and recentering
- Format conversion between PIL, numpy, and PyTorch tensors
- Normalization for model input
"""

import PIL.Image
import cv2
import numpy as np
import torch
from einops import repeat, rearrange
from torchvision import transforms
from transformers import AutoModelForImageSegmentation

from PIL import Image
# Optional background removal (commented out by default)
# from rembg import remove, new_session


def array_to_tensor(np_array: np.ndarray, normalize: bool = True) -> torch.Tensor:
    """
    Convert numpy array to PyTorch tensor with optional normalization.
    
    This function converts image arrays to the format expected by the model,
    including normalization to [-1, 1] range and proper tensor dimensions.
    
    Args:
        np_array (np.ndarray): Input image array of shape [H, W, C]
        normalize (bool): Whether to normalize pixel values to [-1, 1]. Defaults to True.
        
    Returns:
        torch.Tensor: Converted tensor of shape [1, C, H, W]
    """
    image_pt = torch.tensor(np_array).float()
    
    # Normalize pixel values from [0, 255] to [-1, 1]
    if normalize:
        image_pt = image_pt / 255 * 2 - 1
    
    # Rearrange dimensions from HWC to CHW and add batch dimension
    image_pt = rearrange(image_pt, "h w c -> c h w")
    image_pts = repeat(image_pt, "c h w -> b c h w", b=1)
    
    return image_pts



#import rembg
from PIL import Image


class ImageProcessorV2:
    def __init__(self, size=512, border_ratio=None):
        self.size = size
        self.border_ratio = border_ratio

    @staticmethod
    def recenter(image, border_ratio: float = 0.2):
        """ recenter an image to leave some empty space at the image border.

        Args:
            image (ndarray): input image, float/uint8 [H, W, 3/4]
            mask (ndarray): alpha mask, bool [H, W]
            border_ratio (float, optional): border ratio, image will be resized to (1 - border_ratio). Defaults to 0.2.

        Returns:
            ndarray: output image, float/uint8 [H, W, 3/4]
        """

        if image.shape[-1] == 4:
            mask = image[..., 3]
        else:
            mask = np.ones_like(image[..., 0:1]) * 255
            image = np.concatenate([image, mask], axis=-1)
            mask = mask[..., 0]

        H, W, C = image.shape

        size = max(H, W)
        result = np.zeros((size, size, C), dtype=np.uint8)

        coords = np.nonzero(mask)
        x_min, x_max = coords[0].min(), coords[0].max()
        y_min, y_max = coords[1].min(), coords[1].max()
        h = x_max - x_min
        w = y_max - y_min
        if h == 0 or w == 0:
            raise ValueError('input image is empty')
        desired_size = int(size * (1 - border_ratio))
        scale = desired_size / max(h, w)
        h2 = int(h * scale)
        w2 = int(w * scale)
        x2_min = (size - h2) // 2
        x2_max = x2_min + h2

        y2_min = (size - w2) // 2
        y2_max = y2_min + w2

        result[x2_min:x2_max, y2_min:y2_max] = cv2.resize(image[x_min:x_max, y_min:y_max], (w2, h2),
                                                          interpolation=cv2.INTER_CUBIC)

        bg = np.ones((result.shape[0], result.shape[1], 3), dtype=np.uint8) * 255

        mask = result[..., 3:].astype(np.float32) / 255
        result = result[..., :3] * mask + bg * (1 - mask)

        result = result.clip(0, 255).astype(np.uint8)
        mask = mask.clip(0, 1)
        return result, mask

    def __call__(self, image_path, border_ratio=0.15, to_tensor=True, return_mask=False, **kwargs):
        if self.border_ratio is not None:
            border_ratio = self.border_ratio
            print(f"Using border_ratio from init: {border_ratio}")

        if isinstance(image_path, str):
            image = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
            image, mask = self.recenter(image, border_ratio=border_ratio)
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        elif isinstance(image_path, Image.Image):
            image = image_path.convert("RGBA")
            image = np.asarray(image)
            image, mask = self.recenter(image, border_ratio=border_ratio)

        image = cv2.resize(image, (self.size, self.size), interpolation=cv2.INTER_CUBIC)
        mask = cv2.resize(mask, (self.size, self.size), interpolation=cv2.INTER_NEAREST)
        mask = mask[..., np.newaxis]

        if to_tensor:
            image = array_to_tensor(image)
            mask = array_to_tensor(mask, normalize=False)
        if return_mask:
            return image, mask
        return image



class BRIARMBG:
    def __init__(self, path="briaai/RMBG-2.0", device='cuda'):
        self.birefnet = AutoModelForImageSegmentation.from_pretrained(
            path, trust_remote_code=True
        )
        self.birefnet.to(device)
        self.transform_image = transforms.Compose(
            [
                transforms.Resize((1024, 1024)),
                transforms.ToTensor(),
                transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
            ]
        )
        self.device = device

    def __call__(self, image):
        image_size = image.size
        input_images = self.transform_image(image).unsqueeze(0).to(self.device)
        with torch.no_grad():
            preds = self.birefnet(input_images)[-1].sigmoid().cpu()
        pred = preds[0].squeeze()
        pred_pil = transforms.ToPILImage()(pred)
        mask = pred_pil.resize(image_size)
        image.putalpha(mask)
        return image


class SRRealESRGAN:
    def __init__(self, path="weights/RealESRGAN_x2.pth", scale=2, download=True, device='cuda'):
        from RealESRGAN import RealESRGAN
        self.device = torch.device(device)
        self.model = RealESRGAN(self.device, scale=scale)
        self.model.load_weights(path, download=download)

    def __call__(self, image: PIL.Image.Image):
        image = self.model.predict(image.convert('RGB'))
        return image


class BackgroundRemover:
    def __init__(self):
        self.session = new_session()

    def __call__(self, image: Image.Image):
        output = remove(image, session=self.session, bgcolor=[255, 255, 255, 0])
        return output


from functools import partial

IMAGE_PROCESSORS = {
    "v2": ImageProcessorV2,
}

DEFAULT_IMAGEPROCESSOR = 'v2'
