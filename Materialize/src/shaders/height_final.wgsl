// Final height composition (F1/F6): contrast-pivoted base + detail_mix·detail
// + optional high-frequency shading injection (intrinsic pre-pass, F6).

@group(0) @binding(0)
var base_tex: texture_2d<f32>;

@group(0) @binding(1)
var detail_tex: texture_2d<f32>;

@group(0) @binding(2)
var shading_hf_tex: texture_2d<f32>;

@group(0) @binding(3)
var dst_tex: texture_storage_2d<r32float, write>;

@group(1) @binding(0)
var<uniform> params: Params;

@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(base_tex);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let base = textureLoad(base_tex, coords, 0).r;
    let detail = textureLoad(detail_tex, coords, 0).r;

    let contrasted = (base - 0.5) * params.height_contrast + 0.5;

    var height = contrasted + params.detail_mix * detail;

    // F6: shape-from-shading — fold the high-frequency part of the intrinsic
    // shading into the height (soft shadow boundaries become gentle relief).
    height += params.shading_height_mix * textureLoad(shading_hf_tex, coords, 0).r;

    textureStore(dst_tex, coords, vec4<f32>(clamp(height, 0.0, 1.0), 0.0, 0.0, 1.0));
}
