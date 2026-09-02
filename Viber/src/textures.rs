//! Mipmaps + anisotropic filtering for loaded world textures.
//!
//! Bevy's image loader does NOT generate mipmaps for plain PNG/JPG files
//! (only KTX2/DDS ship them) and the default sampler ships with
//! `anisotropy_clamp: 1`. World-space tiled ground textures (the simple-rpg
//! terrain tiles `vale_grass.png` every 5 m) then alias into crawling pixel
//! speckle — worst when the camera moves. This module generates a
//! box-filter mip chain CPU-side for uncompressed 4-byte-per-pixel images on
//! load, and `main.rs` raises the default sampler anisotropy to 8.

use bevy::asset::{AssetEvent, Assets};
use bevy::image::{Image, ImageSampler, ImageSamplerDescriptor};
use bevy::prelude::*;

/// Patch every freshly-loaded image: add a mip chain when the file has none.
pub fn generate_mipmaps_on_load(
    mut events: MessageReader<AssetEvent<Image>>,
    mut images: ResMut<Assets<Image>>,
) {
    for event in events.read() {
        match event {
            AssetEvent::Added { id } | AssetEvent::LoadedWithDependencies { id } => {
                if let Some(mut image) = images.get_mut(*id) {
                    // into_inner marca o asset como modificado → o render
                    // re-extrai e re-faz o upload agora com a cadeia de mips.
                    patch_image(image.into_inner());
                }
            }
            _ => {}
        }
    }
}

/// True when the format is plain uncompressed RGBA/BGRA (1×1 blocks, 4
/// bytes) — the only layouts the CPU box filter here understands.
fn is_plain_rgba(format: bevy::render::render_resource::TextureFormat) -> bool {
    use bevy::render::render_resource::TextureFormat;
    matches!(
        format,
        TextureFormat::Rgba8UnormSrgb
            | TextureFormat::Rgba8Unorm
            | TextureFormat::Bgra8UnormSrgb
            | TextureFormat::Bgra8Unorm
    )
}

/// Append a box-filter mip chain to `image` when it has a single mip level.
/// Idempotent: images that already carry mips (KTX2/DDS) pass through.
pub fn patch_image(image: &mut Image) {
    if image.texture_descriptor.mip_level_count > 1 {
        return;
    }
    if !is_plain_rgba(image.texture_descriptor.format) {
        return;
    }
    let Some(data) = image.data.as_ref() else {
        return;
    };
    let width = image.width();
    let height = image.height();
    if width == 0 || height == 0 {
        return;
    }
    let mut levels = Vec::new();
    levels.push(data.clone());
    let mut lw = width;
    let mut lh = height;
    while (lw > 1 || lh > 1) && levels.len() < 12 {
        let (nw, nh) = ((lw / 2).max(1), (lh / 2).max(1));
        let prev = levels.last().expect("levels is non-empty");
        let mut next = vec![0u8; (nw * nh * 4) as usize];
        for y in 0..nh {
            for x in 0..nw {
                for c in 0..4 {
                    let sx = (x * 2).min(lw - 1);
                    let sy = (y * 2).min(lh - 1);
                    let sx1 = (sx + 1).min(lw - 1);
                    let sy1 = (sy + 1).min(lh - 1);
                    let px = |xx: u32, yy: u32| ((yy * lw + xx) * 4 + c) as usize;
                    next[((y * nw + x) * 4 + c) as usize] = ((prev[px(sx, sy)] as u32
                        + prev[px(sx1, sy)] as u32
                        + prev[px(sx, sy1)] as u32
                        + prev[px(sx1, sy1)] as u32)
                        / 4) as u8;
                }
            }
        }
        levels.push(next);
        lw = nw;
        lh = nh;
    }
    if levels.len() <= 1 {
        return;
    }
    let total: usize = levels.iter().map(Vec::len).sum();
    let mut chain = Vec::with_capacity(total);
    for level in &levels {
        chain.extend_from_slice(level);
    }
    image.data = Some(chain);
    image.texture_descriptor.mip_level_count = levels.len() as u32;
    // Filtragem anisotrópica nas texturas de chão: em ângulo rasant o mip
    // único de maior compressão é que era amostrado, e é isso que cintila.
    // (wgpu exige todos os filtros lineares quando anisotropy > 1.)
    image.sampler = ImageSampler::Descriptor(ImageSamplerDescriptor {
        anisotropy_clamp: 8,
        ..ImageSamplerDescriptor::linear()
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use bevy::asset::RenderAssetUsages;
    use bevy::render::render_resource::{
        Extent3d, TextureDescriptor, TextureDimension, TextureFormat, TextureUsages,
    };

    fn rgba_image(width: u32, height: u32, fill: [u8; 4]) -> Image {
        let len = (width * height * 4) as usize;
        Image {
            data: Some(vec_of(fill, len)),
            data_order: Default::default(),
            texture_descriptor: TextureDescriptor {
                label: None,
                size: Extent3d {
                    width,
                    height,
                    depth_or_array_layers: 1,
                },
                mip_level_count: 1,
                sample_count: 1,
                dimension: TextureDimension::D2,
                format: TextureFormat::Rgba8UnormSrgb,
                usage: TextureUsages::TEXTURE_BINDING | TextureUsages::COPY_DST,
                view_formats: &[],
            },
            sampler: ImageSampler::Default,
            texture_view_descriptor: None,
            asset_usage: RenderAssetUsages::default(),
            copy_on_resize: false,
        }
    }

    fn vec_of(fill: [u8; 4], len: usize) -> Vec<u8> {
        let mut v = Vec::with_capacity(len);
        for i in 0..len {
            v.push(fill[i % 4]);
        }
        v
    }

    /// Sólido: a cadeia de mips preserva a cor e produz os níveis esperados.
    #[test]
    fn test_mip_chain_on_solid_color() {
        let mut image = rgba_image(8, 4, [255, 0, 0, 255]);
        patch_image(&mut image);
        // 8x4 → 4x2 → 2x1 → 1x1
        assert_eq!(image.texture_descriptor.mip_level_count, 4);
        let data = image.data.as_ref().expect("data present");
        assert_eq!(data.len(), (32 + 8 + 2 + 1) * 4);
        assert!(data.chunks(4).all(|px| px == [255, 0, 0, 255]));
    }

    /// Já tem mips (KTX2/DDS): passa intacto.
    #[test]
    fn test_patch_skips_existing_mips() {
        let mut image = rgba_image(4, 4, [1, 2, 3, 4]);
        image.texture_descriptor.mip_level_count = 3;
        let before = image.data.clone();
        patch_image(&mut image);
        assert_eq!(image.data, before);
        assert_eq!(image.texture_descriptor.mip_level_count, 3);
    }

    /// Formatos comprimidos/não-RGBA passam intactos.
    #[test]
    fn test_patch_skips_unsupported_format() {
        use bevy::render::render_resource::TextureFormat;
        let mut image = rgba_image(4, 4, [0; 4]);
        image.texture_descriptor.format = TextureFormat::R16Uint;
        let before = image.data.clone();
        patch_image(&mut image);
        assert_eq!(image.data, before);
        assert_eq!(image.texture_descriptor.mip_level_count, 1);
        assert!(is_plain_rgba(TextureFormat::Rgba8UnormSrgb));
        assert!(!is_plain_rgba(TextureFormat::R16Uint));
    }

    /// 1×1: sem cadeia a acrescentar, permanece com um nível.
    #[test]
    fn test_patch_1x1_is_noop() {
        let mut image = rgba_image(1, 1, [9, 9, 9, 255]);
        patch_image(&mut image);
        assert_eq!(image.texture_descriptor.mip_level_count, 1);
    }
}
