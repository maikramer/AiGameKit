# Changelog

## 1.1.0 (2026-10-26)

A smaller public API in `include/sprongmotion.h` (it replaces `include/sm.h`): plain structs with fields, verb and noun
names, load and unload pairs, an options struct for full control and one error string per thread. Output is
bit-identical to 1.0.1 for the same model file, prompt and settings on every exact build.

- A failed call returns an empty value (`sm_is_model_valid`, `sm_is_motion_valid`, `sm_is_text_valid`) and
  `sm_get_error()` says why. A call given an empty value fails too and keeps the first error.
- A model now keeps its own contexts: any number of threads may generate with one model at once.
- Error messages name the option fields (`text_guidance = nan is out of range 0 .. 20`, `path[0].time = ...`).
- `make lib` rebuilds the static library from scratch, so renamed sources leave no stale objects in it.

Old to new:

| 1.0 | 1.1 |
|---|---|
| `#include "sm.h"` | `#include "sprongmotion.h"` |
| `sm_version()`, `SM_VERSION` (a number) | `sm_get_version()`, `SM_VERSION` (`"1.1.0"`), `SM_VERSION_MAJOR` etc. |
| `sm_build_info()` | `sm_get_build_info()` |
| `sm_status_message(s)`, `sm_ctx_last_error(c)`, `sm_request_last_error(r)`, `err` buffers | `sm_get_error()` |
| `sm_status_name`, `sm_status`, `SM_OK`, `SM_DONE`, `SM_E_*` | removed: calls return values, empty on failure |
| `sm_model_load_file(path, hooks, &m, err, n)` | `sm_model m = sm_load_model(path)` |
| `sm_model_load_memory(bytes, size, flags, hooks, &m, err, n)` | `sm_load_model_from_memory(data, size)` (copies the bytes) |
| `SM_LOAD_BORROW`, `SM_LOAD_NOCHECK` | removed |
| `sm_model_free(m)` | `sm_unload_model(model)` |
| `sm_model_info(m, SM_I_FPS)`, `SM_I_FRAMES_PER_TOKEN`, `SM_I_MAX_FRAMES` | `model.fps`, `model.frames_per_token`, `model.max_seconds * model.fps` |
| `SM_I_JOINTS`, `SM_I_FEATURE_DIM`, `SM_I_CODE_LAYERS`, `SM_I_TEXT_DIM` | `model.joint_count`, `model.feature_size`, `model.code_layers`, `model.text_vector_size` |
| `SM_I_ENCODER`, `SM_I_PATH_INPUT` | `model.has_encoder`, `model.follows_path` (and `model.has_generator`) |
| `SM_I_PARAMS_K` | removed: `sm_get_model_meta(model, "general.parameter_count")` |
| `sm_model_meta(m, key)` | `sm_get_model_meta(model, key)` |
| `sm_model_id(m)` | `model.id` |
| `sm_model_vocab(m, i)` | `sm_get_model_word(model, i)` |
| `sm_skeleton_joint_name(m, j)`, `sm_skeleton_parent(m, j)` | `model.joint_names[j]`, `model.joint_parents[j]` |
| `sm_skeleton_offsets(m, out, 66)` | `model.rest_offsets` (66 floats, read only) |
| `sm_hooks.alloc`, `.free` | `sm_set_memory_functions(alloc, free)` before loading (no user pointer) |
| `sm_hooks.n_threads` | `sm_set_thread_count(n)` before loading |
| `sm_hooks.parallel_for`, `.user` | `sm_set_parallel_for(fn, user)` before loading (int indices) |
| `sm_hooks.log`, `SM_HOOKS_INIT`, `SM_DEPRECATED` | removed (the logger was never called) |
| `sm_ctx_create`, `sm_ctx_free`, `sm_ctx_reserve`, `sm_ctx_memory_bytes` | removed: the model holds the contexts |
| `sm_request_create`, `_free`, `_clone`, `_reset` | `sm_options o = sm_default_options(model)`, a plain value |
| `sm_request_set_int` / `_set_float` / `_get_*` with `SM_P_SECONDS`, `_SEED`, `_STEPS` | `o.seconds`, `o.seed`, `o.steps` |
| `SM_P_RES_LAYERS`, `SM_P_CFG`, `SM_P_RES_CFG`, `SM_P_TEMP`, `SM_P_TOP_K` | `o.residual_layers`, `o.text_guidance`, `o.residual_guidance`, `o.temperature`, `o.top_k` |
| `SM_P_FOOT_LOCK`, `SM_P_IN_PLACE`, `SM_P_LOOP`, `SM_P_PATH_WARP` | `o.foot_lock`, `o.in_place`, `o.loop`, `o.path_warp` (bool) |
| `sm_param_next`, `_from_name`, `_name`, `_bounds`, `sm_request_set_by_name` | removed: the fields, the ranges in the header, `model.max_seconds` |
| `sm_api_schema_json`, `sm_model_schema_json` | removed |
| `sm_request_set_text(r, text, len)` | the `prompt` argument |
| `sm_request_set_text_encoded(r, t)` | `o.text = &text` |
| `sm_request_set_prefix(r, prev)` | `o.previous = &prev` |
| `sm_request_set_codes(r, codes, n)` | `o.pinned_codes = codes`, `o.pinned_token_count = n` |
| `sm_request_set_path(r, points, n)` | `o.path = points` (`sm_path_point`: time, x, z, heading, the same layout), `o.path_count = n` |
| `sm_ctx_set_progress(c, fn, user)`, `sm_ctx_cancel`, `sm_ctx_clear_cancel` | `o.progress` (returns `true` to go on, `false` cancels), `o.user` |
| `sm_ctx_generate(c, r, outs, n)` | `sm_generate_motion_ex(model, prompt, o)`, once per seed for variants (seed + i) |
| `sm_ctx_begin`, `_step`, `_preview`, `_finish`, `_abort` | removed: `o.progress` reports each pass |
| `sm_generate_simple(m, text, seconds, seed, out, err, n)` | `sm_generate_motion(model, prompt, seconds, seed)` |
| `sm_ctx_generate_blocks(c, r, n, texts, encs, seconds, seeds, keep, first_frames, out)` | `sm_generate_timeline(model, blocks, n, o)`, `sm_block` {prompt, text, seconds, seed, kept_codes}; `motion.block_start_frames` |
| `sm_ctx_decode_codes(c, r, codes, n, out)` | `sm_decode_codes(model, codes, n, o)` |
| `sm_ctx_encode_motion(c, r, joints, n, out)` | `sm_encode_motion(model, joints, n, o)` |
| `sm_text_create` and `sm_ctx_encode_text(c, text, len, t)` | `sm_text t = sm_encode_text(model, prompt)` |
| `sm_text_free(t)` | `sm_unload_text(t)` |
| `sm_text_vector(t, &v, &dim)` | `t.vector`, `t.vector_size` |
| `sm_text_set_vector(t, v, dim)` | `sm_text_from_vector(model, v, dim)` |
| `sm_text_unknown_words(t, buf, cap, &need, &n)` | `t.unknown_words` (one per line), `t.unknown_word_count` |
| `sm_motion_create`, `sm_motion_free` | returned by the calls, `sm_unload_motion(motion)` |
| `sm_motion_reserve`, `sm_motion_copy` | removed |
| `sm_motion_frames`, `_seconds`, `_seed` | `motion.frame_count`, `motion.frame_count / motion.fps`, `motion.seed` |
| `sm_motion_joints`, `_rotations`, `_offsets`, `_root`, `_speed` | `motion.joints`, `.rotations`, `.offsets`, `.root`, `.speed` |
| `sm_motion_path_error`, `_contacts`, `_features` | `motion.path_error`, `.contacts`, `.features` |
| `sm_motion_codes(mo, &n)`, `sm_motion_confidence` | `motion.codes` (`motion.token_count * 6`), `motion.confidence` |
| `sm_motion_write_bvh(mo, scale, buf, cap, &need)` | `sm_export_motion_bvh_text(motion, scale)` and `sm_free`, or `sm_export_motion_bvh(motion, file_name)` |

## 1.0.1 (2026-10-21)

Faster, with the same output: codes, features and joints stay bit-identical to 1.0.0 on every exact build at any
thread count.

- The matrix weights are dequantised once at load into f32 panels instead of on every pass: about 6 % less time per
  timeline block and 2 to 3 % per 10 s clip, native and WebAssembly. A model takes 57 MB more memory for it.
- `docs/design.md` lists the faster options that would change the output, with their measured gains.

## 1.0.0 (2026-10-21)

First release. Loads SprongMotion-14M 1.0 (`SprongMotion-14M-v1.0-Q8_0.gguf`, model file format 2).

- C11 library with no dependencies: static and shared builds for macOS, Linux and Windows, and standalone WebAssembly
  (SIMD128 and plain).
- Text to motion: `sm_generate_simple`, or contexts and requests with every sampling parameter, variants, and the step
  API with progress, previews and cancel.
- Editing: pinned codes, in-painting a range, loops, timelines of prompt blocks (`sm_ctx_generate_blocks`), joints into
  codes with the motion encoder (`sm_ctx_encode_motion`).
- Paths: `sm_request_set_path` warps the root onto a curve, with planted feet keeping their yaw. `sm_motion_speed`.
- Outputs: joints, rotations, offsets, root, contacts, features, codes, confidence, BVH. Foot lock and in place.
- Same model file and same request give bit-identical output on every exact build (native, wasm SIMD, wasm plain) at
  any thread count. `FAST=1` trades that for fused multiply-add speed.
- `make dist` and `make dist-wasm` package a release per platform.
