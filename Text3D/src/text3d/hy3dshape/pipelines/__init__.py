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
Hunyuan3D-Omni Pipelines Module

This module contains the inference pipelines for the Hunyuan3D-Omni model,
providing multi-modal conditional 3D generation capabilities.

Available Pipelines:
    - Hunyuan3DOmniSiTFlowMatchingPipeline: Main pipeline for multi-modal 3D generation
      using SiT (Scalable Interpolant Transformers) with Flow Matching
"""

from .pipeline_generation_sit_omni import Hunyuan3DOmniSiTFlowMatchingPipeline

__all__ = [
    'Hunyuan3DOmniSiTFlowMatchingPipeline'
]
