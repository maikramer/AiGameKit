// Guided filter application (F1) — He, Sun, Tang, ECCV 2010 / TPAMI 2013.
// Self-guidance on the height I: q = meanI + varI/(varI+ε) · (I − meanI).
// varI = meanII − meanI² over a box window of radius `guided_radius`
// (computed upstream by two `blur.wgsl` passes on I and I²).

@group(0) @binding(0)
var h_tex: texture_2d<f32>;

@group(0) @binding(1)
var mean_i_tex: texture_2d<f32>;

@group(0) @binding(2)
var mean_ii_tex: texture_2d<f32>;

@group(0) @binding(3)
var base_tex: texture_storage_2d<r32float, write>;

@group(0) @binding(4)
var detail_tex: texture_storage_2d<r32float, write>;

@group(1) @binding(0)
var<uniform> params: Params;

@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(h_tex);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let h = textureLoad(h_tex, coords, 0).r;
    let mean_i = textureLoad(mean_i_tex, coords, 0).r;
    let mean_ii = textureLoad(mean_ii_tex, coords, 0).r;

    let var_i = max(mean_ii - mean_i * mean_i, 0.0);
    let eps = max(params.guided_eps, 1e-6);
    let a = var_i / (var_i + eps);

    let base = mean_i + a * (h - mean_i);
    let detail = h - base;

    textureStore(base_tex, coords, vec4<f32>(base, 0.0, 0.0, 1.0));
    textureStore(detail_tex, coords, vec4<f32>(detail, 0.0, 0.0, 1.0));
}
