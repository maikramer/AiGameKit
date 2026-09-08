// Metallic detection (F5): two-tier HSL detector over a gray-world-balanced
// diffuse (or intrinsic albedo when the F6 pre-pass ran), luminance-variance
// damping, vegetation veto, and specular evidence (direct residual from the
// intrinsic pre-pass when available).

@group(0) @binding(0)
var input_texture: texture_2d<f32>;

@group(0) @binding(1)
var specular_texture: texture_2d<f32>;

@group(0) @binding(2)
var output_texture: texture_storage_2d<rgba8unorm, write>;

@group(1) @binding(0)
var<uniform> params: Params;

fn sample_coord(coords: vec2<i32>) -> vec2<i32> {
    let dims = textureDimensions(input_texture);
    return wrap_or_clamp(coords, dims, params.seamless);
}

fn sample_rgb(coords: vec2<i32>) -> vec3<f32> {
    return textureLoad(input_texture, sample_coord(coords), 0).rgb;
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

// Two-tier detector. Achromatic group (sat < 0.15) covers steel/silver/
// aluminum/titanium/pewter/chrome/blue-steel; chromatic group uses
// non-overlapping hue bands (copper/bronze/gold/brass).
fn detect_metallic(rgb: vec3<f32>) -> f32 {
    let hsl = rgb_to_hsl(rgb);
    let h = hsl.x;
    let s = hsl.y;
    let l = hsl.z;

    var metallic = 0.0;

    if (s < 0.15 && l > 0.30 && l < 0.92) {
        let lum_factor = smooth_step(0.30, 0.85, l);
        let sat_factor = 1.0 - smooth_step(0.0, 0.15, s);
        var blue_factor = 1.0;
        if (h > 0.55 && h < 0.68) {
            blue_factor = 1.15;
        }
        metallic = max(metallic, clamp(lum_factor * sat_factor * blue_factor, 0.0, 1.0));
    }

    if (s >= 0.30 && l > 0.20) {
        var chromatic = 0.0;
        if (h >= 0.00 && h < 0.06) {
            chromatic = max(chromatic, 1.0 - abs(h - 0.03) * 16.0);
        } else if (h >= 0.06 && h < 0.09) {
            chromatic = max(chromatic, 1.0 - abs(h - 0.075) * 33.0);
        } else if (h >= 0.09 && h < 0.14) {
            chromatic = max(chromatic, 1.0 - abs(h - 0.115) * 22.0);
        } else if (h >= 0.14 && h < 0.17) {
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

fn local_luma_variance_3x3(center: vec2<i32>) -> f32 {
    var sum = 0.0;
    var sum_sq = 0.0;
    let n = 9.0;
    for (var dy = -1; dy <= 1; dy++) {
        for (var dx = -1; dx <= 1; dx++) {
            let rgb = sample_rgb(center + vec2<i32>(dx, dy));
            let luma = luma709(rgb);
            sum += luma;
            sum_sq += luma * luma;
        }
    }
    let mean = sum / n;
    return clamp((sum_sq / n) - mean * mean, 0.0, 0.25);
}

fn green_neighbourhood_fraction(center: vec2<i32>) -> f32 {
    var green = 0.0;
    let n = 25.0;
    for (var dy = -4; dy <= 4; dy += 2) {
        for (var dx = -4; dx <= 4; dx += 2) {
            let hsl = rgb_to_hsl(sample_rgb(center + vec2<i32>(dx, dy)));
            if (hsl.x > 0.17 && hsl.x < 0.46 && hsl.y > 0.15) {
                green += 1.0;
            }
        }
    }
    return green / n;
}

@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(input_texture);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    var color = textureLoad(input_texture, coords, 0).rgb;

    // Gray-world white balance (Buchsbaum 1980): cancels illumination colour
    // temperature so the hue bands stay meaningful under tinted light.
    if (params.metallic_gray_world == 1u) {
        color = clamp(color * vec3<f32>(params.gray_gain_r, params.gray_gain_g, params.gray_gain_b), vec3<f32>(0.0), vec3<f32>(4.0));
    }

    let raw = detect_metallic(color);

    let variance = local_luma_variance_3x3(coords);
    let variance_factor = params.metallic_local_variance_factor;
    let damping = 1.0 - variance_factor * clamp(variance * 4.0, 0.0, 1.0);

    let green_frac = green_neighbourhood_fraction(coords);
    let vegetation_veto = 1.0 - smooth_step(0.30, 0.60, green_frac);

    var metallic = raw * params.metallic_scale * damping * vegetation_veto;

    // Specular evidence: bright desaturated highlights are metal-only in
    // PBR (dielectrics cap specular at ~0.04). Prefer the intrinsic residual
    // when present; fall back to the heuristic luma/saturation shape.
    if (params.metallic_specular_gain > 0.0) {
        let sp_dims = textureDimensions(specular_texture);
        let has_specular = sp_dims.x > 1u;
        let spec = select(
            smooth_step(0.75, 0.95, luma709(color)) * (1.0 - rgb_to_hsl(color).y),
            textureLoad(specular_texture, coords, 0).r,
            has_specular,
        );
        metallic += params.metallic_specular_gain * spec * damping * vegetation_veto;
    }

    metallic = clamp(metallic, 0.0, 1.0);

    textureStore(output_texture, coords, vec4<f32>(metallic, 0.0, 0.0, 1.0));
}
