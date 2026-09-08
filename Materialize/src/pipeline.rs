//! Multi-pass PBR pipeline.
//!
//! Height is no longer a single shader: a chain of small compute passes builds
//! it — luminance → gaussian pyramid blend (iterative σ-doubling, two
//! σ0·√3 rounds per level) → guided filter base/detail split (He et al. 2010)
//! → final composition. Every dispatch runs in ONE encoder/submit; WebGPU
//! serialises dispatches with implicit barriers between passes.

use anyhow::{Context, Result};
use image::DynamicImage;
use std::time::Instant;

use crate::gpu::{ChainStep, ComputePipeline, GpuContext};
use crate::io::MapSelection;
use crate::preset::{FilterParams, PresetParams};

const PARAMS_PRELUDE: &str = include_str!("shaders/params.wgsl");
const BLUR_SHADER: &str = include_str!("shaders/blur.wgsl");
const UNARY_SHADER: &str = include_str!("shaders/unary.wgsl");
const BINARY_SHADER: &str = include_str!("shaders/binary.wgsl");
const GUIDED_SHADER: &str = include_str!("shaders/guided.wgsl");
const HEIGHT_FINAL_SHADER: &str = include_str!("shaders/height_final.wgsl");
const SEAMLESS_SHADER: &str = include_str!("shaders/seamless.wgsl");
const NORMAL_SHADER: &str = include_str!("shaders/normal.wgsl");
const METALLIC_SHADER: &str = include_str!("shaders/metallic.wgsl");
const SMOOTHNESS_SHADER: &str = include_str!("shaders/smoothness.wgsl");
const EDGE_SHADER: &str = include_str!("shaders/edge.wgsl");
const AO_SHADER: &str = include_str!("shaders/ao.wgsl");
const CURVATURE_SHADER: &str = include_str!("shaders/curvature.wgsl");

/// Make-seamless tier (F4). The image is rolled by the minimum-SSD offset
/// (CPU) before upload; `Fast` cross-fades the seam band, `High` additionally
/// solves a Poisson blend on the band.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum SeamlessMode {
    #[default]
    Off,
    Fast,
    High,
}

/// Extra processing options for one image.
#[derive(Default)]
pub struct ProcessOptions<'a> {
    pub seamless_mode: SeamlessMode,
    pub intrinsic: Option<&'a IntrinsicMaps>,
}

/// Concatenate the shared prelude (Params/FilterParams structs + helpers) in
/// front of a shader body — the single source of truth lives in params.wgsl.
fn compose(body: &str) -> String {
    format!("{PARAMS_PRELUDE}\n{body}")
}

/// Optional intrinsic-decomposition inputs (F6). When present, metallic and
/// smoothness read the shadow-free albedo, the metallic pass gets the specular
/// residual, and the high-frequency part of the shading folds into the height.
pub struct IntrinsicMaps {
    pub albedo: DynamicImage,
    pub shading: DynamicImage,
    pub specular: DynamicImage,
}

#[derive(Debug, Clone, Default)]
pub struct StageTimings {
    pub seam_ms: u128,
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
    luma_pipeline: ComputePipeline,
    scale_pipeline: ComputePipeline,
    blend_add_pipeline: ComputePipeline,
    square_pipeline: ComputePipeline,
    sub_pipeline: ComputePipeline,
    blur_h_pipeline: ComputePipeline,
    blur_v_pipeline: ComputePipeline,
    guided_pipeline: ComputePipeline,
    height_final_pipeline: ComputePipeline,
    seam_blend_pipeline: ComputePipeline,
    seam_upconvert_pipeline: ComputePipeline,
    seam_poisson_pipeline: ComputePipeline,
    seam_downconvert_pipeline: ComputePipeline,
    normal_pipeline: ComputePipeline,
    metallic_pipeline: ComputePipeline,
    smoothness_pipeline: ComputePipeline,
    edge_pipeline: ComputePipeline,
    ao_pipeline: ComputePipeline,
    curvature_pipeline: ComputePipeline,
    params_bind_group_layout: wgpu::BindGroupLayout,
    /// 1×1 black rgba8 — stand-in for a deselected specular input.
    dummy_rgba8_black: wgpu::Texture,
    /// 1×1 flat normal (0.5, 0.5, 1.0) — stand-in for a deselected normal.
    dummy_rgba8_flat_normal: wgpu::Texture,
    /// 1×1 zero r32 — stand-in for the optional shading high-frequency input.
    dummy_r32_zero: wgpu::Texture,
    pub adapter_info: String,
}

impl Pipeline {
    pub async fn new() -> Result<Self> {
        let gpu = GpuContext::new().await?;
        let adapter_info = gpu.adapter_info_string();

        let params_bind_group_layout = gpu.create_params_bind_group_layout();
        let r32 = wgpu::TextureFormat::R32Float;
        let rgba8 = wgpu::TextureFormat::Rgba8Unorm;
        let prelude = |body: &str| compose(body);

        let luma_pipeline = gpu.create_pipeline(
            &prelude(UNARY_SHADER),
            "main_luma",
            &[rgba8],
            &[r32],
            &params_bind_group_layout,
        )?;
        let scale_pipeline = gpu.create_pipeline(
            &prelude(UNARY_SHADER),
            "main_scale",
            &[r32],
            &[r32],
            &params_bind_group_layout,
        )?;
        let blend_add_pipeline = gpu.create_pipeline(
            &prelude(BINARY_SHADER),
            "main_blend_add",
            &[r32, r32],
            &[r32],
            &params_bind_group_layout,
        )?;
        let square_pipeline = gpu.create_pipeline(
            &prelude(UNARY_SHADER),
            "main_square",
            &[r32],
            &[r32],
            &params_bind_group_layout,
        )?;
        let sub_pipeline = gpu.create_pipeline(
            &prelude(BINARY_SHADER),
            "main_sub",
            &[r32, r32],
            &[r32],
            &params_bind_group_layout,
        )?;
        let blur_h_pipeline = gpu.create_pipeline(
            &prelude(BLUR_SHADER),
            "main_h",
            &[r32],
            &[r32],
            &params_bind_group_layout,
        )?;
        let blur_v_pipeline = gpu.create_pipeline(
            &prelude(BLUR_SHADER),
            "main_v",
            &[r32],
            &[r32],
            &params_bind_group_layout,
        )?;
        let guided_pipeline = gpu.create_pipeline(
            &prelude(GUIDED_SHADER),
            "main",
            &[r32, r32, r32],
            &[r32, r32],
            &params_bind_group_layout,
        )?;
        let height_final_pipeline = gpu.create_pipeline(
            &prelude(HEIGHT_FINAL_SHADER),
            "main",
            &[r32, r32, r32],
            &[r32],
            &params_bind_group_layout,
        )?;
        let rgba16f = wgpu::TextureFormat::Rgba16Float;
        let seam_blend_pipeline = gpu.create_pipeline_slotted(
            &prelude(SEAMLESS_SHADER),
            "main_blend",
            &[(0, rgba8)],
            &[(3, rgba8)],
            &params_bind_group_layout,
        )?;
        let seam_upconvert_pipeline = gpu.create_pipeline_slotted(
            &prelude(SEAMLESS_SHADER),
            "main_upconvert",
            &[(0, rgba8)],
            &[(4, rgba16f)],
            &params_bind_group_layout,
        )?;
        let seam_poisson_pipeline = gpu.create_pipeline_slotted(
            &prelude(SEAMLESS_SHADER),
            "main_poisson",
            &[(0, rgba16f), (1, rgba8), (2, rgba8)],
            &[(4, rgba16f)],
            &params_bind_group_layout,
        )?;
        let seam_downconvert_pipeline = gpu.create_pipeline_slotted(
            &prelude(SEAMLESS_SHADER),
            "main_downconvert",
            &[(0, rgba16f)],
            &[(3, rgba8)],
            &params_bind_group_layout,
        )?;
        let normal_pipeline = gpu.create_pipeline(
            &prelude(NORMAL_SHADER),
            "main",
            &[r32],
            &[rgba8],
            &params_bind_group_layout,
        )?;
        let metallic_pipeline = gpu.create_pipeline(
            &prelude(METALLIC_SHADER),
            "main",
            &[rgba8, rgba8],
            &[rgba8],
            &params_bind_group_layout,
        )?;
        let smoothness_pipeline = gpu.create_pipeline(
            &prelude(SMOOTHNESS_SHADER),
            "main",
            &[rgba8, rgba8, rgba8],
            &[rgba8],
            &params_bind_group_layout,
        )?;
        let edge_pipeline = gpu.create_pipeline(
            &prelude(EDGE_SHADER),
            "main",
            &[rgba8],
            &[rgba8],
            &params_bind_group_layout,
        )?;
        let ao_pipeline = gpu.create_pipeline(
            &prelude(AO_SHADER),
            "main",
            &[r32, r32],
            &[rgba8],
            &params_bind_group_layout,
        )?;
        let curvature_pipeline = gpu.create_pipeline(
            &prelude(CURVATURE_SHADER),
            "main",
            &[r32],
            &[rgba8],
            &params_bind_group_layout,
        )?;

        let dummy_rgba8_black = gpu.create_output_texture(1, 1, rgba8);
        gpu.queue.write_texture(
            wgpu::TexelCopyTextureInfo {
                texture: &dummy_rgba8_black,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            &[0, 0, 0, 255],
            wgpu::TexelCopyBufferLayout {
                offset: 0,
                bytes_per_row: Some(256),
                rows_per_image: Some(1),
            },
            wgpu::Extent3d {
                width: 1,
                height: 1,
                depth_or_array_layers: 1,
            },
        );
        let dummy_rgba8_flat_normal = gpu.create_output_texture(1, 1, rgba8);
        gpu.queue.write_texture(
            wgpu::TexelCopyTextureInfo {
                texture: &dummy_rgba8_flat_normal,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            &[128, 128, 255, 255],
            wgpu::TexelCopyBufferLayout {
                offset: 0,
                bytes_per_row: Some(256),
                rows_per_image: Some(1),
            },
            wgpu::Extent3d {
                width: 1,
                height: 1,
                depth_or_array_layers: 1,
            },
        );
        let dummy_r32_zero = gpu.create_output_texture(1, 1, r32);
        gpu.queue.write_texture(
            wgpu::TexelCopyTextureInfo {
                texture: &dummy_r32_zero,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            &0.0f32.to_le_bytes(),
            wgpu::TexelCopyBufferLayout {
                offset: 0,
                bytes_per_row: Some(256),
                rows_per_image: Some(1),
            },
            wgpu::Extent3d {
                width: 1,
                height: 1,
                depth_or_array_layers: 1,
            },
        );

        Ok(Self {
            gpu,
            luma_pipeline,
            scale_pipeline,
            blend_add_pipeline,
            square_pipeline,
            sub_pipeline,
            blur_h_pipeline,
            blur_v_pipeline,
            guided_pipeline,
            height_final_pipeline,
            seam_blend_pipeline,
            seam_upconvert_pipeline,
            seam_poisson_pipeline,
            seam_downconvert_pipeline,
            normal_pipeline,
            metallic_pipeline,
            smoothness_pipeline,
            edge_pipeline,
            ao_pipeline,
            curvature_pipeline,
            params_bind_group_layout,
            dummy_rgba8_black,
            dummy_rgba8_flat_normal,
            dummy_r32_zero,
            adapter_info,
        })
    }

    pub async fn process(
        &self,
        image: &DynamicImage,
        params: &PresetParams,
        selection: &MapSelection,
    ) -> Result<(PbrMaps, StageTimings)> {
        self.process_with(image, params, selection, &ProcessOptions::default())
            .await
    }

    pub async fn process_with(
        &self,
        image: &DynamicImage,
        params: &PresetParams,
        selection: &MapSelection,
        options: &ProcessOptions<'_>,
    ) -> Result<(PbrMaps, StageTimings)> {
        let total_start = Instant::now();
        let mut timings = StageTimings::default();
        let intrinsic = options.intrinsic;

        let width = image.width();
        let height = image.height();

        let params_buffer = self.gpu.create_params_buffer(bytemuck::bytes_of(params));
        let params_bind_group = self
            .gpu
            .create_params_bind_group(&self.params_bind_group_layout, &params_buffer);

        let workgroups_x = width.div_ceil(8);
        let workgroups_y = height.div_ceil(8);

        let mut steps: Vec<ChainStep> = Vec::with_capacity(32 + 64);

        // === F4: make-seamless pre-pass (roll + band blend on the diffuse) ===
        let t_seam = Instant::now();
        let (diffuse_bytes, rolled_offset) = match options.seamless_mode {
            SeamlessMode::Off => (image.to_rgba8().into_raw(), None),
            mode => {
                let rgba = image.to_rgba8();
                let raw = rgba.as_raw().clone();
                let band = params.seam_band as usize;
                let (dx, dy) =
                    crate::seam::find_best_offset(&raw, width as usize, height as usize, band);
                let rolled = crate::seam::roll_image(&raw, width as usize, height as usize, dx, dy);
                let _ = mode;
                (rolled, Some((dx, dy)))
            }
        };
        let diffuse_texture = self
            .gpu
            .create_texture_from_rgba(&diffuse_bytes, width, height);
        let rolled_view = diffuse_texture.create_view(&Default::default());

        let effective_diffuse_view: wgpu::TextureView = match options.seamless_mode {
            SeamlessMode::Off => rolled_view,
            SeamlessMode::Fast => {
                let blended =
                    self.gpu
                        .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
                let bview = blended.create_view(&Default::default());
                let bg = self.gpu.create_bind_group_slotted(
                    &self.seam_blend_pipeline.bind_group_layout,
                    &[(0, &rolled_view), (3, &bview)],
                );
                steps.push(ChainStep {
                    pipeline: &self.seam_blend_pipeline,
                    bind_group0: bg,
                    bind_group1: params_bind_group.clone(),
                    workgroups_x,
                    workgroups_y,
                });
                bview
            }
            SeamlessMode::High => {
                // Fast blend first (it is the Poisson target T).
                let blended =
                    self.gpu
                        .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
                let blended_view = blended.create_view(&Default::default());
                let bg = self.gpu.create_bind_group_slotted(
                    &self.seam_blend_pipeline.bind_group_layout,
                    &[(0, &rolled_view), (3, &blended_view)],
                );
                steps.push(ChainStep {
                    pipeline: &self.seam_blend_pipeline,
                    bind_group0: bg,
                    bind_group1: params_bind_group.clone(),
                    workgroups_x,
                    workgroups_y,
                });

                // Seed the iterate with T.
                let f16a =
                    self.gpu
                        .create_output_texture(width, height, wgpu::TextureFormat::Rgba16Float);
                let f16b =
                    self.gpu
                        .create_output_texture(width, height, wgpu::TextureFormat::Rgba16Float);
                let f16a_view = f16a.create_view(&Default::default());
                let f16b_view = f16b.create_view(&Default::default());
                let bg = self.gpu.create_bind_group_slotted(
                    &self.seam_upconvert_pipeline.bind_group_layout,
                    &[(0, &blended_view), (4, &f16a_view)],
                );
                steps.push(ChainStep {
                    pipeline: &self.seam_upconvert_pipeline,
                    bind_group0: bg,
                    bind_group1: params_bind_group.clone(),
                    workgroups_x,
                    workgroups_y,
                });

                // Jacobi: 48 steps, ping-pong (prev, T=blended, S=rolled → out).
                let poisson_ab = self.gpu.create_bind_group_slotted(
                    &self.seam_poisson_pipeline.bind_group_layout,
                    &[
                        (0, &f16a_view),
                        (1, &blended_view),
                        (2, &rolled_view),
                        (4, &f16b_view),
                    ],
                );
                let poisson_ba = self.gpu.create_bind_group_slotted(
                    &self.seam_poisson_pipeline.bind_group_layout,
                    &[
                        (0, &f16b_view),
                        (1, &blended_view),
                        (2, &rolled_view),
                        (4, &f16a_view),
                    ],
                );
                let mut last = &f16a_view;
                for i in 0..48u32 {
                    let bg = if i % 2 == 0 { &poisson_ab } else { &poisson_ba };
                    last = if i % 2 == 0 { &f16b_view } else { &f16a_view };
                    steps.push(ChainStep {
                        pipeline: &self.seam_poisson_pipeline,
                        bind_group0: bg.clone(),
                        bind_group1: params_bind_group.clone(),
                        workgroups_x,
                        workgroups_y,
                    });
                }

                // Down-convert the final iterate to the prepared diffuse.
                let prepared =
                    self.gpu
                        .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
                let prepared_view = prepared.create_view(&Default::default());
                let bg = self.gpu.create_bind_group_slotted(
                    &self.seam_downconvert_pipeline.bind_group_layout,
                    &[(0, last), (3, &prepared_view)],
                );
                steps.push(ChainStep {
                    pipeline: &self.seam_downconvert_pipeline,
                    bind_group0: bg,
                    bind_group1: params_bind_group.clone(),
                    workgroups_x,
                    workgroups_y,
                });
                prepared_view
            }
        };
        if options.seamless_mode != SeamlessMode::Off {
            timings.seam_ms = t_seam.elapsed().as_millis();
        }
        let _ = rolled_offset;
        let diffuse_view = effective_diffuse_view;

        // F6: albedo replaces the diffuse for metallic/smoothness; specular is
        // direct evidence; shading folds its high-frequency into the height.
        let albedo_texture = intrinsic
            .map(|maps| self.gpu.create_texture_from_image(&maps.albedo))
            .unwrap_or_else(|| {
                self.gpu
                    .create_output_texture(1, 1, wgpu::TextureFormat::Rgba8Unorm)
            });
        let albedo_view = if intrinsic.is_some() {
            albedo_texture.create_view(&Default::default())
        } else {
            self.dummy_rgba8_black.create_view(&Default::default())
        };
        let specular_view = match intrinsic {
            Some(maps) => self
                .gpu
                .create_texture_from_image(&maps.specular)
                .create_view(&Default::default()),
            None => self.dummy_rgba8_black.create_view(&Default::default()),
        };

        // Scratch pool (r32float ping-pong) + final height/base/detail.
        let mk_r32 = |gpu: &GpuContext| {
            gpu.create_output_texture(width, height, wgpu::TextureFormat::R32Float)
        };
        let s0 = mk_r32(&self.gpu);
        let s1 = mk_r32(&self.gpu);
        let s2 = mk_r32(&self.gpu);
        let s3 = mk_r32(&self.gpu);
        let s4 = mk_r32(&self.gpu);
        let s5 = mk_r32(&self.gpu);
        let base_tex = mk_r32(&self.gpu);
        let detail_tex = mk_r32(&self.gpu);
        let height_tex = mk_r32(&self.gpu);
        let v = |t: &wgpu::Texture| t.create_view(&Default::default());
        let (s0v, s1v, s2v, s3v, s4v, s5v) = (v(&s0), v(&s1), v(&s2), v(&s3), v(&s4), v(&s5));
        let base_v = v(&base_tex);
        let detail_v = v(&detail_tex);
        let height_v = v(&height_tex);

        // Filter uniform buffers: gaussian σ0, gaussian σ0·√3 (level doubling),
        // box window for the guided filter, and one weight per pyramid level.
        let seamless = params.seamless == 1;
        let levels = params.height_pyramid_levels.round().clamp(1.0, 7.0) as usize;
        let sigma0 = params.height_sigma_base.max(0.1);
        let sigma_double = sigma0 * 3.0f32.sqrt(); // σ² add: (σd)² + (σd)² = 3σ0² on a σ0 base ⇒ σk = 2σk−1
        let weights = params.pyramid_weights();

        let filter_buffer = |fp: &FilterParams| -> (wgpu::Buffer, wgpu::BindGroup) {
            let buf = self.gpu.create_params_buffer(bytemuck::bytes_of(fp));
            let bg = self
                .gpu
                .create_params_bind_group(&self.params_bind_group_layout, &buf);
            (buf, bg)
        };
        let (_b0, gauss0_bg) = filter_buffer(&FilterParams::gaussian(sigma0, seamless));
        let (_bd, gaussd_bg) = filter_buffer(&FilterParams::gaussian(sigma_double, seamless));
        let (_bx, box_bg) = filter_buffer(&FilterParams::boxed(params.guided_radius, seamless));
        let weight_bgs: Vec<wgpu::BindGroup> = weights
            .iter()
            .map(|w| filter_buffer(&FilterParams::boxed(0.0, seamless).with_weight(*w)).1)
            .collect();

        // === Height chain (F1) ===
        let t0 = Instant::now();

        // Luminance of the diffuse.
        let luma_bg = self.gpu.create_bind_group(
            &self.luma_pipeline.bind_group_layout,
            &[&diffuse_view],
            &[&s0v],
        );
        steps.push(ChainStep {
            pipeline: &self.luma_pipeline,
            bind_group0: luma_bg,
            bind_group1: params_bind_group.clone(),
            workgroups_x,
            workgroups_y,
        });

        // Level 0: gaussian σ0 of the luma.
        let bh =
            self.gpu
                .create_bind_group(&self.blur_h_pipeline.bind_group_layout, &[&s0v], &[&s1v]);
        steps.push(ChainStep {
            pipeline: &self.blur_h_pipeline,
            bind_group0: bh,
            bind_group1: gauss0_bg.clone(),
            workgroups_x,
            workgroups_y,
        });
        let bv =
            self.gpu
                .create_bind_group(&self.blur_v_pipeline.bind_group_layout, &[&s1v], &[&s2v]);
        steps.push(ChainStep {
            pipeline: &self.blur_v_pipeline,
            bind_group0: bv,
            bind_group1: gauss0_bg.clone(),
            workgroups_x,
            workgroups_y,
        });

        // Accumulator seed: acc = w0 · b0.
        let seed_bg =
            self.gpu
                .create_bind_group(&self.scale_pipeline.bind_group_layout, &[&s2v], &[&s3v]);
        steps.push(ChainStep {
            pipeline: &self.scale_pipeline,
            bind_group0: seed_bg,
            bind_group1: weight_bgs[0].clone(),
            workgroups_x,
            workgroups_y,
        });

        // Levels 1..L: double σ with two σ0·√3 rounds, then accumulate.
        // acc ping-pongs between s3 and s4; b_k lives in s2.
        let mut acc_in = &s3v;
        let mut acc_out = &s4v;
        #[allow(clippy::needless_range_loop)]
        for k in 1..levels {
            for _round in 0..2 {
                // h: s2 → s1, then v: s1 → s2 (b is back in s2 with doubled σ).
                let bg = self.gpu.create_bind_group(
                    &self.blur_h_pipeline.bind_group_layout,
                    &[&s2v],
                    &[&s1v],
                );
                steps.push(ChainStep {
                    pipeline: &self.blur_h_pipeline,
                    bind_group0: bg,
                    bind_group1: gaussd_bg.clone(),
                    workgroups_x,
                    workgroups_y,
                });
                let bg = self.gpu.create_bind_group(
                    &self.blur_v_pipeline.bind_group_layout,
                    &[&s1v],
                    &[&s2v],
                );
                steps.push(ChainStep {
                    pipeline: &self.blur_v_pipeline,
                    bind_group0: bg,
                    bind_group1: gaussd_bg.clone(),
                    workgroups_x,
                    workgroups_y,
                });
            }
            let bg = self.gpu.create_bind_group(
                &self.blend_add_pipeline.bind_group_layout,
                &[acc_in, &s2v],
                &[acc_out],
            );
            steps.push(ChainStep {
                pipeline: &self.blend_add_pipeline,
                bind_group0: bg,
                bind_group1: weight_bgs[k].clone(),
                workgroups_x,
                workgroups_y,
            });
            std::mem::swap(&mut acc_in, &mut acc_out);
        }
        let h_view: &wgpu::TextureView = acc_in;

        // Guided filter statistics: meanI and meanII over a box window.
        let bg =
            self.gpu
                .create_bind_group(&self.blur_h_pipeline.bind_group_layout, &[h_view], &[&s0v]);
        steps.push(ChainStep {
            pipeline: &self.blur_h_pipeline,
            bind_group0: bg,
            bind_group1: box_bg.clone(),
            workgroups_x,
            workgroups_y,
        });
        let bg =
            self.gpu
                .create_bind_group(&self.blur_v_pipeline.bind_group_layout, &[&s0v], &[&s2v]);
        steps.push(ChainStep {
            pipeline: &self.blur_v_pipeline,
            bind_group0: bg,
            bind_group1: box_bg.clone(),
            workgroups_x,
            workgroups_y,
        });
        let mean_i_view = &s2v; // meanI

        let bg =
            self.gpu
                .create_bind_group(&self.square_pipeline.bind_group_layout, &[h_view], &[&s1v]);
        steps.push(ChainStep {
            pipeline: &self.square_pipeline,
            bind_group0: bg,
            bind_group1: params_bind_group.clone(),
            workgroups_x,
            workgroups_y,
        });
        let bg =
            self.gpu
                .create_bind_group(&self.blur_h_pipeline.bind_group_layout, &[&s1v], &[&s0v]);
        steps.push(ChainStep {
            pipeline: &self.blur_h_pipeline,
            bind_group0: bg,
            bind_group1: box_bg.clone(),
            workgroups_x,
            workgroups_y,
        });
        let bg =
            self.gpu
                .create_bind_group(&self.blur_v_pipeline.bind_group_layout, &[&s0v], &[&s1v]);
        steps.push(ChainStep {
            pipeline: &self.blur_v_pipeline,
            bind_group0: bg,
            bind_group1: box_bg.clone(),
            workgroups_x,
            workgroups_y,
        });
        let mean_ii_view = &s1v; // meanII

        let bg = self.gpu.create_bind_group(
            &self.guided_pipeline.bind_group_layout,
            &[h_view, mean_i_view, mean_ii_view],
            &[&base_v, &detail_v],
        );
        steps.push(ChainStep {
            pipeline: &self.guided_pipeline,
            bind_group0: bg,
            bind_group1: params_bind_group.clone(),
            workgroups_x,
            workgroups_y,
        });

        // F6: high-frequency shading → optional third input of the composer.
        let hf_view: wgpu::TextureView = match intrinsic {
            Some(maps) => {
                let shading_tex = self.gpu.create_texture_from_image(&maps.shading);
                let shading_v = shading_tex.create_view(&Default::default());
                let sld = self.gpu.create_bind_group(
                    &self.luma_pipeline.bind_group_layout,
                    &[&shading_v],
                    &[&s0v],
                );
                steps.push(ChainStep {
                    pipeline: &self.luma_pipeline,
                    bind_group0: sld,
                    bind_group1: params_bind_group.clone(),
                    workgroups_x,
                    workgroups_y,
                });
                let bg = self.gpu.create_bind_group(
                    &self.blur_h_pipeline.bind_group_layout,
                    &[&s0v],
                    &[&s5v],
                );
                steps.push(ChainStep {
                    pipeline: &self.blur_h_pipeline,
                    bind_group0: bg,
                    bind_group1: box_bg.clone(),
                    workgroups_x,
                    workgroups_y,
                });
                let bg = self.gpu.create_bind_group(
                    &self.blur_v_pipeline.bind_group_layout,
                    &[&s5v],
                    &[&s2v],
                );
                steps.push(ChainStep {
                    pipeline: &self.blur_v_pipeline,
                    bind_group0: bg,
                    bind_group1: box_bg.clone(),
                    workgroups_x,
                    workgroups_y,
                });
                let bg = self.gpu.create_bind_group(
                    &self.sub_pipeline.bind_group_layout,
                    &[&s0v, &s2v],
                    &[&s5v],
                );
                steps.push(ChainStep {
                    pipeline: &self.sub_pipeline,
                    bind_group0: bg,
                    bind_group1: params_bind_group.clone(),
                    workgroups_x,
                    workgroups_y,
                });
                s5v.clone()
            }
            None => self.dummy_r32_zero.create_view(&Default::default()),
        };

        let bg = self.gpu.create_bind_group(
            &self.height_final_pipeline.bind_group_layout,
            &[&base_v, &detail_v, &hf_view],
            &[&height_v],
        );
        steps.push(ChainStep {
            pipeline: &self.height_final_pipeline,
            bind_group0: bg,
            bind_group1: params_bind_group.clone(),
            workgroups_x,
            workgroups_y,
        });
        timings.height_ms = t0.elapsed().as_millis();

        // === Downstream single-pass maps ===
        let normal_texture = if selection.normal {
            let t = Instant::now();
            let tex =
                self.gpu
                    .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
            let view = tex.create_view(&Default::default());
            let bg = self.gpu.create_bind_group(
                &self.normal_pipeline.bind_group_layout,
                &[&height_v],
                &[&view],
            );
            steps.push(ChainStep {
                pipeline: &self.normal_pipeline,
                bind_group0: bg,
                bind_group1: params_bind_group.clone(),
                workgroups_x,
                workgroups_y,
            });
            timings.normal_ms = t.elapsed().as_millis();
            Some(tex)
        } else {
            None
        };

        let metallic_texture = if selection.metallic {
            let t = Instant::now();
            let tex =
                self.gpu
                    .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
            let view = tex.create_view(&Default::default());
            let src_view = if intrinsic.is_some() {
                &albedo_view
            } else {
                &diffuse_view
            };
            let bg = self.gpu.create_bind_group(
                &self.metallic_pipeline.bind_group_layout,
                &[src_view, &specular_view],
                &[&view],
            );
            steps.push(ChainStep {
                pipeline: &self.metallic_pipeline,
                bind_group0: bg,
                bind_group1: params_bind_group.clone(),
                workgroups_x,
                workgroups_y,
            });
            timings.metallic_ms = t.elapsed().as_millis();
            Some(tex)
        } else {
            None
        };

        let smoothness_texture = if selection.smoothness {
            let t = Instant::now();
            let tex =
                self.gpu
                    .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
            let view = tex.create_view(&Default::default());
            let src_view = if intrinsic.is_some() {
                &albedo_view
            } else {
                &diffuse_view
            };
            let metallic_view: wgpu::TextureView = match &metallic_texture {
                Some(mt) => mt.create_view(&Default::default()),
                None => self.dummy_rgba8_black.create_view(&Default::default()),
            };
            let normal_view: wgpu::TextureView = match &normal_texture {
                Some(nt) => nt.create_view(&Default::default()),
                None => self
                    .dummy_rgba8_flat_normal
                    .create_view(&Default::default()),
            };
            let bg = self.gpu.create_bind_group(
                &self.smoothness_pipeline.bind_group_layout,
                &[src_view, &metallic_view, &normal_view],
                &[&view],
            );
            steps.push(ChainStep {
                pipeline: &self.smoothness_pipeline,
                bind_group0: bg,
                bind_group1: params_bind_group.clone(),
                workgroups_x,
                workgroups_y,
            });
            timings.smoothness_ms = t.elapsed().as_millis();
            Some(tex)
        } else {
            None
        };

        let edge_texture = if selection.edge {
            let t = Instant::now();
            let tex =
                self.gpu
                    .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
            let view = tex.create_view(&Default::default());
            let normal_view_for_input: wgpu::TextureView = match &normal_texture {
                Some(nt) => nt.create_view(&Default::default()),
                None => self
                    .dummy_rgba8_flat_normal
                    .create_view(&Default::default()),
            };
            let bg = self.gpu.create_bind_group(
                &self.edge_pipeline.bind_group_layout,
                &[&normal_view_for_input],
                &[&view],
            );
            steps.push(ChainStep {
                pipeline: &self.edge_pipeline,
                bind_group0: bg,
                bind_group1: params_bind_group.clone(),
                workgroups_x,
                workgroups_y,
            });
            timings.edge_ms = t.elapsed().as_millis();
            Some(tex)
        } else {
            None
        };

        let ao_texture = if selection.ao {
            let t = Instant::now();
            let tex =
                self.gpu
                    .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
            let view = tex.create_view(&Default::default());
            let bg = self.gpu.create_bind_group(
                &self.ao_pipeline.bind_group_layout,
                &[&height_v, &base_v],
                &[&view],
            );
            steps.push(ChainStep {
                pipeline: &self.ao_pipeline,
                bind_group0: bg,
                bind_group1: params_bind_group.clone(),
                workgroups_x,
                workgroups_y,
            });
            timings.ao_ms = t.elapsed().as_millis();
            Some(tex)
        } else {
            None
        };

        let curvature_texture = if selection.curvature {
            let t = Instant::now();
            let tex =
                self.gpu
                    .create_output_texture(width, height, wgpu::TextureFormat::Rgba8Unorm);
            let view = tex.create_view(&Default::default());
            let bg = self.gpu.create_bind_group(
                &self.curvature_pipeline.bind_group_layout,
                &[&height_v],
                &[&view],
            );
            steps.push(ChainStep {
                pipeline: &self.curvature_pipeline,
                bind_group0: bg,
                bind_group1: params_bind_group.clone(),
                workgroups_x,
                workgroups_y,
            });
            timings.curvature_ms = t.elapsed().as_millis();
            Some(tex)
        } else {
            None
        };

        // One submit for the whole chain.
        self.gpu.dispatch_chain(&steps);

        // Read back results
        let t_read = Instant::now();
        // Propagar (o mesmo `.ok()` do gpu.rs engolia device-lost e o
        // read_texture a seguir podia ficar pendurado no map_async).
        self.gpu
            .device
            .poll(wgpu::PollType::wait_indefinitely())
            .context("GPU device poll failed before readback")?;

        let height_data = self.gpu.read_texture(&height_tex).await?;
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

    /// Blocking wrapper around `process_with`.
    pub fn process_blocking_with(
        &self,
        image: &DynamicImage,
        params: &PresetParams,
        selection: &MapSelection,
        options: &ProcessOptions<'_>,
    ) -> Result<(PbrMaps, StageTimings)> {
        pollster::block_on(self.process_with(image, params, selection, options))
    }
}
