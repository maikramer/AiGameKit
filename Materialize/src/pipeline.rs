use anyhow::Result;
use image::DynamicImage;
use std::time::Instant;

use crate::gpu::{ComputePipeline, GpuContext};
use crate::io::MapSelection;
use crate::preset::PresetParams;

const HEIGHT_SHADER: &str = include_str!("shaders/height.wgsl");
const NORMAL_SHADER: &str = include_str!("shaders/normal.wgsl");
const METALLIC_SHADER: &str = include_str!("shaders/metallic.wgsl");
const SMOOTHNESS_SHADER: &str = include_str!("shaders/smoothness.wgsl");
const EDGE_SHADER: &str = include_str!("shaders/edge.wgsl");
const AO_SHADER: &str = include_str!("shaders/ao.wgsl");
const CURVATURE_SHADER: &str = include_str!("shaders/curvature.wgsl");

#[derive(Debug, Clone, Default)]
pub struct StageTimings {
    pub height_ms: u128,
    pub normal_ms: u128,
    pub metallic_ms: u128,
    pub smoothness_ms: u128,
    pub edge_ms: u128,
    pub ao_ms: u128,
    pub curvature_ms: u128,
    pub readback_ms: u128,
    pub total_ms: u128,
}

pub struct PbrMaps {
    pub height: Vec<f32>,
    pub normal: Vec<u8>,
    pub metallic: Vec<u8>,
    pub smoothness: Vec<u8>,
    pub edge: Vec<u8>,
    pub ao: Vec<u8>,
    pub curvature: Vec<u8>,
}

pub struct Pipeline {
    pub gpu: GpuContext,
    height_pipeline: ComputePipeline,
    normal_pipeline: ComputePipeline,
    metallic_pipeline: ComputePipeline,
    smoothness_pipeline: ComputePipeline,
    edge_pipeline: ComputePipeline,
    ao_pipeline: ComputePipeline,
    curvature_pipeline: ComputePipeline,
    params_bind_group_layout: wgpu::BindGroupLayout,
    pub adapter_info: String,
}

impl Pipeline {
    pub async fn new() -> Result<Self> {
        let gpu = GpuContext::new().await?;
        let adapter_info = gpu.adapter_info_string();

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

        let curvature_pipeline = gpu.create_compute_pipeline(
            CURVATURE_SHADER,
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
            curvature_pipeline,
            params_bind_group_layout,
            adapter_info,
        })
    }

    pub async fn process(
        &self,
        image: &DynamicImage,
        params: &PresetParams,
        selection: &MapSelection,
    ) -> Result<(PbrMaps, StageTimings)> {
        let total_start = Instant::now();
        let mut timings = StageTimings::default();

        let width = image.width();
        let height = image.height();

        let params_buffer = self.gpu.create_params_buffer(bytemuck::bytes_of(params));
        let params_bind_group = self
            .gpu
            .create_params_bind_group(&self.params_bind_group_layout, &params_buffer);

        let diffuse_texture = self.gpu.create_texture_from_image(image);
        let diffuse_view = diffuse_texture.create_view(&Default::default());

        let workgroups_x = width.div_ceil(8);
        let workgroups_y = height.div_ceil(8);

        // 1. Height (always run; downstream shaders depend on it)
        let t0 = Instant::now();
        let height_texture =
            self.gpu
                .create_output_texture(width, height, wgpu::TextureFormat::R32Float);
        let height_view = height_texture.create_view(&Default::default());

        let height_bind_group = self.gpu.create_bind_group(
            &self.height_pipeline.bind_group_layout,
            &diffuse_view,
            &height_view,
        );

        self.gpu.dispatch_compute(
            &self.height_pipeline.pipeline,
            &height_bind_group,
            &params_bind_group,
            workgroups_x,
            workgroups_y,
        );
        timings.height_ms = t0.elapsed().as_millis();

        // 2. Normal from height
        let normal_texture = if selection.normal {
            let t = Instant::now();
            let tex =
                self.gpu
                    .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
            let view = tex.create_view(&Default::default());
            let bg = self.gpu.create_bind_group(
                &self.normal_pipeline.bind_group_layout,
                &height_view,
                &view,
            );
            self.gpu.dispatch_compute(
                &self.normal_pipeline.pipeline,
                &bg,
                &params_bind_group,
                workgroups_x,
                workgroups_y,
            );
            timings.normal_ms = t.elapsed().as_millis();
            Some(tex)
        } else {
            None
        };

        // 3. Metallic from diffuse
        let metallic_texture = if selection.metallic {
            let t = Instant::now();
            let tex =
                self.gpu
                    .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
            let view = tex.create_view(&Default::default());
            let bg = self.gpu.create_bind_group(
                &self.metallic_pipeline.bind_group_layout,
                &diffuse_view,
                &view,
            );
            self.gpu.dispatch_compute(
                &self.metallic_pipeline.pipeline,
                &bg,
                &params_bind_group,
                workgroups_x,
                workgroups_y,
            );
            timings.metallic_ms = t.elapsed().as_millis();
            Some(tex)
        } else {
            None
        };

        // 4. Smoothness from diffuse + metallic
        let smoothness_texture = if selection.smoothness {
            let t = Instant::now();
            let tex =
                self.gpu
                    .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
            let view = tex.create_view(&Default::default());

            // Metallic map is needed as input; create a dummy one-pixel view if skipped.
            let metallic_view_for_input: wgpu::TextureView = if let Some(ref mt) = metallic_texture
            {
                mt.create_view(&Default::default())
            } else {
                let dummy = self
                    .gpu
                    .create_output_texture(1, 1, wgpu::TextureFormat::Rgba8Unorm);
                dummy.create_view(&Default::default())
            };

            let bg = self.gpu.create_bind_group_2_inputs(
                &self.smoothness_pipeline.bind_group_layout,
                &diffuse_view,
                &metallic_view_for_input,
                &view,
            );
            self.gpu.dispatch_compute(
                &self.smoothness_pipeline.pipeline,
                &bg,
                &params_bind_group,
                workgroups_x,
                workgroups_y,
            );
            timings.smoothness_ms = t.elapsed().as_millis();
            Some(tex)
        } else {
            None
        };

        // 5. Edge from normal
        let edge_texture = if selection.edge {
            let t = Instant::now();
            let tex =
                self.gpu
                    .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
            let view = tex.create_view(&Default::default());
            let normal_view_for_input: wgpu::TextureView = if let Some(ref nt) = normal_texture {
                nt.create_view(&Default::default())
            } else {
                let dummy = self
                    .gpu
                    .create_output_texture(1, 1, wgpu::TextureFormat::Rgba8Unorm);
                dummy.create_view(&Default::default())
            };
            let bg = self.gpu.create_bind_group(
                &self.edge_pipeline.bind_group_layout,
                &normal_view_for_input,
                &view,
            );
            self.gpu.dispatch_compute(
                &self.edge_pipeline.pipeline,
                &bg,
                &params_bind_group,
                workgroups_x,
                workgroups_y,
            );
            timings.edge_ms = t.elapsed().as_millis();
            Some(tex)
        } else {
            None
        };

        // 6. AO from height
        let ao_texture = if selection.ao {
            let t = Instant::now();
            let tex =
                self.gpu
                    .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
            let view = tex.create_view(&Default::default());
            let bg = self.gpu.create_bind_group(
                &self.ao_pipeline.bind_group_layout,
                &height_view,
                &view,
            );
            self.gpu.dispatch_compute(
                &self.ao_pipeline.pipeline,
                &bg,
                &params_bind_group,
                workgroups_x,
                workgroups_y,
            );
            timings.ao_ms = t.elapsed().as_millis();
            Some(tex)
        } else {
            None
        };

        // 7. Curvature from height (opt-in)
        let curvature_texture = if selection.curvature {
            let t = Instant::now();
            let tex =
                self.gpu
                    .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
            let view = tex.create_view(&Default::default());
            let bg = self.gpu.create_bind_group(
                &self.curvature_pipeline.bind_group_layout,
                &height_view,
                &view,
            );
            self.gpu.dispatch_compute(
                &self.curvature_pipeline.pipeline,
                &bg,
                &params_bind_group,
                workgroups_x,
                workgroups_y,
            );
            timings.curvature_ms = t.elapsed().as_millis();
            Some(tex)
        } else {
            None
        };

        // Read back results
        let t_read = Instant::now();
        self.gpu
            .device
            .poll(wgpu::PollType::wait_indefinitely())
            .ok();

        let height_data = self.gpu.read_texture(&height_texture).await?;
        let normal_data = if let Some(ref nt) = normal_texture {
            self.gpu.read_texture(nt).await?
        } else {
            Vec::new()
        };
        let metallic_data = if let Some(ref mt) = metallic_texture {
            self.gpu.read_texture(mt).await?
        } else {
            Vec::new()
        };
        let smoothness_data = if let Some(ref st) = smoothness_texture {
            self.gpu.read_texture(st).await?
        } else {
            Vec::new()
        };
        let edge_data = if let Some(ref et) = edge_texture {
            self.gpu.read_texture(et).await?
        } else {
            Vec::new()
        };
        let ao_data = if let Some(ref at) = ao_texture {
            self.gpu.read_texture(at).await?
        } else {
            Vec::new()
        };
        let curvature_data = if let Some(ref ct) = curvature_texture {
            self.gpu.read_texture(ct).await?
        } else {
            Vec::new()
        };
        timings.readback_ms = t_read.elapsed().as_millis();

        let height_f32: Vec<f32> = height_data
            .chunks_exact(4)
            .map(|chunk| f32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]))
            .collect();

        let metallic_r: Vec<u8> = metallic_data.chunks_exact(4).map(|c| c[0]).collect();
        let smoothness_r: Vec<u8> = smoothness_data.chunks_exact(4).map(|c| c[0]).collect();
        let edge_r: Vec<u8> = edge_data.chunks_exact(4).map(|c| c[0]).collect();
        let ao_r: Vec<u8> = ao_data.chunks_exact(4).map(|c| c[0]).collect();
        let curvature_r: Vec<u8> = curvature_data.chunks_exact(4).map(|c| c[0]).collect();

        timings.total_ms = total_start.elapsed().as_millis();

        Ok((
            PbrMaps {
                height: height_f32,
                normal: normal_data,
                metallic: metallic_r,
                smoothness: smoothness_r,
                edge: edge_r,
                ao: ao_r,
                curvature: curvature_r,
            },
            timings,
        ))
    }

    /// Blocking wrapper around `process` for use in non-async contexts (e.g. batch loop).
    pub fn process_blocking(
        &self,
        image: &DynamicImage,
        params: &PresetParams,
        selection: &MapSelection,
    ) -> Result<(PbrMaps, StageTimings)> {
        pollster::block_on(self.process(image, params, selection))
    }
}
