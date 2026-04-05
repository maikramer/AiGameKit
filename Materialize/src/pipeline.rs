use anyhow::Result;
use image::DynamicImage;

use crate::gpu::{ComputePipeline, GpuContext};
use crate::preset::PresetParams;

const HEIGHT_SHADER: &str = include_str!("shaders/height.wgsl");
const NORMAL_SHADER: &str = include_str!("shaders/normal.wgsl");
const METALLIC_SHADER: &str = include_str!("shaders/metallic.wgsl");
const SMOOTHNESS_SHADER: &str = include_str!("shaders/smoothness.wgsl");
const EDGE_SHADER: &str = include_str!("shaders/edge.wgsl");
const AO_SHADER: &str = include_str!("shaders/ao.wgsl");

pub struct PbrMaps {
    pub height: Vec<f32>,
    pub normal: Vec<u8>,
    pub metallic: Vec<u8>,
    pub smoothness: Vec<u8>,
    pub edge: Vec<u8>,
    pub ao: Vec<u8>,
}

pub struct Pipeline {
    gpu: GpuContext,
    height_pipeline: ComputePipeline,
    normal_pipeline: ComputePipeline,
    metallic_pipeline: ComputePipeline,
    smoothness_pipeline: ComputePipeline,
    edge_pipeline: ComputePipeline,
    ao_pipeline: ComputePipeline,
    params_bind_group_layout: wgpu::BindGroupLayout,
}

impl Pipeline {
    pub async fn new() -> Result<Self> {
        let gpu = GpuContext::new().await?;

        let params_bind_group_layout = gpu.create_params_bind_group_layout();

        let height_pipeline = gpu.create_compute_pipeline(
            HEIGHT_SHADER,
            "main",
            wgpu::TextureFormat::Rgba8Unorm,
            wgpu::TextureFormat::R32Float,
            &params_bind_group_layout,
        )?;
        let normal_pipeline = gpu.create_compute_pipeline(
            NORMAL_SHADER,
            "main",
            wgpu::TextureFormat::R32Float,
            wgpu::TextureFormat::Rgba8Unorm,
            &params_bind_group_layout,
        )?;
        let metallic_pipeline = gpu.create_compute_pipeline(
            METALLIC_SHADER,
            "main",
            wgpu::TextureFormat::Rgba8Unorm,
            wgpu::TextureFormat::Rgba8Unorm,
            &params_bind_group_layout,
        )?;

        let smoothness_pipeline = gpu.create_compute_pipeline_2_inputs(
            SMOOTHNESS_SHADER,
            "main",
            wgpu::TextureFormat::Rgba8Unorm,
            wgpu::TextureFormat::Rgba8Unorm,
            wgpu::TextureFormat::Rgba8Unorm,
            &params_bind_group_layout,
        )?;

        let edge_pipeline = gpu.create_compute_pipeline(
            EDGE_SHADER,
            "main",
            wgpu::TextureFormat::Rgba8Unorm,
            wgpu::TextureFormat::Rgba8Unorm,
            &params_bind_group_layout,
        )?;

        let ao_pipeline = gpu.create_compute_pipeline(
            AO_SHADER,
            "main",
            wgpu::TextureFormat::R32Float,
            wgpu::TextureFormat::Rgba8Unorm,
            &params_bind_group_layout,
        )?;

        Ok(Self {
            gpu,
            height_pipeline,
            normal_pipeline,
            metallic_pipeline,
            smoothness_pipeline,
            edge_pipeline,
            ao_pipeline,
            params_bind_group_layout,
        })
    }

    pub async fn process(&self, image: &DynamicImage, params: &PresetParams) -> Result<PbrMaps> {
        let width = image.width();
        let height = image.height();

        let params_buffer = self.gpu.create_params_buffer(bytemuck::bytes_of(params));
        let params_bind_group = self
            .gpu
            .create_params_bind_group(&self.params_bind_group_layout, &params_buffer);

        let diffuse_texture = self.gpu.create_texture_from_image(image);
        let diffuse_view = diffuse_texture.create_view(&Default::default());

        // 1. Height map
        let height_texture =
            self.gpu
                .create_output_texture(width, height, wgpu::TextureFormat::R32Float);
        let height_view = height_texture.create_view(&Default::default());

        let height_bind_group = self.gpu.create_bind_group(
            &self.height_pipeline.bind_group_layout,
            &diffuse_view,
            &height_view,
        );

        let workgroups_x = width.div_ceil(8);
        let workgroups_y = height.div_ceil(8);

        self.gpu.dispatch_compute(
            &self.height_pipeline.pipeline,
            &height_bind_group,
            &params_bind_group,
            workgroups_x,
            workgroups_y,
        );

        // 2. Normal map from height
        let normal_texture =
            self.gpu
                .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
        let normal_view = normal_texture.create_view(&Default::default());

        let normal_bind_group = self.gpu.create_bind_group(
            &self.normal_pipeline.bind_group_layout,
            &height_view,
            &normal_view,
        );

        self.gpu.dispatch_compute(
            &self.normal_pipeline.pipeline,
            &normal_bind_group,
            &params_bind_group,
            workgroups_x,
            workgroups_y,
        );

        // 3. Metallic map from diffuse
        let metallic_texture =
            self.gpu
                .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
        let metallic_view = metallic_texture.create_view(&Default::default());

        let metallic_bind_group = self.gpu.create_bind_group(
            &self.metallic_pipeline.bind_group_layout,
            &diffuse_view,
            &metallic_view,
        );

        self.gpu.dispatch_compute(
            &self.metallic_pipeline.pipeline,
            &metallic_bind_group,
            &params_bind_group,
            workgroups_x,
            workgroups_y,
        );

        // 4. Smoothness from diffuse + metallic
        let smoothness_texture =
            self.gpu
                .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
        let smoothness_view = smoothness_texture.create_view(&Default::default());

        let smoothness_bind_group = self.gpu.create_bind_group_2_inputs(
            &self.smoothness_pipeline.bind_group_layout,
            &diffuse_view,
            &metallic_view,
            &smoothness_view,
        );

        self.gpu.dispatch_compute(
            &self.smoothness_pipeline.pipeline,
            &smoothness_bind_group,
            &params_bind_group,
            workgroups_x,
            workgroups_y,
        );

        // 5. Edge from normal
        let edge_texture =
            self.gpu
                .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
        let edge_view = edge_texture.create_view(&Default::default());

        let edge_bind_group = self.gpu.create_bind_group(
            &self.edge_pipeline.bind_group_layout,
            &normal_view,
            &edge_view,
        );

        self.gpu.dispatch_compute(
            &self.edge_pipeline.pipeline,
            &edge_bind_group,
            &params_bind_group,
            workgroups_x,
            workgroups_y,
        );

        // 6. AO from height
        let ao_texture =
            self.gpu
                .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
        let ao_view = ao_texture.create_view(&Default::default());

        let ao_bind_group =
            self.gpu
                .create_bind_group(&self.ao_pipeline.bind_group_layout, &height_view, &ao_view);

        self.gpu.dispatch_compute(
            &self.ao_pipeline.pipeline,
            &ao_bind_group,
            &params_bind_group,
            workgroups_x,
            workgroups_y,
        );

        // Read back results
        self.gpu
            .device
            .poll(wgpu::PollType::wait_indefinitely())
            .ok();

        let height_data = self.gpu.read_texture(&height_texture).await?;
        let normal_data = self.gpu.read_texture(&normal_texture).await?;
        let metallic_data = self.gpu.read_texture(&metallic_texture).await?;
        let smoothness_data = self.gpu.read_texture(&smoothness_texture).await?;
        let edge_data = self.gpu.read_texture(&edge_texture).await?;
        let ao_data = self.gpu.read_texture(&ao_texture).await?;

        let height_f32: Vec<f32> = height_data
            .chunks_exact(4)
            .map(|chunk| f32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]))
            .collect();

        let metallic_r: Vec<u8> = metallic_data.chunks_exact(4).map(|c| c[0]).collect();
        let smoothness_r: Vec<u8> = smoothness_data.chunks_exact(4).map(|c| c[0]).collect();
        let edge_r: Vec<u8> = edge_data.chunks_exact(4).map(|c| c[0]).collect();
        let ao_r: Vec<u8> = ao_data.chunks_exact(4).map(|c| c[0]).collect();

        Ok(PbrMaps {
            height: height_f32,
            normal: normal_data,
            metallic: metallic_r,
            smoothness: smoothness_r,
            edge: edge_r,
            ao: ao_r,
        })
    }
}
