// Curvature (F2): multi-scale Laplacian of Gaussian. Upstream separable
// `blur.wgsl` passes compute the Gaussian-blurred height fields at σ = 1, 2, 4;
// this pass takes the 4-neighbour Laplacian at each active level and combines
// them with max(|·|) preserving the sign of the strongest response.
// Output 0.5 = flat, <0.5 convex, >0.5 concave.

@group(0) @binding(0)
var blur1_texture: texture_2d<f32>;

@group(0) @binding(1)
var blur2_texture: texture_2d<f32>;

@group(0) @binding(2)
var blur3_texture: texture_2d<f32>;

@group(0) @binding(3)
var output_texture: texture_storage_2d<rgba8unorm, write>;

@group(1) @binding(0)
var<uniform> params: Params;

fn sample_blurred(tex: texture_2d<f32>, coords: vec2<i32>, dims: vec2<u32>) -> f32 {
    let c = wrap_or_clamp(coords, dims, params.seamless);
    return textureLoad(tex, c, 0).r;
}

fn laplacian_at(tex: texture_2d<f32>, coords: vec2<i32>, dims: vec2<u32>) -> f32 {
    let h = textureLoad(tex, coords, 0).r;
    let hl = sample_blurred(tex, coords + vec2<i32>(-1, 0), dims);
    let hr = sample_blurred(tex, coords + vec2<i32>(1, 0), dims);
    let ht = sample_blurred(tex, coords + vec2<i32>(0, -1), dims);
    let hb = sample_blurred(tex, coords + vec2<i32>(0, 1), dims);
    return (hl + hr + ht + hb) - 4.0 * h;
}

@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(blur1_texture);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let levels = clamp(i32(params.curvature_levels), 1, 3);

    // Strongest signed response across scales.
    var best = laplacian_at(blur1_texture, coords, dims);
    if (levels > 1) {
        let l2 = laplacian_at(blur2_texture, coords, dims);
        if (abs(l2) > abs(best)) {
            best = l2;
        }
    }
    if (levels > 2) {
        let l3 = laplacian_at(blur3_texture, coords, dims);
        if (abs(l3) > abs(best)) {
            best = l3;
        }
    }

    let curvature = clamp(best * params.curvature_gain + 0.5, 0.0, 1.0);

    textureStore(output_texture, coords, vec4<f32>(curvature, curvature, curvature, 1.0));
}
