use anyhow::{Context, Result};
use wgpu::util::DeviceExt;

/// Map `MATERIALIZE_GPU_BACKEND` env var to a wgpu Backends bitmask. Defaults to PRIMARY.
fn parse_gpu_backend_env() -> wgpu::Backends {
    match std::env::var("MATERIALIZE_GPU_BACKEND") {
        Ok(v) => match v.trim().to_lowercase().as_str() {
            "vulkan" => wgpu::Backends::VULKAN,
            "metal" => wgpu::Backends::METAL,
            "dx12" => wgpu::Backends::DX12,
            "gl" | "opengl" => wgpu::Backends::GL,
            "primary" | "" => wgpu::Backends::PRIMARY,
            other => {
                log::warn!(
                    "MATERIALIZE_GPU_BACKEND='{}' unknown; falling back to PRIMARY. \
                     Valid: vulkan|metal|dx12|gl|primary.",
                    other
                );
                wgpu::Backends::PRIMARY
            }
        },
        Err(_) => wgpu::Backends::PRIMARY,
    }
}

pub struct GpuContext {
    pub device: wgpu::Device,
    pub queue: wgpu::Queue,
    /// Captured at adapter request; `adapter` itself is not kept alive.
    adapter_info: wgpu::AdapterInfo,
}

pub struct ComputePipeline {
    pub pipeline: wgpu::ComputePipeline,
    pub bind_group_layout: wgpu::BindGroupLayout,
}

/// One dispatch of a multi-pass chain: pipeline + textures (group 0) + the
/// uniform at group 1 (global `Params` or per-pass `FilterParams`).
pub struct ChainStep<'a> {
    pub pipeline: &'a ComputePipeline,
    pub bind_group0: wgpu::BindGroup,
    pub bind_group1: wgpu::BindGroup,
    pub workgroups_x: u32,
    pub workgroups_y: u32,
}

impl GpuContext {
    pub async fn new() -> Result<Self> {
        let backends = parse_gpu_backend_env();
        let instance = wgpu::Instance::new(wgpu::InstanceDescriptor {
            backends,
            ..wgpu::InstanceDescriptor::new_without_display_handle()
        });

        let adapter = instance
            .request_adapter(&wgpu::RequestAdapterOptions {
                power_preference: wgpu::PowerPreference::HighPerformance,
                compatible_surface: None,
                force_fallback_adapter: false,
                apply_limit_buckets: false,
            })
            .await
            .context("No GPU adapter available. Check Vulkan/Metal/DX12 drivers")?;

        let adapter_info = adapter.get_info();

        let (device, queue) = adapter
            .request_device(&wgpu::DeviceDescriptor {
                required_features: wgpu::Features::empty(),
                required_limits: wgpu::Limits::default(),
                label: None,
                ..Default::default()
            })
            .await
            .context("Failed to create GPU device")?;

        Ok(Self {
            device,
            queue,
            adapter_info,
        })
    }

    /// Human-readable adapter + backend string for verbose mode.
    pub fn adapter_info_string(&self) -> String {
        format!(
            "{} ({:?}, backend {:?})",
            self.adapter_info.name, self.adapter_info.device_type, self.adapter_info.backend
        )
    }

    pub fn create_texture_from_image(&self, image: &image::DynamicImage) -> wgpu::Texture {
        let rgba = image.to_rgba8();
        self.create_texture_from_rgba(rgba.as_raw(), image.width(), image.height())
    }

    /// Upload raw rgba8 bytes as an Rgba8Unorm sampled texture.
    pub fn create_texture_from_rgba(&self, rgba: &[u8], width: u32, height: u32) -> wgpu::Texture {
        let texture_size = wgpu::Extent3d {
            width,
            height,
            depth_or_array_layers: 1,
        };

        let texture = self.device.create_texture(&wgpu::TextureDescriptor {
            size: texture_size,
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: wgpu::TextureFormat::Rgba8Unorm,
            usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
            label: Some("input_texture"),
            view_formats: &[],
        });

        self.queue.write_texture(
            wgpu::TexelCopyTextureInfo {
                aspect: wgpu::TextureAspect::All,
                texture: &texture,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
            },
            rgba,
            wgpu::TexelCopyBufferLayout {
                offset: 0,
                bytes_per_row: Some(4 * width),
                rows_per_image: Some(height),
            },
            texture_size,
        );

        texture
    }

    pub fn create_output_texture(
        &self,
        width: u32,
        height: u32,
        format: wgpu::TextureFormat,
    ) -> wgpu::Texture {
        let texture_size = wgpu::Extent3d {
            width,
            height,
            depth_or_array_layers: 1,
        };

        self.device.create_texture(&wgpu::TextureDescriptor {
            size: texture_size,
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format,
            usage: wgpu::TextureUsages::STORAGE_BINDING
                | wgpu::TextureUsages::COPY_SRC
                | wgpu::TextureUsages::TEXTURE_BINDING
                | wgpu::TextureUsages::COPY_DST,
            label: Some("output_texture"),
            view_formats: &[],
        })
    }

    /// Bind group layout for the uniform params buffer at @group(1) @binding(0).
    pub fn create_params_bind_group_layout(&self) -> wgpu::BindGroupLayout {
        self.device
            .create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
                label: Some("params_bind_group_layout"),
                entries: &[wgpu::BindGroupLayoutEntry {
                    binding: 0,
                    visibility: wgpu::ShaderStages::COMPUTE,
                    ty: wgpu::BindingType::Buffer {
                        ty: wgpu::BufferBindingType::Uniform,
                        has_dynamic_offset: false,
                        min_binding_size: None,
                    },
                    count: None,
                }],
            })
    }

    /// Create a GPU buffer initialised with the preset params bytes.
    pub fn create_params_buffer(&self, data: &[u8]) -> wgpu::Buffer {
        self.device
            .create_buffer_init(&wgpu::util::BufferInitDescriptor {
                label: Some("params_buffer"),
                contents: data,
                usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
            })
    }

    /// Bind group that binds the params buffer to @group(1) @binding(0).
    pub fn create_params_bind_group(
        &self,
        layout: &wgpu::BindGroupLayout,
        buffer: &wgpu::Buffer,
    ) -> wgpu::BindGroup {
        self.device.create_bind_group(&wgpu::BindGroupDescriptor {
            layout,
            entries: &[wgpu::BindGroupEntry {
                binding: 0,
                resource: buffer.as_entire_binding(),
            }],
            label: Some("params_bind_group"),
        })
    }

    /// Generalised compute pipeline: N sampled input textures followed by M
    /// write-only storage outputs at @group(0); a uniform at @group(1).
    pub fn create_pipeline(
        &self,
        shader_code: &str,
        entry_point: &str,
        input_formats: &[wgpu::TextureFormat],
        output_formats: &[wgpu::TextureFormat],
        params_layout: &wgpu::BindGroupLayout,
    ) -> Result<ComputePipeline> {
        assert!(
            !input_formats.is_empty() && !output_formats.is_empty(),
            "compute pipeline needs at least one input and one output texture"
        );

        let shader = self
            .device
            .create_shader_module(wgpu::ShaderModuleDescriptor {
                label: Some("compute_shader"),
                source: wgpu::ShaderSource::Wgsl(shader_code.into()),
            });

        let mut entries: Vec<wgpu::BindGroupLayoutEntry> = Vec::new();
        for (i, format) in input_formats.iter().enumerate() {
            let filterable = matches!(
                format,
                wgpu::TextureFormat::Rgba8Unorm | wgpu::TextureFormat::Rgba8UnormSrgb
            );
            entries.push(wgpu::BindGroupLayoutEntry {
                binding: i as u32,
                visibility: wgpu::ShaderStages::COMPUTE,
                ty: wgpu::BindingType::Texture {
                    multisampled: false,
                    view_dimension: wgpu::TextureViewDimension::D2,
                    sample_type: wgpu::TextureSampleType::Float { filterable },
                },
                count: None,
            });
        }
        for (j, format) in output_formats.iter().enumerate() {
            entries.push(wgpu::BindGroupLayoutEntry {
                binding: (input_formats.len() + j) as u32,
                visibility: wgpu::ShaderStages::COMPUTE,
                ty: wgpu::BindingType::StorageTexture {
                    access: wgpu::StorageTextureAccess::WriteOnly,
                    format: *format,
                    view_dimension: wgpu::TextureViewDimension::D2,
                },
                count: None,
            });
        }

        let bind_group_layout =
            self.device
                .create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
                    label: Some("bind_group_layout"),
                    entries: &entries,
                });

        let pipeline_layout = self
            .device
            .create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
                label: Some("pipeline_layout"),
                bind_group_layouts: &[Some(&bind_group_layout), Some(params_layout)],
                ..Default::default()
            });

        let pipeline = self
            .device
            .create_compute_pipeline(&wgpu::ComputePipelineDescriptor {
                label: Some("compute_pipeline"),
                layout: Some(&pipeline_layout),
                module: &shader,
                entry_point: Some(entry_point),
                compilation_options: Default::default(),
                cache: None,
            });

        Ok(ComputePipeline {
            pipeline,
            bind_group_layout,
        })
    }

    /// Pipeline variant with EXPLICIT binding slots (for shader modules whose
    /// entry points share module-level declarations at fixed slots, e.g.
    /// seamless.wgsl). `inputs`/`outputs` are (slot, format) pairs.
    pub fn create_pipeline_slotted(
        &self,
        shader_code: &str,
        entry_point: &str,
        inputs: &[(u32, wgpu::TextureFormat)],
        outputs: &[(u32, wgpu::TextureFormat)],
        params_layout: &wgpu::BindGroupLayout,
    ) -> Result<ComputePipeline> {
        assert!(
            !inputs.is_empty() && !outputs.is_empty(),
            "compute pipeline needs at least one input and one output texture"
        );

        let shader = self
            .device
            .create_shader_module(wgpu::ShaderModuleDescriptor {
                label: Some("compute_shader"),
                source: wgpu::ShaderSource::Wgsl(shader_code.into()),
            });

        let entries: Vec<wgpu::BindGroupLayoutEntry> = inputs
            .iter()
            .map(|(slot, format)| {
                let filterable = matches!(
                    format,
                    wgpu::TextureFormat::Rgba8Unorm | wgpu::TextureFormat::Rgba8UnormSrgb
                );
                wgpu::BindGroupLayoutEntry {
                    binding: *slot,
                    visibility: wgpu::ShaderStages::COMPUTE,
                    ty: wgpu::BindingType::Texture {
                        multisampled: false,
                        view_dimension: wgpu::TextureViewDimension::D2,
                        sample_type: wgpu::TextureSampleType::Float { filterable },
                    },
                    count: None,
                }
            })
            .chain(
                outputs
                    .iter()
                    .map(|(slot, format)| wgpu::BindGroupLayoutEntry {
                        binding: *slot,
                        visibility: wgpu::ShaderStages::COMPUTE,
                        ty: wgpu::BindingType::StorageTexture {
                            access: wgpu::StorageTextureAccess::WriteOnly,
                            format: *format,
                            view_dimension: wgpu::TextureViewDimension::D2,
                        },
                        count: None,
                    }),
            )
            .collect();

        let bind_group_layout =
            self.device
                .create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
                    label: Some("bind_group_layout_slotted"),
                    entries: &entries,
                });

        let pipeline_layout = self
            .device
            .create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
                label: Some("pipeline_layout"),
                bind_group_layouts: &[Some(&bind_group_layout), Some(params_layout)],
                ..Default::default()
            });

        let pipeline = self
            .device
            .create_compute_pipeline(&wgpu::ComputePipelineDescriptor {
                label: Some("compute_pipeline"),
                layout: Some(&pipeline_layout),
                module: &shader,
                entry_point: Some(entry_point),
                compilation_options: Default::default(),
                cache: None,
            });

        Ok(ComputePipeline {
            pipeline,
            bind_group_layout,
        })
    }

    /// Bind group for a slotted pipeline: (slot, view) pairs, inputs and
    /// outputs together in one list.
    pub fn create_bind_group_slotted(
        &self,
        layout: &wgpu::BindGroupLayout,
        bindings: &[(u32, &wgpu::TextureView)],
    ) -> wgpu::BindGroup {
        let entries: Vec<wgpu::BindGroupEntry> = bindings
            .iter()
            .map(|(slot, view)| wgpu::BindGroupEntry {
                binding: *slot,
                resource: wgpu::BindingResource::TextureView(view),
            })
            .collect();
        self.device.create_bind_group(&wgpu::BindGroupDescriptor {
            layout,
            entries: &entries,
            label: Some("bind_group_slotted"),
        })
    }

    /// Bind group for a pipeline created by [`Self::create_pipeline`]:
    /// inputs then outputs, in the same order.
    pub fn create_bind_group(
        &self,
        layout: &wgpu::BindGroupLayout,
        input_views: &[&wgpu::TextureView],
        output_views: &[&wgpu::TextureView],
    ) -> wgpu::BindGroup {
        let mut entries: Vec<wgpu::BindGroupEntry> = Vec::new();
        for (i, view) in input_views.iter().enumerate() {
            entries.push(wgpu::BindGroupEntry {
                binding: i as u32,
                resource: wgpu::BindingResource::TextureView(view),
            });
        }
        for (j, view) in output_views.iter().enumerate() {
            entries.push(wgpu::BindGroupEntry {
                binding: (input_views.len() + j) as u32,
                resource: wgpu::BindingResource::TextureView(view),
            });
        }
        self.device.create_bind_group(&wgpu::BindGroupDescriptor {
            layout,
            entries: &entries,
            label: Some("bind_group"),
        })
    }

    /// Execute a chain of dispatches in ONE encoder/submit. WebGPU serialises
    /// dispatches (including across passes within a submit) with implicit
    /// barriers, so pass N can read what pass N−1 wrote.
    pub fn dispatch_chain(&self, steps: &[ChainStep]) {
        let mut encoder = self
            .device
            .create_command_encoder(&wgpu::CommandEncoderDescriptor {
                label: Some("chain_encoder"),
            });

        for step in steps {
            let mut compute_pass = encoder.begin_compute_pass(&wgpu::ComputePassDescriptor {
                label: Some("chain_pass"),
                timestamp_writes: None,
            });

            compute_pass.set_pipeline(&step.pipeline.pipeline);
            compute_pass.set_bind_group(0, Some(&step.bind_group0), &[]);
            compute_pass.set_bind_group(1, Some(&step.bind_group1), &[]);
            compute_pass.dispatch_workgroups(step.workgroups_x, step.workgroups_y, 1);
        }

        self.queue.submit(Some(encoder.finish()));
    }

    pub async fn read_texture(&self, texture: &wgpu::Texture) -> Result<Vec<u8>> {
        let size = texture.size();
        let format = texture.format();

        let bytes_per_pixel = match format {
            wgpu::TextureFormat::R32Float => 4,
            wgpu::TextureFormat::R8Unorm => 1,
            wgpu::TextureFormat::Rgba8Unorm => 4,
            wgpu::TextureFormat::Rgba16Float => 8,
            _ => anyhow::bail!("Unsupported texture format for readback"),
        };

        let unpadded_bytes_per_row = size.width * bytes_per_pixel;
        let align = wgpu::COPY_BYTES_PER_ROW_ALIGNMENT;
        let padded_bytes_per_row = unpadded_bytes_per_row.div_ceil(align) * align;
        let buffer_size = padded_bytes_per_row * size.height;

        let buffer = self.device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("readback_buffer"),
            size: buffer_size as u64,
            usage: wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::MAP_READ,
            mapped_at_creation: false,
        });

        let mut encoder = self
            .device
            .create_command_encoder(&wgpu::CommandEncoderDescriptor {
                label: Some("readback_encoder"),
            });

        encoder.copy_texture_to_buffer(
            wgpu::TexelCopyTextureInfo {
                aspect: wgpu::TextureAspect::All,
                texture,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
            },
            wgpu::TexelCopyBufferInfo {
                buffer: &buffer,
                layout: wgpu::TexelCopyBufferLayout {
                    offset: 0,
                    bytes_per_row: Some(padded_bytes_per_row),
                    rows_per_image: Some(size.height),
                },
            },
            size,
        );

        self.queue.submit(Some(encoder.finish()));

        let buffer_slice = buffer.slice(..);
        let (sender, receiver) = futures::channel::oneshot::channel();

        buffer_slice.map_async(wgpu::MapMode::Read, move |result| {
            let _ = sender.send(result);
        });

        // Propagar o erro do poll: com `.ok()` um device lost engolia-se e o
        // receiver.await abaixo ficava pendurado para sempre (hang em vez do
        // erro tipado do contrato de exit code).
        self.device
            .poll(wgpu::PollType::wait_indefinitely())
            .context("GPU device poll failed before readback")?;
        receiver.await??;

        let data = buffer_slice
            .get_mapped_range()
            .context("Failed to get mapped buffer range")?;

        let mut unpadded_data = Vec::with_capacity((unpadded_bytes_per_row * size.height) as usize);
        for row in 0..size.height {
            let start = (row * padded_bytes_per_row) as usize;
            let end = start + unpadded_bytes_per_row as usize;
            unpadded_data.extend_from_slice(&data[start..end]);
        }

        drop(data);
        buffer.unmap();

        Ok(unpadded_data)
    }
}
