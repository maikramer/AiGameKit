/* Joint positions to motion features, as the training data's process_file (Guo et al. 2022). */
#include "sm_internal.h"

static const double rest_direction[SM_J][3] = {
    {0, 0, 0}, {1, 0, 0}, {-1, 0, 0}, {0, 1, 0}, {0, -1, 0}, {0, -1, 0}, {0, 1, 0}, {0, -1, 0}, {0, -1, 0}, {0, 1, 0},
    {0, 0, 1}, {0, 0, 1}, {0, 1, 0}, {1, 0, 0}, {-1, 0, 0}, {0, 0, 1}, {0, -1, 0}, {0, -1, 0}, {0, -1, 0},
    {0, -1, 0}, {0, -1, 0}, {0, -1, 0},
};
static const double target_len[SM_J] = {   /* bone lengths of the target skeleton, metres */
    0, 0.10307398438453674, 0.1098833754658699, 0.13156822323799133, 0.3936232030391693, 0.39018821716308594,
    0.1431901901960373, 0.43243294954299927, 0.4256434142589569, 0.0573648139834404, 0.14338180422782898,
    0.14941906929016113, 0.21936000883579254, 0.1374867856502533, 0.14338283240795135, 0.1030391976237297,
    0.13161399960517883, 0.12298436462879181, 0.2568398416042328, 0.2630917429924011, 0.2660118341445923,
    0.2698763310909271,
};
static const int8_t chains[5][7] = {
    {0, 2, 5, 8, 11, -1}, {0, 1, 4, 7, 10, -1}, {0, 3, 6, 9, 12, 15, -1}, {9, 14, 17, 19, 21, -1},
    {9, 13, 16, 18, 20, -1},
};
static const int feet[4] = {SM_L_ANKLE, SM_L_FOOT, SM_R_ANKLE, SM_R_FOOT};

enum { SIGMA = 20, RADIUS = 80 };   /* the facing smoothing: scipy gaussian_filter1d, radius int(4 sigma + 0.5) */
#define CONTACT_STEP2 0.002          /* m^2: a foot moving less than this per frame is on the ground */
#define FRAME (SM_J * 3)

static void sub(const double *a, const double *b, double *out) { for (int k = 0; k < 3; k++) out[k] = a[k] - b[k]; }
static double len3(const double *v) { return sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]); }
static void unit3(double *v) { const double n = len3(v); for (int k = 0; k < 3; k++) v[k] /= n; }
static sm_quat qconj(sm_quat q) { return (sm_quat){q.w, -q.x, -q.y, -q.z}; }

/* asin x = 2 atan(x / (1 + sqrt(1 - x^2))), atan halved twice, then its series to z^25: + - * / and sqrt only */
static double exact_asin(double x) {
    x = x > 1 ? 1 : x < -1 ? -1 : x;
    double z = x / (1 + sqrt(1 - x * x));
    z = z / (1 + sqrt(1 + z * z));
    z = z / (1 + sqrt(1 + z * z));
    double series = 0;
    for (int k = 12; k >= 0; k--) series = 1.0 / (2 * k + 1) - z * z * series;
    return 8 * z * series;
}

/* (az, 0, -ax): Y x unit(a), a = the hip line plus the shoulder line */
static void facing(const double *p, int sign, double *out) {
    double a[3];
    for (int k = 0; k < 3; k++)
        a[k] = sign * (p[3 * SM_L_HIP + k] - p[3 * SM_R_HIP + k]) +
               (p[3 * SM_R_SHOULDER + k] - p[3 * SM_L_SHOULDER + k]);
    unit3(a);
    out[0] = a[2];
    out[1] = 0;
    out[2] = -a[0];
}

/* the right knee and ankle bones of the first frame, rounded as the reference does */
static double right_leg_length(const float *joints) {
    double total = 0;
    for (int i = 0; i < 2; i++) {
        const int j = i ? SM_R_ANKLE : SM_R_KNEE;
        double bone[3];
        for (int k = 0; k < 3; k++) bone[k] = (double)joints[3 * j + k] - joints[3 * sm_parent[j] + k];
        total += (float)len3(bone);
    }
    return total;
}

/* the bones' directions with the target lengths, the root scaled by leg length (IK then FK onto the target skeleton) */
static void retarget(const float *joints, uint32_t T, double *positions) {
    const double scale = (target_len[SM_R_KNEE] + target_len[SM_R_ANKLE]) / right_leg_length(joints);
    for (size_t t = 0; t < T; t++) {
        const float *j = joints + t * FRAME;
        double *p = positions + t * FRAME;
        for (int k = 0; k < 3; k++) p[k] = j[k] * scale;
        for (int b = 1; b < SM_J; b++) {
            const int a = sm_parent[b];
            double dir[3];
            for (int k = 0; k < 3; k++) dir[k] = (double)j[3 * b + k] - j[3 * a + k];
            unit3(dir);
            for (int k = 0; k < 3; k++) p[3 * b + k] = p[3 * a + k] + dir[k] * target_len[b];
        }
    }
}

/* on the floor, root at x = z = 0 and facing +Z in the first frame */
static void canonical(double *positions, uint32_t T) {
    const size_t n = (size_t)T * SM_J;
    double floor_y = INFINITY;
    for (size_t i = 0; i < n; i++) floor_y = positions[3 * i + 1] < floor_y ? positions[3 * i + 1] : floor_y;

    const double x0 = positions[0], z0 = positions[2];
    for (size_t i = 0; i < n; i++) {
        positions[3 * i] -= x0;
        positions[3 * i + 1] -= floor_y;
        positions[3 * i + 2] -= z0;
    }

    double face[3], turned[3];
    const double forward[3] = {0, 0, 1};
    facing(positions, -1, face);
    unit3(face);
    const sm_quat q = sm_qbetween(face, forward);
    for (size_t i = 0; i < n; i++) {
        sm_qrot(q, positions + 3 * i, turned);
        memcpy(positions + 3 * i, turned, sizeof turned);
    }
}

/* the root's yaw per frame from the smoothed facing (edges repeated); frame 0 keeps no turn, as the reference */
static void root_yaw(const double *positions, uint32_t T, double *facings, sm_quat *yaw) {
    double weight[RADIUS + 1], sum = 0;
    for (int k = 0; k <= RADIUS; k++) {
        weight[k] = sm_exp(-0.5 / (SIGMA * SIGMA) * k * k);
        sum += k ? 2 * weight[k] : weight[k];
    }
    for (int k = 0; k <= RADIUS; k++) weight[k] /= sum;
    for (size_t t = 0; t < T; t++) facing(positions + t * FRAME, 1, facings + 3 * t);

    for (int64_t t = 0; t < T; t++) {
        double smooth[3];
        const double forward[3] = {0, 0, 1};
        for (int k = 0; k < 3; k++) smooth[k] = facings[3 * t + k] * weight[0];
        for (int64_t d = RADIUS; d >= 1; d--) {
            const int64_t a = t - d < 0 ? 0 : t - d, b = t + d >= T ? T - 1 : t + d;
            for (int k = 0; k < 3; k++) smooth[k] += (facings[3 * a + k] + facings[3 * b + k]) * weight[d];
        }
        unit3(smooth);
        yaw[t] = t ? sm_qbetween(smooth, forward) : (sm_quat){1, 0, 0, 0};
    }
}

/* the first two columns of the rotation matrix (Zhou et al. 2019) */
static void cont6d(sm_quat q, float *out) {
    const double s = 2 / (q.w * q.w + q.x * q.x + q.y * q.y + q.z * q.z);
    const double m[6] = {1 - s * (q.y * q.y + q.z * q.z), s * (q.x * q.y + q.z * q.w), s * (q.x * q.z - q.y * q.w),
                         s * (q.x * q.y - q.z * q.w), 1 - s * (q.x * q.x + q.z * q.z), s * (q.y * q.z + q.x * q.w)};
    for (int i = 0; i < 6; i++) out[i] = (float)m[i];
}

/* the rotations of each chain's bones relative to the rotation before (the chain's start: the root's yaw) */
static void bone_rotations(const double *p, sm_quat root, float *rot6d) {
    for (int c = 0; c < 5; c++) {
        sm_quat rot = root;
        for (int i = 1; chains[c][i] >= 0; i++) {
            const int a = chains[c][i - 1], b = chains[c][i];
            double dir[3];
            sub(p + 3 * b, p + 3 * a, dir);
            unit3(dir);
            const sm_quat local = sm_qmul(qconj(rot), sm_qbetween(rest_direction[b], dir));
            cont6d(local, rot6d + 6 * (b - 1));
            rot = sm_qmul(rot, local);
        }
    }
}

/* the features of the step from frame p to frame next */
static void frame_features(const double *p, const double *next, sm_quat yaw, sm_quat next_yaw, float *f) {
    double step[3], local[3];
    sub(next, p, step);
    sm_qrot(next_yaw, step, local);
    const sm_quat yaw_velocity = sm_qmul(next_yaw, qconj(yaw));
    f[0] = (float)exact_asin(yaw_velocity.y);
    f[1] = (float)local[0];
    f[2] = (float)local[2];
    f[3] = (float)p[1];

    for (int j = 1; j < SM_J; j++) {
        const double from_root[3] = {p[3 * j] - p[0], p[3 * j + 1], p[3 * j + 2] - p[2]};
        sm_qrot(yaw, from_root, local);
        for (int k = 0; k < 3; k++) f[SM_F_POS + 3 * (j - 1) + k] = (float)local[k];
    }
    bone_rotations(p, yaw, f + SM_F_ROT);
    for (int j = 0; j < SM_J; j++) {
        sub(next + 3 * j, p + 3 * j, step);
        sm_qrot(yaw, step, local);
        for (int k = 0; k < 3; k++) f[SM_F_VEL + 3 * j + k] = (float)local[k];
    }
    for (int i = 0; i < 4; i++) {
        sub(next + 3 * feet[i], p + 3 * feet[i], step);
        f[SM_F_CONTACT + i] = step[0] * step[0] + step[1] * step[1] + step[2] * step[2] < CONTACT_STEP2 ? 1.0f : 0.0f;
    }
}

size_t sm_feat_tmp_doubles(uint32_t T) { return (size_t)T * (FRAME + 4 + 3); }

void sm_joints_to_features(const float *joints, uint32_t T, double *tmp, float *feat) {
    double *positions = tmp, *facings = positions + (size_t)T * FRAME;
    sm_quat *yaw = (sm_quat *)(void *)(facings + 3 * (size_t)T);
    retarget(joints, T, positions);
    canonical(positions, T);
    root_yaw(positions, T, facings, yaw);
    for (size_t t = 0; t + 1 < T; t++) {
        const double *p = positions + t * FRAME;
        frame_features(p, p + FRAME, yaw[t], yaw[t + 1], feat + t * SM_F);
    }
}
