// SprongMotion: text to 3D human motion. C11, no dependencies.
//
//     sm_model model = sm_load_model("SprongMotion-14M-v1.0-Q8_0.gguf");
//     sm_motion motion = sm_generate_motion(model, "a person waves with the left hand", 3.0f, 7);
//     if (!sm_is_motion_valid(motion)) puts(sm_get_error());
//     sm_export_motion_bvh(motion, "wave.bvh");
//     sm_unload_motion(motion);
//     sm_unload_model(model);
//
// Units: metres, seconds, radians. Y up, right-handed, a clip starts at the origin facing +Z. Strings are UTF-8.
// A failed call returns an empty value (sm_is_*_valid says false) and sm_get_error() says why. A call given an empty
// value fails too and keeps the error that made it, so a chain of calls can be checked once at the end.
// A model is shared by any number of threads, and each call may run on its own thread.
// The same model file (model.id), prompt and options give bit-identical motion on every build (native, wasm SIMD,
// wasm plain) at any thread count. A native FAST=1 build (fused multiply-add) only comes close: its build info says
// fma. Within version 1 functions are only added and structs only grow at the end.
#ifndef SPRONGMOTION_H
#define SPRONGMOTION_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32) && defined(SM_SHARED)
#  ifdef SM_BUILD
#    define SM_API __declspec(dllexport)
#  else
#    define SM_API __declspec(dllimport)
#  endif
#elif defined(SM_SHARED)
#  define SM_API __attribute__((visibility("default")))
#else
#  define SM_API
#endif

#define SM_VERSION_MAJOR 1
#define SM_VERSION_MINOR 1
#define SM_VERSION_PATCH 0
#define SM_VERSION "1.1.0"

// Model

typedef struct sm_model {
    const char *id;                  // "SprongMotion-14M@1a2b3c4d": name and file hash, store it with codes
    int fps;                         // 20
    int frames_per_token;            // 2
    float max_seconds;               // the longest clip
    int code_layers;                 // codes per token (6)
    int feature_size;                // floats per feature frame (263)
    int text_vector_size;            // floats in an encoded prompt (384)
    int joint_count;                 // 22
    const char *const *joint_names;  // "pelvis", "l_hip", ...
    const int *joint_parents;        // -1 for the root
    const float *rest_offsets;       // joint_count * 3: each joint from its parent on the reference skeleton, metres
    bool has_generator;              // false: a file with only the decoder
    bool has_encoder;                // sm_encode_motion works
    bool follows_path;               // the network itself reads a path; false: the root warp alone, codes ignore paths
    void *internal;
} sm_model;

SM_API sm_model sm_load_model(const char *file_name);                          // memory-mapped, or read if that fails
SM_API sm_model sm_load_model_from_memory(const void *data, size_t size);      // copies the bytes
SM_API bool sm_is_model_valid(sm_model model);
SM_API void sm_unload_model(sm_model model);                                   // after every text and motion of it
SM_API const char *sm_get_model_meta(sm_model model, const char *key);         // GGUF string metadata, NULL if absent
SM_API const char *sm_get_model_word(sm_model model, int index);               // known words, sorted, NULL past end

// Options

typedef struct sm_path_point {
    float time;                      // when the root passes the point, rising; NaN = free, at the motion's own speed
    float x, z;                      // metres, in the output's frame (a previous motion's with options.previous)
    float heading;                   // NaN, or a facing for models that follow paths: 0 = +Z, pi/2 = +X
} sm_path_point;

typedef struct sm_text sm_text;
typedef struct sm_motion sm_motion;

typedef struct sm_options {
    float seconds;                   // clip length, 0.5 .. max_seconds (default 4)
    int seed;                        // 0 .. 2^31 - 1: the same seed gives the same motion (default 0)
    int steps;                       // base-layer unmasking steps, 1 .. 64 (default 10)
    int residual_layers;             // 0 .. 5, 0 = base codes only, a fast draft (default 5)
    float text_guidance;             // of the base layer, 0 .. 20 (default 3)
    float residual_guidance;         // of the residual layers, 0 .. 20 (default 3)
    float temperature;               // 0.01 .. 10 (default 1)
    int top_k;                       // sample from the k likeliest codes, 0 = all, 0 .. 512 (default 52)
    bool foot_lock;                  // planted feet stay put and above the floor (default true)
    bool in_place;                   // joints and BVH without the horizontal travel; root keeps it (default false)
    bool loop;                       // a seamless cycle: the end leads into the start (default false)
    bool path_warp;                  // with a path, the root lands on it; false: only path_error (default true)
    const sm_path_point *path;       // 2 .. 1024 points on a Catmull-Rom curve; the first again closes it
    int path_count;                  // 0: no path
    const sm_motion *previous;       // continue this motion from its last 0.3 s (not with loop)
    const int16_t *pinned_codes;     // token_count * code_layers, as motion.codes: base code -1 = make, else kept
    int pinned_token_count;          // up to 512, at most the clip's tokens
    const sm_text *text;             // an encoded prompt to use instead of the prompt string
    bool (*progress)(void *user, int done, int total);   // called after each pass; false cancels
    void *user;
} sm_options;

SM_API sm_options sm_default_options(sm_model model);   // the model file's defaults

// Generate

typedef struct sm_block {
    const char *prompt;              // or text
    const sm_text *text;
    float seconds;                   // the seams stay within the model's maximum
    int seed;
    const int16_t *kept_codes;       // codes from an earlier timeline, kept exactly; NULL: made, its seams pinned
} sm_block;

SM_API sm_motion sm_generate_motion(sm_model model, const char *prompt, float seconds, int seed);
SM_API sm_motion sm_generate_motion_ex(sm_model model, const char *prompt, sm_options options);
// 1 .. 256 blocks back to back; seams to kept or earlier blocks are pinned, then all is decoded at once
SM_API sm_motion sm_generate_timeline(sm_model model, const sm_block *blocks, int block_count, sm_options options);
SM_API sm_motion sm_decode_codes(sm_model model, const int16_t *codes, int token_count, sm_options options);
// joints at 20 fps from anywhere (any start and facing, 3 .. 12001 frames) into codes and back, to continue or edit
SM_API sm_motion sm_encode_motion(sm_model model, const float *joints, int frame_count, sm_options options);

// Text

struct sm_text {
    const float *vector;             // the pooled text vector
    int vector_size;
    int unknown_word_count;          // words the model does not know
    const char *unknown_words;       // those words, one per line ("" if none)
    void *internal;
};

SM_API sm_text sm_encode_text(sm_model model, const char *prompt);                       // one encoding for many seeds
SM_API sm_text sm_text_from_vector(sm_model model, const float *vector, int vector_size);  // your own vector, no words
SM_API bool sm_is_text_valid(sm_text text);
SM_API void sm_unload_text(sm_text text);

// Motion

struct sm_motion {
    int frame_count;
    int fps;
    int seed;                        // -1 when decoded from codes
    float *joints;                   // frame_count * 22 * 3 positions, metres
    float *rotations;                // frame_count * 22 * 4, x y z w: each joint relative to its parent, turning its
                                     // children's rest offsets (as BVH and glTF do)
    float *offsets;                  // 22 * 3: this clip's own skeleton (each bone's median), as in the BVH
    float *root;                     // frame_count * 3, the full travel (also with in_place)
    float *speed;                    // frame_count, m/s: the root smoothed over 0.25 s, before a path warp
    uint8_t *contacts;               // frame_count * 4 on the ground: l_ankle, l_foot, r_ankle, r_foot
    float *features;                 // frame_count * 263
    int16_t *codes;                  // token_count * code_layers: store them with model.id
    float *confidence;               // token_count, 0 .. 1
    int token_count;                 // a token is frames_per_token frames
    float path_error;                // metres the root was from the path before the warp, -1 without a path
    int *block_start_frames;         // block_count + 1 entries: where each block starts, then the end (timelines)
    int block_count;
    void *internal;
};

SM_API bool sm_is_motion_valid(sm_motion motion);
SM_API void sm_unload_motion(sm_motion motion);
SM_API char *sm_export_motion_bvh_text(sm_motion motion, float scale);   // 100 = centimetres; free with sm_free
SM_API bool sm_export_motion_bvh(sm_motion motion, const char *file_name);   // in centimetres

// Library

typedef void (*sm_task_fn)(void *arg, int index);

SM_API const char *sm_get_error(void);        // why the last call on this thread failed, "" if it did not
SM_API const char *sm_get_version(void);      // "1.1.0"
SM_API const char *sm_get_build_info(void);   // "1.1.0 native exact", for bug reports
SM_API void sm_set_thread_count(int count);   // for models loaded later; 0 = all cores (default)
// run task(arg, i) for every i < n on your job system, for models loaded later; NULL = the built-in threads
SM_API void sm_set_parallel_for(void (*parallel_for)(void *user, int n, sm_task_fn task, void *arg), void *user);
// before anything is loaded; NULL, NULL = the defaults
SM_API void sm_set_memory_functions(void *(*alloc)(size_t size, size_t align), void (*free)(void *ptr));
SM_API void sm_free(void *ptr);

#ifdef __cplusplus
}
#endif
#endif
