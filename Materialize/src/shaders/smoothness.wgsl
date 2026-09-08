// Smoothness (F5): luminance-contrast base + metallic boost, minus a GGX
// slope-variance proxy computed from the normal map — microfacet dispersion
// ⇒ noisy normals ⇒ higher roughness (Walter et al. 2007: α² = E[slope²]).

@group(0) @binding(0)
var diffuse_texture: texture_2d<f32>;

@group(0) @binding(1)
var metallic_texture: texture_2d<f32>;

@group(0) @binding(2)
var normal_texture: texture_2d<f32>;

@group(0) @binding(3)
var output_texture: texture_storage_2d<rgba8unorm, write>;

@group(1) @binding(0)
var<uniform> params: Params;

fn sample_coord(coords: vec2<i32>) -> vec2<i32> {
    let dims = textureDimensions(diffuse_texture);
    return wrap_or_clamp(coords, dims, params.seamless);
}

fn luma_at(coords: vec2<i32>) -> f32 {
    let rgb = textureLoad(diffuse_texture, sample_coord(coords), 0).rgb;
    return luma709(rgb);
}

// 5×5 luminance variance, scaled to ~[0,1].
fn local_contrast_5x5(center: vec2<i32>) -> f32 {
    var sum = 0.0;
    let n = 25.0;
    for (var dy = -2; dy <= 2; dy++) {
        for (var dx = -2; dx <= 2; dx++) {
            sum += luma_at(center + vec2<i32>(dx, dy));
        }
    }
    let mean = sum / n;
    var acc = 0.0;
    for (var dy = -2; dy <= 2; dy++) {
        for (var dx = -2; dx <= 2; dx++) {
            let luma = luma_at(center + vec2<i32>(dx, dy));
            acc += (luma - mean) * (luma - mean);
        }
    }
    return clamp((acc / n) * 8.0, 0.0, 1.0);
}

// Decoded normal slope (nx, ny) at a texel.
fn slope_at(coords: vec2<i32>) -> vec2<f32> {
    let n = textureLoad(normal_texture, sample_coord(coords), 0);
    return vec2<f32>(n.r, n.g) * 2.0 - 1.0;
}

// Variance of the slope field over 5×5 — the microfacet dispersion proxy.
fn slope_variance_5x5(center: vec2<i32>) -> f32 {
    var sum = vec2<f32>(0.0);
    var sum_sq = 0.0;
    let n = 25.0;
    for (var dy = -2; dy <= 2; dy++) {
        for (var dx = -2; dx <= 2; dx++) {
            let s = slope_at(center + vec2<i32>(dx, dy));
            sum += s;
            sum_sq += dot(s, s);
        }
    }
    let mean = sum / n;
    let var_total = (sum_sq / n) - dot(mean, mean);
    return max(var_total, 0.0);
}

@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(diffuse_texture);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let metallic = textureLoad(metallic_texture, coords, 0).r;
    let lc = local_contrast_5x5(coords);

    var smoothness = clamp(
        params.smoothness_base
            + params.smoothness_metallic_boost * metallic
            - params.smoothness_roughness_factor * lc,
        0.0,
        1.0,
    );

    if (params.roughness_slope_mix > 0.0) {
        let slope_std = sqrt(slope_variance_5x5(coords));
        let rough_proxy = clamp(params.roughness_slope_scale * slope_std, 0.0, 1.0);
        smoothness = clamp(smoothness - params.roughness_slope_mix * rough_proxy, 0.0, 1.0);
    }

    textureStore(output_texture, coords, vec4<f32>(smoothness, smoothness, smoothness, 1.0));
}
