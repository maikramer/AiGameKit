/* Text to a BVH file. Usage: 01_hello model.gguf "a person waves" out.bvh */
#include <stdio.h>
#include "sprongmotion.h"

int main(int argc, char **argv) {
    if (argc != 4) {
        fprintf(stderr, "usage: %s model.gguf \"a person waves\" out.bvh\n", argv[0]);
        return 2;
    }
    sm_model model = sm_load_model(argv[1]);
    sm_motion motion = sm_generate_motion(model, argv[2], 4.0f, 1);
    const bool ok = sm_export_motion_bvh(motion, argv[3]);
    if (ok) printf("%s: %d frames at %d fps\n", argv[3], motion.frame_count, motion.fps);
    else fprintf(stderr, "%s\n", sm_get_error());
    sm_unload_motion(motion);
    sm_unload_model(model);
    return !ok;
}
