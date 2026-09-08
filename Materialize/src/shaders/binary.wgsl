// Two-input micro-passes (F1/F6): blend/split with two sampled textures
// (bindings 0, 1) and one r32float storage output (binding 2).

@group(0) @binding(0)
var src_tex: texture_2d<f32>;

@group(0) @binding(1)
var src2_tex: texture_2d<f32>;

@group(0) @binding(2)
var dst_tex: texture_storage_2d<r32float, write>;

@group(1) @binding(0)
var<uniform> fparams: FilterParams;

// out = src0 + weight · src1  (pyramid accumulation)
@compute @workgroup_size(8, 8, 1)
fn main_blend_add(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(src_tex);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let a = textureLoad(src_tex, coords, 0).r;
    let b = textureLoad(src2_tex, coords, 0).r;
    textureStore(dst_tex, coords, vec4<f32>(a + fparams.weight * b, 0.0, 0.0, 1.0));
}

// out = src0 − src1  (high-frequency extraction, F6)
@compute @workgroup_size(8, 8, 1)
fn main_sub(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(src_tex);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let a = textureLoad(src_tex, coords, 0).r;
    let b = textureLoad(src2_tex, coords, 0).r;
    textureStore(dst_tex, coords, vec4<f32>(a - b, 0.0, 0.0, 1.0));
}
