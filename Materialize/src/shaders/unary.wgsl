// Single-input micro-passes of the height chain (F1): element-wise ops with
// one sampled texture (binding 0), one r32float storage output (binding 1)
// and the FilterParams uniform at group(1).

@group(0) @binding(0)
var src_tex: texture_2d<f32>;

@group(0) @binding(1)
var dst_tex: texture_storage_2d<r32float, write>;

@group(1) @binding(0)
var<uniform> fparams: FilterParams;

// Rec.709 luminance of the rgba8 diffuse → r32float.
@compute @workgroup_size(8, 8, 1)
fn main_luma(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(src_tex);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let c = textureLoad(src_tex, coords, 0).rgb;
    textureStore(dst_tex, coords, vec4<f32>(luma709(c), 0.0, 0.0, 1.0));
}

// out = weight · src  (pyramid accumulator seed)
@compute @workgroup_size(8, 8, 1)
fn main_scale(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(src_tex);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let v = textureLoad(src_tex, coords, 0).r * fparams.weight;
    textureStore(dst_tex, coords, vec4<f32>(v, 0.0, 0.0, 1.0));
}

// out = src²  (guided filter needs E[I²])
@compute @workgroup_size(8, 8, 1)
fn main_square(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(src_tex);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let v = textureLoad(src_tex, coords, 0).r;
    textureStore(dst_tex, coords, vec4<f32>(v * v, 0.0, 0.0, 1.0));
}

// out = src  (ping-pong copy)
@compute @workgroup_size(8, 8, 1)
fn main_copy(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(src_tex);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let v = textureLoad(src_tex, coords, 0).r;
    textureStore(dst_tex, coords, vec4<f32>(v, 0.0, 0.0, 1.0));
}
