struct Params {
    height_blur_radius_0: f32,           // 1
    height_blur_radius_1: f32,           // 2
    height_blur_radius_2: f32,           // 3
    height_contrast: f32,                // 4
    normal_strength: f32,                // 5
    normal_flip_y: u32,                  // 6  (NEW 2.0)
    metallic_scale: f32,                 // 7
    metallic_local_variance_factor: f32, // 8  (NEW 2.0)
    smoothness_base: f32,                // 9
    smoothness_metallic_boost: f32,      // 10
    smoothness_roughness_factor: f32,    // 11 (NEW 2.0)
    edge_contrast: f32,                  // 12
    ao_depth_scale: f32,                 // 13
    seamless: u32,                       // 14 (NEW 2.0)
    _pad0: f32,                          // 15
    _pad1: f32,                          // 16
}

@group(0) @binding(0)
var input_texture: texture_2d<f32>;

@group(0) @binding(1)
var output_texture: texture_storage_2d<rgba8unorm, write>;

@group(1) @binding(0)
var<uniform> params: Params;

fn sample_coord(coords: vec2<i32>, dims: vec2<u32>) -> vec2<i32> {
    let d = vec2<i32>(dims);
    if (params.seamless == 1u) {
        return ((coords % d) + d) % d;
    }
    return clamp(coords, vec2<i32>(0), d - vec2<i32>(1));
}

fn rgb_to_hsl(rgb: vec3<f32>) -> vec3<f32> {
    let max_val = max(max(rgb.r, rgb.g), rgb.b);
    let min_val = min(min(rgb.r, rgb.g), rgb.b);
    let delta = max_val - min_val;

    let l = (max_val + min_val) * 0.5;

    var s = 0.0;
    let denom = max(1e-6, 1.0 - abs(2.0 * l - 1.0));
    if (delta > 0.0) {
        s = delta / denom;
    }

    var h = 0.0;
    if (delta > 0.0) {
        if (max_val == rgb.r) {
            h = (rgb.g - rgb.b) / delta;
            if (rgb.g < rgb.b) {
                h += 6.0;
            }
        } else if (max_val == rgb.g) {
            h = (rgb.b - rgb.r) / delta + 2.0;
        } else {
            h = (rgb.r - rgb.g) / delta + 4.0;
        }
        h = h / 6.0;
    }

    return vec3<f32>(h, s, l);
}

fn smooth_step(edge0: f32, edge1: f32, x: f32) -> f32 {
    let t = clamp((x - edge0) / (edge1 - edge0), 0.0, 1.0);
    return t * t * (3.0 - 2.0 * t);
}

// Two-tier detector. Achromatic group (sat < 0.15) covers steel/silver/aluminum/
// titanium/pewter/chrome/blue-steel; chromatic group uses non-overlapping hue bands.
fn detect_metallic(rgb: vec3<f32>) -> f32 {
    let hsl = rgb_to_hsl(rgb);
    let h = hsl.x;
    let s = hsl.y;
    let l = hsl.z;

    var metallic = 0.0;

    // === Achromatic metals (gray) ===
    if (s < 0.15 && l > 0.30 && l < 0.92) {
        let lum_factor = smooth_step(0.30, 0.85, l);
        let sat_factor = 1.0 - smooth_step(0.0, 0.15, s);
        // Blue tint bonus (titanium blue / blue steel).
        var blue_factor = 1.0;
        if (h > 0.55 && h < 0.68) {
            blue_factor = 1.15;
        }
        metallic = max(metallic, clamp(lum_factor * sat_factor * blue_factor, 0.0, 1.0));
    }

    // === Chromatic metals (sat >= 0.30), non-overlapping hue bands ===
    if (s >= 0.30 && l > 0.20) {
        var chromatic = 0.0;
        if (h >= 0.00 && h < 0.06) {
            // Copper.
            chromatic = max(chromatic, 1.0 - abs(h - 0.03) * 16.0);
        } else if (h >= 0.06 && h < 0.09) {
            // Bronze.
            chromatic = max(chromatic, 1.0 - abs(h - 0.075) * 33.0);
        } else if (h >= 0.09 && h < 0.14) {
            // Gold.
            chromatic = max(chromatic, 1.0 - abs(h - 0.115) * 22.0);
        } else if (h >= 0.14 && h < 0.17) {
            // Brass.
            chromatic = max(chromatic, 1.0 - abs(h - 0.155) * 33.0);
        }

        if (chromatic > 0.0) {
            let lum_factor = smooth_step(0.20, 0.70, l);
            let sat_factor = smooth_step(0.30, 0.80, s);
            metallic = max(metallic, clamp(chromatic * lum_factor * sat_factor, 0.0, 1.0));
        }
    }

    return clamp(metallic, 0.0, 1.0);
}

// 3×3 luminance variance. Textured non-metals (concrete, gray stone) have high local
// variance; polished metals have low variance. Used to damp false positives (F2.5).
fn local_luma_variance_3x3(center: vec2<i32>, dims: vec2<u32>) -> f32 {
    var sum = 0.0;
    var sum_sq = 0.0;
    let n = 9.0;
    for (var dy = -1; dy <= 1; dy++) {
        for (var dx = -1; dx <= 1; dx++) {
            let c = sample_coord(center + vec2<i32>(dx, dy), dims);
            let rgb = textureLoad(input_texture, c, 0).rgb;
            let luma = dot(rgb, vec3<f32>(0.2126, 0.7152, 0.0722));
            sum += luma;
            sum_sq += luma * luma;
        }
    }
    let mean = sum / n;
    return clamp((sum_sq / n) - mean * mean, 0.0, 0.25);
}

@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(input_texture);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let color = textureLoad(input_texture, coords, 0).rgb;
    let raw = detect_metallic(color);

    let variance = local_luma_variance_3x3(coords, dims);
    let variance_factor = params.metallic_local_variance_factor;
    let damping = 1.0 - variance_factor * clamp(variance * 4.0, 0.0, 1.0);

    let metallic = clamp(raw * params.metallic_scale * damping, 0.0, 1.0);

    textureStore(output_texture, coords, vec4<f32>(metallic, 0.0, 0.0, 1.0));
}
