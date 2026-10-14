//! Hillshade minimap rendering for baked heightmaps, plus the overlay primitives used to
//! draw feature outlines on top of it.
//!
//! Everything here is plain Rgba8 math — no `image` crate dependency: the buffer converts
//! straight into a [`bevy::image::Image`] for display in an `ImageNode`.

use bevy::asset::RenderAssetUsages;
use bevy::image::Image;
use bevy::render::render_resource::{Extent3d, TextureDimension, TextureFormat};

use super::ahgt::Ahgt;

/// Ambient light floor so flat terrain stays visible instead of black.
const AMBIENT: f32 = 0.35;
/// Light direction (x east, y up, z south): from the north-west, elevated.
const LIGHT: [f32; 3] = [-0.55, 0.62, -0.55];
/// Fallbacks when the `.ahgt` metadata does not declare the world extents.
const FALLBACK_WORLD_SIZE: f32 = 4000.0;
const FALLBACK_MAX_HEIGHT: f32 = 200.0;

/// An RGBA8 image buffer, row-major, ready for feature overlays and bevy conversion.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Minimap {
    pub width: u32,
    pub height: u32,
    pub pixels: Vec<u8>,
}

impl Minimap {
    /// A minimap filled with a single color.
    pub fn filled(width: u32, height: u32, rgba: [u8; 4]) -> Self {
        Self {
            width,
            height,
            pixels: rgba.repeat((width as usize) * (height as usize)),
        }
    }

    /// Reads one pixel; out-of-bounds coordinates read as transparent black.
    #[cfg_attr(
        not(test),
        expect(dead_code, reason = "pixel read API used by the minimap tests")
    )]
    pub fn pixel(&self, x: i32, y: i32) -> [u8; 4] {
        if self.contains(x, y) {
            let i = (y as usize * self.width as usize + x as usize) * 4;
            self.pixels[i..i + 4].try_into().unwrap()
        } else {
            [0, 0, 0, 0]
        }
    }

    /// Writes one pixel, clamped to the canvas (out-of-bounds writes are dropped).
    pub fn set_pixel(&mut self, x: i32, y: i32, rgba: [u8; 4]) {
        if self.contains(x, y) {
            let i = (y as usize * self.width as usize + x as usize) * 4;
            self.pixels[i..i + 4].copy_from_slice(&rgba);
        }
    }

    fn contains(&self, x: i32, y: i32) -> bool {
        x >= 0 && y >= 0 && (x as u32) < self.width && (y as u32) < self.height
    }

    /// Draws an axis-aligned rectangle outline (pads).
    pub fn draw_rect_outline(
        &mut self,
        center: [f32; 2],
        half_size: [f32; 2],
        thickness: f32,
        color: [u8; 4],
    ) {
        let (cx, cy) = (center[0], center[1]);
        let (hx, hy) = (half_size[0], half_size[1]);
        self.draw_rect_filled([cx - hx, cy - hy], [cx + hx, cy - hy], color);
        self.draw_rect_filled([cx - hx, cy + hy - thickness], [cx + hx, cy + hy], color);
        self.draw_rect_filled([cx - hx, cy - hy], [cx - hx + thickness, cy + hy], color);
        self.draw_rect_filled([cx + hx - thickness, cy - hy], [cx + hx, cy + hy], color);
    }

    /// Fills an axis-aligned rectangle between two corners (pixel coordinates).
    fn draw_rect_filled(&mut self, a: [f32; 2], b: [f32; 2], color: [u8; 4]) {
        let x0 = a[0].floor().max(0.0) as i32;
        let y0 = a[1].floor().max(0.0) as i32;
        let x1 = (b[0].ceil() as i32).min(self.width as i32 - 1);
        let y1 = (b[1].ceil() as i32).min(self.height as i32 - 1);
        for y in y0..=y1 {
            for x in x0..=x1 {
                self.set_pixel(x, y, color);
            }
        }
    }

    /// Draws a circle outline (exclusions, lakes).
    pub fn draw_circle_outline(
        &mut self,
        center: [f32; 2],
        radius: f32,
        thickness: f32,
        color: [u8; 4],
    ) {
        let steps = (radius.max(2.0) * 8.0).ceil() as usize;
        for step in 0..steps {
            let angle = step as f32 / steps as f32 * std::f32::consts::TAU;
            let point = [
                center[0] + angle.cos() * radius,
                center[1] + angle.sin() * radius,
            ];
            self.stamp(point, thickness, color);
        }
    }

    /// Draws a filled disc (point markers: cliffs, arches, caves, waypoints).
    pub fn draw_disc(&mut self, center: [f32; 2], radius: f32, color: [u8; 4]) {
        let r = radius.ceil() as i32;
        let cx = center[0].round() as i32;
        let cy = center[1].round() as i32;
        for dy in -r..=r {
            for dx in -r..=r {
                if dx * dx + dy * dy <= r * r {
                    self.set_pixel(cx + dx, cy + dy, color);
                }
            }
        }
    }

    /// Draws a polyline (rivers, roads) in pixel coordinates.
    pub fn draw_polyline(&mut self, points: &[[f32; 2]], thickness: f32, color: [u8; 4]) {
        for pair in points.windows(2) {
            self.draw_segment(pair[0], pair[1], thickness, color);
        }
    }

    /// Draws one line segment by stamping squares along it.
    fn draw_segment(&mut self, a: [f32; 2], b: [f32; 2], thickness: f32, color: [u8; 4]) {
        let dx = b[0] - a[0];
        let dy = b[1] - a[1];
        let steps = (dx.hypot(dy).ceil() as usize).max(1);
        for step in 0..=steps {
            let t = step as f32 / steps as f32;
            self.stamp([a[0] + dx * t, a[1] + dy * t], thickness, color);
        }
    }

    /// Stamps a `thickness`-sized square centered on a point.
    fn stamp(&mut self, point: [f32; 2], thickness: f32, color: [u8; 4]) {
        let half = (thickness / 2.0).ceil().max(0.5);
        let x0 = (point[0] - half).floor() as i32;
        let y0 = (point[1] - half).floor() as i32;
        let span = (half * 2.0).ceil() as i32;
        for dy in 0..span {
            for dx in 0..span {
                self.set_pixel(x0 + dx, y0 + dy, color);
            }
        }
    }
}

/// Renders an `.ahgt` heightmap as a square grayscale hillshade minimap.
///
/// The grid is downsampled per output pixel by taking the **max** of each block — ridgelines
/// must survive downsampling or mountains flatten into noise. Shading uses a north-west
/// light: slopes facing away from it darken, with an ambient floor and a mild altitude lift
/// so high ground reads brighter even on unlit flanks.
pub fn render_minimap(ahgt: &Ahgt, width_px: u32) -> Minimap {
    let side = width_px.max(1);
    let mut minimap = Minimap::filled(side, side, [0, 0, 0, 255]);

    let gw = ahgt.width.max(1);
    let gd = ahgt.depth.max(1);
    let world_size = ahgt.meta.world_size.unwrap_or(FALLBACK_WORLD_SIZE).max(1.0);
    let max_height = ahgt.meta.max_height.unwrap_or(FALLBACK_MAX_HEIGHT).max(1.0);

    // Block-max downsample to `side x side` normalized heights.
    let mut heights = vec![0.0f32; (side * side) as usize];
    for iz in 0..side as usize {
        let z0 = iz * gd / side as usize;
        let z1 = (((iz + 1) * gd / side as usize).max(z0 + 1)).min(gd);
        for ix in 0..side as usize {
            let x0 = ix * gw / side as usize;
            let x1 = (((ix + 1) * gw / side as usize).max(x0 + 1)).min(gw);
            let mut block_max = 0.0f32;
            for z in z0..z1 {
                for x in x0..x1 {
                    block_max = block_max.max(ahgt.normalized(x, z));
                }
            }
            heights[iz * side as usize + ix] = block_max;
        }
    }

    // Hillshade with a north-west light; gradients in meters per meter.
    let cell_m = world_size / side as f32;
    let relief = max_height / cell_m; // converts normalized gradients to real slopes
    let light = normalize3(LIGHT);
    let at = |ix: i32, iz: i32| -> f32 {
        heights[iz.clamp(0, side as i32 - 1) as usize * side as usize
            + ix.clamp(0, side as i32 - 1) as usize]
    };
    for iz in 0..side as i32 {
        for ix in 0..side as i32 {
            let h = at(ix, iz);
            let gx = (at(ix + 1, iz) - at(ix - 1, iz)) * 0.5 * relief;
            let gz = (at(ix, iz + 1) - at(ix, iz - 1)) * 0.5 * relief;
            let normal = normalize3([-gx, 1.0, -gz]);
            let lambert = (normal[0] * light[0] + normal[1] * light[1] + normal[2] * light[2])
                .clamp(0.0, 1.0);
            let value = AMBIENT + (1.0 - AMBIENT) * lambert;
            let value = value * (0.85 + 0.15 * h);
            let gray = (value * 255.0).round().clamp(0.0, 255.0) as u8;
            minimap.set_pixel(ix, iz, [gray, gray, gray, 255]);
        }
    }
    minimap
}

/// Converts a rendered minimap into a bevy texture for an `ImageNode`.
pub fn minimap_to_bevy_image(minimap: &Minimap) -> Image {
    Image::new(
        Extent3d {
            width: minimap.width,
            height: minimap.height,
            depth_or_array_layers: 1,
        },
        TextureDimension::D2,
        minimap.pixels.clone(),
        TextureFormat::Rgba8UnormSrgb,
        RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
    )
}

fn normalize3(v: [f32; 3]) -> [f32; 3] {
    let len = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt();
    if len < 1e-6 {
        [0.0, 1.0, 0.0]
    } else {
        [v[0] / len, v[1] / len, v[2] / len]
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::doc::ahgt::AhgtMeta;

    fn ahgt_from(width: usize, depth: usize, heights: Vec<u16>) -> Ahgt {
        Ahgt {
            width,
            depth,
            data: heights,
            meta: AhgtMeta {
                world_size: Some(1000.0),
                max_height: Some(100.0),
            },
        }
    }

    fn brightness(map: &Minimap, x: i32, y: i32) -> u8 {
        map.pixel(x, y)[0]
    }

    #[test]
    fn flat_grid_renders_uniform_gray() {
        let map = render_minimap(&ahgt_from(64, 64, vec![1000; 64 * 64]), 32);
        assert_eq!(map.width, 32);
        assert_eq!(map.height, 32);
        let first = map.pixel(0, 0);
        for y in 0..32 {
            for x in 0..32 {
                assert_eq!(map.pixel(x, y), first, "flat terrain must shade uniformly");
                assert_eq!(map.pixel(x, y)[3], 255);
            }
        }
        // Flat ground faces straight up: ambient + full lambert, no altitude spread.
        assert!(
            first[0] > 120,
            "flat ground should be mid-bright, got {:?}",
            first
        );
    }

    #[test]
    fn mountain_shades_brighter_on_the_northwest_flank() {
        // A single bump: heights fall off with distance from the center.
        let side = 64;
        let mut heights = vec![0u16; side * side];
        for z in 0..side {
            for x in 0..side {
                let d = (((x as f32 - 32.0).powi(2) + (z as f32 - 32.0).powi(2)) / 200.0).sqrt();
                heights[z * side + x] = (u16::MAX as f32 * (1.0 - (d / 8.0).min(1.0))) as u16;
            }
        }
        let map = render_minimap(&ahgt_from(side, side, heights), 64);

        // Same rows, columns equidistant from the peak: west flank vs east flank.
        let west = (20..28)
            .map(|x| brightness(&map, x, 32) as u32)
            .sum::<u32>();
        let east = (36..44)
            .map(|x| brightness(&map, x, 32) as u32)
            .sum::<u32>();
        assert!(
            west > east + 40,
            "NW-lit hill must shade west brighter than east (west {west} vs east {east})"
        );
        // And the shading varies across the map at all.
        let values: Vec<u8> = (0..64 * 64)
            .map(|i| brightness(&map, i / 64, i % 64))
            .collect();
        let distinct = values
            .iter()
            .collect::<std::collections::HashSet<_>>()
            .len();
        assert!(
            distinct > 8,
            "relief must produce varied brightness, got {distinct} values"
        );
    }

    #[test]
    fn downsampling_keeps_dimensions_for_any_grid_ratio() {
        // Grid larger, smaller and equal to the target size.
        for (gw, gd) in [(200, 100), (10, 10), (32, 32)] {
            let map = render_minimap(&ahgt_from(gw, gd, vec![0; gw * gd]), 48);
            assert_eq!((map.width, map.height), (48, 48));
            assert_eq!(map.pixels.len(), 48 * 48 * 4);
        }
    }

    #[test]
    fn overlay_primitives_stay_inside_the_canvas() {
        let mut map = Minimap::filled(40, 40, [10, 10, 10, 255]);
        // Deliberately off-canvas geometry: nothing may panic or resize the buffer.
        map.draw_rect_outline([-30.0, -30.0], [100.0, 60.0], 2.0, [255, 0, 0, 255]);
        map.draw_circle_outline([500.0, 500.0], 30.0, 2.0, [0, 255, 0, 255]);
        map.draw_disc([-5.0, -5.0], 4.0, [0, 0, 255, 255]);
        map.draw_polyline(
            &[[-10.0, 20.0], [20.0, 20.0], [20.0, 90.0]],
            2.0,
            [255, 255, 0, 255],
        );
        assert_eq!(map.width, 40);
        assert_eq!(map.pixels.len(), 40 * 40 * 4);

        // The on-canvas parts did land: the rect's bottom edge (y ≈ 28–30)
        // crosses the canvas, and the polyline runs along y = 20.
        assert_eq!(
            map.pixel(10, 29),
            [255, 0, 0, 255],
            "rect bottom edge must be drawn"
        );
        assert_eq!(
            map.pixel(10, 20),
            [255, 255, 0, 255],
            "polyline must be drawn"
        );
        assert_eq!(map.pixel(0, 0)[3], 255);
    }

    #[test]
    fn converts_to_bevy_image_with_matching_size() {
        let map = render_minimap(&ahgt_from(16, 16, vec![0; 256]), 24);
        let image = minimap_to_bevy_image(&map);
        let size = &image.texture_descriptor.size;
        assert_eq!((size.width, size.height), (24, 24));
        assert_eq!(
            image.texture_descriptor.format,
            TextureFormat::Rgba8UnormSrgb
        );
        assert_eq!(image.data.as_deref().map(<[u8]>::len), Some(24 * 24 * 4));
    }
}
