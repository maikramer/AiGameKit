//! Shader regression harness; no engine, window, assets, or browser is started.
//!
//! CPU: `cargo test --test sky_shader` (Naga parse + full validation + ABI).
//! GPU: `cargo test --test sky_shader sky_gpu -- --ignored --nocapture`
//! Optional captures: `VIBER_SKY_CAPTURE_DIR=/tmp/sky` with the GPU command.
//! `WGPU_ADAPTER_NAME=llvmpipe` selects software Vulkan if installed.
//!
//! IMPORTANT: imports are explicit minimal stubs, NOT Bevy import composition.
//! These tests cannot prove compatibility with the complete Bevy view layout or
//! tonemapping/bloom. GPU images execute the actual fragment shader on a fullscreen
//! triangle, with synthetic camera rays, and read back linear RGBA32Float pixels.
//! PNG previews use Reinhard + sRGB, not Bevy's tonemapper; raw .rgba32f files are
//! little-endian RGBA f32, default 256 x 128, row-major top to bottom.
//! Set VIBER_SKY_CAPTURE_WIDTH=1024 for 1024x512 real renders (no upscaling).

use bevy::render::{
    render_resource::*,
    renderer::{RenderDevice, RenderQueue, initialize_renderer},
    settings::{WgpuSettings, WgpuSettingsPriority},
};
use naga::{AddressSpace, ScalarKind, ShaderStage, StorageAccess, TypeInner, VectorSize};
use std::{borrow::Cow, path::Path, sync::mpsc, time::Duration};
use viber::sky::SkyConfig;

/// O grupo do MATERIAL no bevy 0.19 (material.rs) — o `#{MATERIAL_BIND_GROUP}`
/// do WGSL é substituído por este valor em runtime. O céu leu ETERNAMENTE o
/// binding errado quando o grupo estava hardcoded a 2 (céu "congelado" em
/// lixo que parecia aurora — regressão de 2026-09-06).
const MATERIAL_BIND_GROUP: u32 = bevy::pbr::MATERIAL_BIND_GROUP_INDEX as u32;

fn dimensions() -> (u32, u32) {
    let width = std::env::var("VIBER_SKY_CAPTURE_WIDTH")
        .map(|s| s.parse::<u32>().expect("capture width integer"))
        .unwrap_or(256);
    assert!(
        (64..=2048).contains(&width) && width % 16 == 0,
        "width must be 64..2048 and divisible by 16 for readback alignment"
    );
    (width, width / 2)
}
const IMPORTS: [(&str, &str); 3] = [
    (
        "#import bevy_render::view::View",
        "struct View { world_from_view: mat4x4<f32>, };",
    ),
    (
        "#import bevy_render::globals::Globals",
        "struct Globals { time: f32, delta_time: f32, frame_count: u32, };",
    ),
    (
        "#import bevy_pbr::forward_io::VertexOutput",
        "struct VertexOutput { @builtin(position) position: vec4<f32>, @location(0) world_position: vec4<f32>, };",
    ),
];

fn standalone(source: &str) -> String {
    source.lines().map(|line| {
        let trimmed = line.trim();
        if trimmed.starts_with('#') {
            IMPORTS.iter().find(|(import, _)| *import == trimmed)
                .unwrap_or_else(|| panic!("unsupported shader directive: {line}; extend the explicit harness contract"))
                .1.to_owned()
        } else {
            line.replace("#{MATERIAL_BIND_GROUP}", &MATERIAL_BIND_GROUP.to_string())
        }
    }).collect::<Vec<_>>().join("\n")
}

fn validate(source: &str) -> naga::Module {
    let module = naga::front::wgsl::parse_str(source)
        .unwrap_or_else(|error| panic!("{}", error.emit_to_string(source)));
    naga::valid::Validator::new(
        naga::valid::ValidationFlags::all(),
        naga::valid::Capabilities::all(),
    )
    .validate(&module)
    .unwrap_or_else(|error| panic!("{}", error.emit_to_string(source)));
    module
}

#[test]
fn template_and_specialized_worlds_parse_and_validate() {
    validate(&standalone(include_str!("../src/sky.wgsl")));
    for config in [
        SkyConfig::default(),
        SkyConfig {
            drive: true,
            cloud_coverage: 0.0,
            cloud_density: 0.0,
            star_density: 0.0,
            aurora: 0.0,
            nebula: 0.0,
            ..Default::default()
        },
        SkyConfig {
            drive: false,
            cloud_coverage: 1.0,
            cloud_density: 1.0,
            cloud_elevation: 1.0,
            sun_elevation: 90.0,
            mie_g: 1.0,
            wind: [0.0, 0.0],
            ..Default::default()
        },
        SkyConfig {
            drive: true,
            sun_elevation: -90.0,
            cloud_elevation: 0.0,
            sun_intensity: 0.0,
            ..Default::default()
        },
        SkyConfig {
            // O ramo físico (nishita) com os multiplicadores do simple-rpg:
            // valida o vec3 das consts e o raymarch no mesmo harness.
            drive: true,
            model: viber::sky::SkyModel::Nishita,
            rayleigh: 2.8,
            nishita_mie: 1.5,
            mie_g: 0.8,
            sun_intensity: 2.6,
            ..Default::default()
        },
    ] {
        validate(&standalone(&config.render_world_shader()));
    }
}

#[test]
fn storage_binding_and_six_vec4_abi_are_preserved() {
    let module = validate(&standalone(include_str!("../src/sky.wgsl")));
    let (_, sky) = module
        .global_variables
        .iter()
        .find(|(_, var)| var.name.as_deref() == Some("sky"))
        .expect("sky storage binding");
    assert_eq!(
        sky.space,
        AddressSpace::Storage {
            access: StorageAccess::LOAD
        }
    );
    let binding = sky.binding.as_ref().expect("bound storage");
    assert_eq!(
        (binding.group, binding.binding),
        (MATERIAL_BIND_GROUP, 0),
        "o storage do céu TEM de viver no grupo do MATERIAL (#{MATERIAL_BIND_GROUP}) — \
         um grupo hardcoded fica atrás do bevy e lê o binding errado como SkyUniform"
    );
    let TypeInner::Struct { members, span } = &module.types[sky.ty].inner else {
        panic!("SkyUniform must be a struct")
    };
    assert_eq!(*span, 96);
    assert_eq!(members.len(), 6);
    for (i, (member, name)) in members
        .iter()
        .zip(["sun", "moon", "zenith", "horizon", "sun_tint", "params"])
        .enumerate()
    {
        assert_eq!(member.name.as_deref(), Some(name));
        assert_eq!(member.offset, i as u32 * 16);
        assert!(matches!(
            module.types[member.ty].inner,
            TypeInner::Vector {
                size: VectorSize::Quad,
                scalar: naga::Scalar {
                    kind: ScalarKind::Float,
                    width: 4
                }
            }
        ));
    }
    assert!(
        module
            .entry_points
            .iter()
            .any(|entry| entry.name == "fragment" && entry.stage == ShaderStage::Fragment)
    );
}

#[test]
fn validator_rejects_invalid_types_and_syntax() {
    // Negative controls: these assertions prove the harness is validating, not
    // merely searching text or accepting whatever the preprocessor produces.
    let bad_type = "@fragment fn fragment() -> @location(0) vec4<f32> { return true; }";
    let module = naga::front::wgsl::parse_str(bad_type).unwrap();
    assert!(
        naga::valid::Validator::new(
            naga::valid::ValidationFlags::all(),
            naga::valid::Capabilities::all()
        )
        .validate(&module)
        .is_err()
    );
    assert!(naga::front::wgsl::parse_str("@fragment fn fragment( { }").is_err());
}

fn floats(values: impl IntoIterator<Item = f32>) -> Vec<u8> {
    values.into_iter().flat_map(f32::to_le_bytes).collect()
}

// Explicit synthetic atmospheres: do not silently depend on gameplay clocks.
fn atmosphere(night: bool, coverage_delta: f32, time: f32) -> [[f32; 4]; 6] {
    if night {
        [
            [0.0, -0.8, -0.6, 0.0],
            [0.0, 0.8, -0.6, 1.0],
            [0.008, 0.015, 0.05, 0.0],
            [0.035, 0.06, 0.12, coverage_delta],
            [1.0, 0.96, 0.88, time],
            [0.7, 0.25, 0.32, 1380.0],
        ]
    } else {
        [
            [0.0, 0.8, -0.6, 1.0],
            [0.0, -0.8, 0.6, 0.0],
            [0.085, 0.255, 0.62, 0.0],
            [0.60, 0.755, 0.90, coverage_delta],
            [1.0, 0.96, 0.88, time],
            [0.7, 0.25, 0.32, 720.0],
        ]
    }
}

// Golden hour: sun scraping the horizon, full warm grading, day=1.
fn dawn_atmosphere() -> [[f32; 4]; 6] {
    [
        [0.0, 0.06, -1.0, 1.0],
        [0.0, -0.8, 0.6, 0.0],
        [0.10, 0.22, 0.50, 1.0],
        [0.85, 0.55, 0.30, 0.0],
        [1.0, 0.62, 0.35, 120.0],
        [0.7, 0.25, 0.32, 400.0],
    ]
}

fn render(
    device: &RenderDevice,
    queue: &RenderQueue,
    config: &SkyConfig,
    sky: [[f32; 4]; 6],
    top: bool,
) -> Vec<[f32; 4]> {
    let (width, height) = dimensions();
    let mut source = standalone(&config.render_world_shader());
    // Two perspective views cover the horizon (including below it) and zenith.
    // Keep fragment() completely unchanged, including its real dpdx/dpdy path.
    let ray = if top {
        "vec3(p.x, 1.0, p.y)"
    } else {
        "vec3(p.x * 2.0, p.y * 1.25 + 0.55, -1.0)"
    };
    source.push_str(&format!(
        r#"
@vertex fn qa_vertex(@builtin(vertex_index) index: u32) -> VertexOutput {{
    let p = array<vec2<f32>, 3>(vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0))[index];
    var out: VertexOutput;
    out.position = vec4(p, 0.0, 1.0);
    out.world_position = vec4({ray}, 1.0);
    return out;
}}
"#
    ));
    validate(&source);
    let shader = device.create_and_validate_shader_module(ShaderModuleDescriptor {
        label: Some("sky regression"),
        source: ShaderSource::Wgsl(Cow::Owned(source)),
    });
    let binding = |binding, ty| BindGroupLayoutEntry {
        binding,
        visibility: ShaderStages::FRAGMENT,
        ty: BindingType::Buffer {
            ty,
            has_dynamic_offset: false,
            min_binding_size: None,
        },
        count: None,
    };
    let view_layout = device.create_bind_group_layout(
        "sky test view",
        &[
            binding(0, BufferBindingType::Uniform),
            binding(11, BufferBindingType::Uniform),
        ],
    );
    let empty_layout = device.create_bind_group_layout("sky test empty", &[]);
    let sky_layout = device.create_bind_group_layout(
        "sky test storage",
        &[binding(0, BufferBindingType::Storage { read_only: true })],
    );
    // O layout do pipeline tem de ter UM slot por grupo até ao grupo do
    // material (0 = view, 1..MATERIAL_BIND_GROUP-1 vazios, MATERIAL = storage
    // do céu). Hardcodar 3 grupos e ligar o storage ao 2 falhava a validação
    // ("Binding is missing from the pipeline layout") quando o bevy 0.19.1
    // passou o MATERIAL_BIND_GROUP_INDEX para 3 — estes testes GPU são
    // `#[ignore]`, por isso o desvio ficou invisível até 2026-09-08.
    // O slice tem de ser LITERAL (um Vec intermédio fixa o tipo antes da
    // coerção e os lifetimes `&'a [Option<&'a BindGroupLayout<'a>>]` do
    // descriptor não unificam) — o assert mantém o acoplamento honesto.
    assert_eq!(
        MATERIAL_BIND_GROUP, 3,
        "bevy mudou o grupo do material — atualizar o layout de grupos do harness"
    );
    let layout = device.create_pipeline_layout(&PipelineLayoutDescriptor {
        label: None,
        bind_group_layouts: &[
            Some(&view_layout),
            Some(&empty_layout),
            Some(&empty_layout),
            Some(&sky_layout),
        ],
        immediate_size: 0,
    });
    let pipeline = device.create_render_pipeline(&RawRenderPipelineDescriptor {
        label: Some("sky fullscreen"),
        layout: Some(&layout),
        vertex: RawVertexState {
            module: &shader,
            entry_point: Some("qa_vertex"),
            compilation_options: Default::default(),
            buffers: &[],
        },
        fragment: Some(RawFragmentState {
            module: &shader,
            entry_point: Some("fragment"),
            compilation_options: Default::default(),
            targets: &[Some(ColorTargetState {
                format: TextureFormat::Rgba32Float,
                blend: None,
                write_mask: ColorWrites::ALL,
            })],
        }),
        primitive: Default::default(),
        depth_stencil: None,
        multisample: Default::default(),
        multiview_mask: None,
        cache: None,
    });
    let view = device.create_buffer_with_data(&BufferInitDescriptor {
        label: None,
        contents: &floats([
            1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0,
        ]),
        usage: BufferUsages::UNIFORM,
    });
    let globals = device.create_buffer_with_data(&BufferInitDescriptor {
        label: None,
        contents: &[0; 16],
        usage: BufferUsages::UNIFORM,
    });
    let storage = device.create_buffer_with_data(&BufferInitDescriptor {
        label: None,
        contents: &floats(sky.into_iter().flatten()),
        usage: BufferUsages::STORAGE,
    });
    let view_group = device.create_bind_group(
        None,
        &view_layout,
        &[
            BindGroupEntry {
                binding: 0,
                resource: view.as_entire_binding(),
            },
            BindGroupEntry {
                binding: 11,
                resource: globals.as_entire_binding(),
            },
        ],
    );
    let empty_group = device.create_bind_group(None, &empty_layout, &[]);
    let sky_group = device.create_bind_group(
        None,
        &sky_layout,
        &[BindGroupEntry {
            binding: 0,
            resource: storage.as_entire_binding(),
        }],
    );
    let extent = Extent3d {
        width,
        height,
        depth_or_array_layers: 1,
    };
    let texture = device.create_texture(&TextureDescriptor {
        label: None,
        size: extent,
        mip_level_count: 1,
        sample_count: 1,
        dimension: TextureDimension::D2,
        format: TextureFormat::Rgba32Float,
        usage: TextureUsages::RENDER_ATTACHMENT | TextureUsages::COPY_SRC,
        view_formats: &[],
    });
    let target = texture.create_view(&Default::default());
    let readback = device.create_buffer(&BufferDescriptor {
        label: None,
        size: u64::from(width * height * 16),
        usage: BufferUsages::COPY_DST | BufferUsages::MAP_READ,
        mapped_at_creation: false,
    });
    let mut encoder = device.create_command_encoder(&Default::default());
    {
        let attachments = [Some(RenderPassColorAttachment {
            view: &target,
            resolve_target: None,
            depth_slice: None,
            ops: Operations {
                load: LoadOp::Clear(Default::default()),
                store: StoreOp::Store,
            },
        })];
        let mut pass = encoder.begin_render_pass(&RenderPassDescriptor {
            label: None,
            color_attachments: &attachments,
            depth_stencil_attachment: None,
            timestamp_writes: None,
            occlusion_query_set: None,
            multiview_mask: None,
        });
        pass.set_pipeline(&pipeline);
        pass.set_bind_group(0, &*view_group, &[]);
        pass.set_bind_group(1, &*empty_group, &[]);
        pass.set_bind_group(MATERIAL_BIND_GROUP, &*sky_group, &[]);
        pass.draw(0..3, 0..1);
    }
    encoder.copy_texture_to_buffer(
        TexelCopyTextureInfo {
            texture: &texture,
            mip_level: 0,
            origin: Origin3d::ZERO,
            aspect: TextureAspect::All,
        },
        TexelCopyBufferInfo {
            buffer: &readback,
            layout: TexelCopyBufferLayout {
                offset: 0,
                bytes_per_row: Some(width * 16),
                rows_per_image: Some(height),
            },
        },
        extent,
    );
    queue.submit([encoder.finish()]);
    let (tx, rx) = mpsc::channel();
    readback.slice(..).map_async(MapMode::Read, move |result| {
        tx.send(result).unwrap();
    });
    device
        .poll(PollType::Wait {
            submission_index: None,
            timeout: Some(Duration::from_secs(60)),
        })
        .expect("GPU poll");
    rx.recv_timeout(Duration::from_secs(60))
        .expect("GPU readback callback")
        .expect("GPU readback mapping");
    let mapped = readback.slice(..).get_mapped_range();
    let pixels = mapped
        .chunks_exact(16)
        .map(|pixel| {
            std::array::from_fn(|i| f32::from_le_bytes(pixel[i * 4..i * 4 + 4].try_into().unwrap()))
        })
        .collect();
    drop(mapped);
    readback.unmap();
    pixels
}

fn assert_pixels(label: &str, pixels: &[[f32; 4]]) {
    let (width, height) = dimensions();
    assert_eq!(pixels.len(), (width * height) as usize);
    for (i, pixel) in pixels.iter().enumerate() {
        assert!(
            pixel.iter().all(|x| x.is_finite()),
            "{label}: non-finite pixel {i}: {pixel:?}"
        );
        assert!(
            pixel[..3].iter().all(|x| *x >= -1e-5),
            "{label}: negative radiance at {i}: {pixel:?}"
        );
        assert!(
            (pixel[3] - 1.0).abs() < 1e-6,
            "{label}: nonopaque pixel {i}"
        );
    }
    let min = pixels.iter().map(|p| p[2]).fold(f32::INFINITY, f32::min);
    let max = pixels
        .iter()
        .map(|p| p[2])
        .fold(f32::NEG_INFINITY, f32::max);
    assert!(max - min > 1e-4, "{label}: flat/unrendered image");
}

fn capture(label: &str, pixels: &[[f32; 4]]) {
    let Ok(dir) = std::env::var("VIBER_SKY_CAPTURE_DIR") else {
        return;
    };
    let dir = Path::new(&dir);
    std::fs::create_dir_all(dir).unwrap();
    let srgb = |linear: f32| {
        let x = linear.max(0.0) / (1.0 + linear.max(0.0));
        let x = if x <= 0.0031308 {
            x * 12.92
        } else {
            1.055 * x.powf(1.0 / 2.4) - 0.055
        };
        (x.clamp(0.0, 1.0) * 255.0).round() as u8
    };
    let bytes: Vec<u8> = pixels
        .iter()
        .flat_map(|p| [srgb(p[0]), srgb(p[1]), srgb(p[2]), 255])
        .collect();
    image::save_buffer(
        dir.join(format!("{label}.png")),
        &bytes,
        dimensions().0,
        dimensions().1,
        image::ColorType::Rgba8,
    )
    .unwrap();
    std::fs::write(
        dir.join(format!("{label}.rgba32f")),
        floats(pixels.iter().flatten().copied()),
    )
    .unwrap();
}

#[test]
#[ignore = "requires a Vulkan/Metal/DX12 adapter; explicit real-GPU regression and optional captures"]
fn sky_gpu_finite_day_night_and_zero_clouds() {
    let options = WgpuSettings {
        priority: WgpuSettingsPriority::WebGPU,
        ..Default::default()
    };
    let resources = bevy::tasks::block_on(initialize_renderer(
        options.backends.unwrap(),
        None,
        &options,
    ));
    eprintln!("Sky GPU adapter: {:?}", resources.3.get_info());
    let (device, queue) = (&resources.0, &resources.1);
    let config = SkyConfig {
        drive: true,
        ..Default::default()
    };
    let day = render(device, queue, &config, atmosphere(false, 0.0, 120.0), false);
    let night = render(device, queue, &config, atmosphere(true, 0.0, 120.0), false);
    let dawn = render(device, queue, &config, dawn_atmosphere(), false);
    for (label, pixels) in [("day", &day), ("night", &night), ("dawn", &dawn)] {
        assert_pixels(label, pixels);
        capture(label, pixels);
    }
    let mean = |pixels: &[[f32; 4]]| {
        pixels
            .iter()
            .map(|p| (p[0] + p[1] + p[2]) as f64 / 3.0)
            .sum::<f64>()
            / pixels.len() as f64
    };
    assert!(
        mean(&day) > mean(&night) * 1.5,
        "day/night radiance not separated"
    );
    let extreme = SkyConfig {
        drive: false,
        sun_elevation: 90.0,
        mie_g: 1.0,
        cloud_coverage: 1.0,
        cloud_density: 4.0,
        ..config.clone()
    };
    let pixels = render(device, queue, &extreme, atmosphere(false, 0.0, 120.0), true);
    assert_pixels("static-zenith-extreme", &pixels);
    capture("static-zenith-extreme", &pixels);
    let mut midnight = atmosphere(true, 0.0, 120.0);
    midnight[5][3] = 1439.99;
    let before = render(device, queue, &config, midnight, false);
    midnight[5][3] = 0.01;
    let after = render(device, queue, &config, midnight, false);
    let error = before
        .iter()
        .zip(&after)
        .flat_map(|(a, b)| a.iter().zip(b).map(|(a, b)| (a - b).abs()))
        .fold(0.0_f32, f32::max);
    assert!(
        error < 2e-5,
        "midnight changes fixed-geometry lunar phase: {error}"
    );
    // Coverage zero must remove ALL cloud layers even at maximum density.
    // Density zero must do the same even at maximum coverage. Compare actual
    // pixels, not source text or a separate CPU reimplementation of noise.
    for top in [false, true] {
        let clear = SkyConfig {
            cloud_coverage: 0.0,
            cloud_density: 0.0,
            ..config.clone()
        };
        let dense = SkyConfig {
            cloud_density: 1.0,
            ..clear.clone()
        };
        let covered = SkyConfig {
            cloud_coverage: 1.0,
            ..clear.clone()
        };
        let base = render(device, queue, &clear, atmosphere(false, 0.0, 120.0), top);
        assert_pixels("clear", &base);
        capture(if top { "clear-zenith" } else { "clear-horizon" }, &base);
        for (label, variant) in [("zero coverage", dense), ("zero density", covered)] {
            let pixels = render(device, queue, &variant, atmosphere(false, 0.0, 120.0), top);
            assert_pixels(label, &pixels);
            let error = base
                .iter()
                .zip(&pixels)
                .flat_map(|(a, b)| a.iter().zip(b).map(|(a, b)| (a - b).abs()))
                .fold(0.0_f32, f32::max);
            assert!(
                error <= 2e-5,
                "{label} changed clear-sky pixels (top={top}, max error={error})"
            );
        }
    }
}

#[test]
#[ignore = "requires a Vulkan/Metal/DX12 adapter; explicit real-GPU regression and optional captures"]
fn sky_gpu_nishita_finite_and_day_night_separation() {
    let options = WgpuSettings {
        priority: WgpuSettingsPriority::WebGPU,
        ..Default::default()
    };
    let resources = bevy::tasks::block_on(initialize_renderer(
        options.backends.unwrap(),
        None,
        &options,
    ));
    eprintln!("Sky GPU adapter: {:?}", resources.3.get_info());
    let (device, queue) = (&resources.0, &resources.1);
    let config = SkyConfig {
        drive: true,
        model: viber::sky::SkyModel::Nishita,
        ..Default::default()
    };
    let day = render(device, queue, &config, atmosphere(false, 0.0, 120.0), false);
    let night = render(device, queue, &config, atmosphere(true, 0.0, 120.0), false);
    let dawn = render(device, queue, &config, dawn_atmosphere(), false);
    for (label, pixels) in [("nishita-day", &day), ("nishita-night", &night), ("nishita-dawn", &dawn)] {
        assert_pixels(label, pixels);
        capture(label, pixels);
    }
    let mean = |pixels: &[[f32; 4]]| {
        pixels
            .iter()
            .map(|p| (p[0] + p[1] + p[2]) as f64 / 3.0)
            .sum::<f64>()
            / pixels.len() as f64
    };
    assert!(
        mean(&day) > mean(&night) * 1.5,
        "nishita day/night radiance not separated"
    );
}
