// Curvature (F2): multi-scale Laplacian of Gaussian. Per level l ∈ [0, L):
// blur the height with σ = 2^l (inline separable), take the 4-neighbour
// Laplacian, combine levels with max(|·|) preserving the sign of the
// strongest response. Output 0.5 = flat, <0.5 convex, >0.5 concave.

@group(0) @binding(0)
var height_texture: texture_2d<f32>;

@group(0) @binding(1)
var output_texture: texture_storage_2d<rgba8unorm, write>;

@group(1) @binding(0)
var<uniform> params: Params;

fn sample_height(coords: vec2<i32>) -> f32 {
    let dims = textureDimensions(height_texture);
    let c = wrap_or_clamp(coords, dims, params.seamless);
    return textureLoad(height_texture, c, 0).r;
}

// Inline separable gaussian (σ ≥ 1 keeps the loop tight).
fn blurred(coords: vec2<i32>, sigma: f32) -> f32 {
    if (sigma < 0.05) {
        return sample_height(coords);
    }
    let r = i32(ceil(sigma * 3.0));

    var h_acc = 0.0;
    var h_wsum = 0.0;
    for (var d = -r; d <= r; d++) {
        let w = exp(-f32(d * d) / (2.0 * sigma * sigma));
        h_acc += w * sample_height(coords + vec2<i32>(d, 0));
        h_wsum += w;
    }

    var v_acc = 0.0;
    var v_wsum = 0.0;
    for (var d = -r; d <= r; d++) {
        var row_acc = 0.0;
        var row_wsum = 0.0;
        for (var e = -r; e <= r; e++) {
            let w = exp(-f32(e * e) / (2.0 * sigma * sigma));
            row_acc += w * sample_height(coords + vec2<i32>(e, d));
            row_wsum += w;
        }
        let w = exp(-f32(d * d) / (2.0 * sigma * sigma));
        v_acc += w * (row_acc / row_wsum);
        v_wsum += w;
    }

    return v_acc / v_wsum;
}

fn laplacian_at(coords: vec2<i32>, sigma: f32) -> f32 {
    let h = blurred(coords, sigma);
    let hl = blurred(coords + vec2<i32>(-1, 0), sigma);
    let hr = blurred(coords + vec2<i32>(1, 0), sigma);
    let ht = blurred(coords + vec2<i32>(0, -1), sigma);
    let hb = blurred(coords + vec2<i32>(0, 1), sigma);
    return (hl + hr + ht + hb) - 4.0 * h;
}

@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(height_texture);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let levels = clamp(i32(params.curvature_levels), 1, 3);

    // Strongest signed response across scales.
    var best = laplacian_at(coords, 1.0);
    if (levels > 1) {
        let l2 = laplacian_at(coords, 2.0);
        if (abs(l2) > abs(best)) {
            best = l2;
        }
    }
    if (levels > 2) {
        let l3 = laplacian_at(coords, 4.0);
        if (abs(l3) > abs(best)) {
            best = l3;
        }
    }

    let curvature = clamp(best * params.curvature_gain + 0.5, 0.0, 1.0);

    textureStore(output_texture, coords, vec4<f32>(curvature, curvature, curvature, 1.0));
}
