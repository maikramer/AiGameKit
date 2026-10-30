/* Features to joints, joint rotations, the clip's skeleton, foot contacts and foot lock. */
#include "sm_internal.h"
#include <stdlib.h>

const int8_t sm_parent[SM_J] = {-1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 12, 13, 14, 16, 17, 18, 19};
static const int8_t child[SM_J] = {-1, 4, 5, 6, 7, 8, 9, 10, 11, -1, -1, -1, 15, 16, 17, -1, 18, 19, 20, 21, -1, -1};
sm_quat sm_qmul(sm_quat a, sm_quat b) {
    return (sm_quat){a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z, a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
                     a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x, a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w};
}

static void cross(const double a[3], const double b[3], double out[3]) {
    out[0] = a[1] * b[2] - a[2] * b[1];
    out[1] = a[2] * b[0] - a[0] * b[2];
    out[2] = a[0] * b[1] - a[1] * b[0];
}

static double dot(const double a[3], const double b[3]) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

static double norm2(const double v[3]) { return v[0] * v[0] + v[1] * v[1] + v[2] * v[2]; }

static void unit(double v[3]) {
    const double n = sqrt(norm2(v));
    for (int i = 0; i < 3; i++) v[i] /= n;
}

/* v + 2 (w (u x v) + u x (u x v)), u = q.xyz */
void sm_qrot(sm_quat q, const double v[3], double out[3]) {
    const double u[3] = {q.x, q.y, q.z};
    double uv[3], uuv[3];
    cross(u, v, uv);
    cross(u, uv, uuv);
    for (int i = 0; i < 3; i++) out[i] = v[i] + 2 * (q.w * uv[i] + uuv[i]);
}

static sm_quat qconj(sm_quat q) { return (sm_quat){q.w, -q.x, -q.y, -q.z}; }

sm_quat sm_qbetween(const double u[3], const double v[3]) {
    double c[3], w = 1 + u[0] * v[0] + u[1] * v[1] + u[2] * v[2];
    cross(u, v, c);
    if (w < 1e-9) {   /* opposite: 180 degrees about any axis perpendicular to u */
        const double x[3] = {1, 0, 0}, z[3] = {0, 0, 1};
        cross(u, x, c);
        if (norm2(c) < 1e-12) cross(u, z, c);
        w = 0;
    }
    const double n = sqrt(w * w + c[0] * c[0] + c[1] * c[1] + c[2] * c[2]);
    return (sm_quat){w / n, c[0] / n, c[1] / n, c[2] / n};
}

/* recover_from_ric: yaw and root xz are running sums of the velocities, the joints turned by the inverse yaw */
void sm_features_to_joints(const float *feat, uint32_t T, sm_track *tr, float *joints) {
    double yaw = tr->yaw, x = tr->x, z = tr->z;
    for (uint32_t t = 0; t < T; t++) {
        const float *f = feat + (size_t)t * SM_F, *prev = t ? f - SM_F : tr->moving ? tr->vel : NULL;
        float *frame = joints + (size_t)t * SM_J * 3;
        if (prev) yaw += prev[0];
        const sm_quat inverse_yaw = {cos(yaw), 0, -sin(yaw), 0};
        double turned[3];
        if (prev) {
            const double velocity[3] = {prev[1], 0, prev[2]};
            sm_qrot(inverse_yaw, velocity, turned);
            x += turned[0];
            z += turned[2];
        }
        frame[0] = (float)x;
        frame[1] = f[3];
        frame[2] = (float)z;
        for (int j = 1; j < SM_J; j++) {
            const double local[3] = {f[3 * j + 1], f[3 * j + 2], f[3 * j + 3]};
            sm_qrot(inverse_yaw, local, turned);
            frame[3 * j] = (float)(turned[0] + x);
            frame[3 * j + 1] = (float)turned[1];
            frame[3 * j + 2] = (float)(turned[2] + z);
        }
    }
    if (!T) return;
    *tr = (sm_track){yaw, x, z, {0}, 1};
    memcpy(tr->vel, feat + (size_t)(T - 1) * SM_F, sizeof tr->vel);
}

static void dif(const float *j, int a, int b, double out[3]) {
    for (int k = 0; k < 3; k++) out[k] = (double)j[3 * a + k] - j[3 * b + k];
}

/* turns a0 onto a1 exactly, then about a1 to bring b0 closest to b1 (TRIAD) */
static sm_quat turn(double a0[3], const double b0[3], double a1[3], const double b1[3]) {
    double b[3], p0[3], p1[3];
    unit(a0);
    unit(a1);
    const sm_quat q = sm_qbetween(a0, a1);
    sm_qrot(q, b0, b);
    const double d0 = dot(b, a1), d1 = dot(b1, a1);
    for (int k = 0; k < 3; k++) {
        p0[k] = b[k] - d0 * a1[k];
        p1[k] = b1[k] - d1 * a1[k];
    }
    if (norm2(p0) < 1e-18 || norm2(p1) < 1e-18) return q;
    unit(p0);
    unit(p1);
    return sm_qmul(sm_qbetween(p0, p1), q);
}

/* each joint's rotation turns its rest offsets towards its children; out relative to the parent's, x y z w */
static sm_quat pelvis_rotation(const float *j, const float *rest) {
    double a0[3], b0[3], a1[3], b1[3];
    dif(rest, SM_L_HIP, SM_R_HIP, a0);
    dif(j, SM_L_HIP, SM_R_HIP, a1);
    dif(rest, SM_SPINE1, SM_PELVIS, b0);
    dif(j, SM_SPINE1, SM_PELVIS, b1);
    return turn(a0, b0, a1, b1);
}

/* the shoulder line: the collar line (15 cm) jitters the chest's twist */
static sm_quat chest_rotation(const float *j, const float *rest) {
    double a0[3], b0[3], a1[3], b1[3];
    dif(rest, SM_NECK, SM_PELVIS, a0);
    dif(j, SM_NECK, SM_SPINE3, a1);
    dif(j, SM_L_SHOULDER, SM_R_SHOULDER, b1);
    for (int k = 0; k < 3; k++)
        b0[k] = (double)rest[3 * SM_L_COLLAR + k] + rest[3 * SM_L_SHOULDER + k] - rest[3 * SM_R_COLLAR + k] -
                rest[3 * SM_R_SHOULDER + k];
    return turn(a0, b0, a1, b1);
}

/* the shortest arc from the parent's rotation that aims the joint's rest offset to its child at the child */
static sm_quat bone_rotation(const float *j, const float *rest, int i, sm_quat parent) {
    if (child[i] < 0) return parent;
    const double offset[3] = {rest[3 * child[i]], rest[3 * child[i] + 1], rest[3 * child[i] + 2]};
    double a0[3], a1[3];
    sm_qrot(parent, offset, a0);
    dif(j, child[i], i, a1);
    unit(a0);
    unit(a1);
    return sm_qmul(sm_qbetween(a0, a1), parent);
}

void sm_rotations(const float *joints, uint32_t T, const float *rest, float *xyzw) {
    for (size_t t = 0; t < T; t++) {
        const float *j = joints + t * SM_J * 3;
        sm_quat global[SM_J];
        for (int i = 0; i < SM_J; i++) {
            const sm_quat parent = i ? global[sm_parent[i]] : (sm_quat){1, 0, 0, 0};
            if (i == SM_PELVIS) global[i] = pelvis_rotation(j, rest);
            else if (i == SM_SPINE3) global[i] = chest_rotation(j, rest);
            else global[i] = bone_rotation(j, rest, i, parent);

            const sm_quat local = sm_qmul(qconj(parent), global[i]);
            const float q[4] = {(float)local.x, (float)local.y, (float)local.z, (float)local.w};
            memcpy(xyzw + (t * SM_J + (size_t)i) * 4, q, sizeof q);
        }
    }
}

static int cmp(const void *a, const void *b) {
    const double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

/* each joint's offset in its parent's frame, the median over the frames per axis; tmp: 3T doubles */
void sm_clip_offsets(const float *joints, const float *xyzw, uint32_t T, double *tmp, float *off) {
    memset(off, 0, sizeof(float) * 3);
    for (int j = 1; j < SM_J; j++) {
        for (uint32_t t = 0; t < T; t++) {
            const float *q = xyzw + (size_t)t * SM_J * 4;
            sm_quat g = {1, 0, 0, 0};
            for (int a = sm_parent[j]; a >= 0; a = sm_parent[a])
                g = sm_qmul((sm_quat){q[4 * a + 3], q[4 * a], q[4 * a + 1], q[4 * a + 2]}, g);
            double d[3], v[3];
            dif(joints + (size_t)t * SM_J * 3, j, sm_parent[j], d);
            sm_qrot(qconj(g), d, v);
            for (int k = 0; k < 3; k++) tmp[(size_t)k * T + t] = v[k];
        }
        for (int k = 0; k < 3; k++) {
            qsort(tmp + (size_t)k * T, T, sizeof(double), cmp);
            off[3 * j + k] = (float)tmp[(size_t)k * T + T / 2];
        }
    }
}

/* two-bone IK: the ankle to target, the knee in its current bend plane; the toe moves with the ankle */
static void leg_ik(float *j, int hip, int knee, int ankle, int toe, const double target[3]) {
    double hk[3], ka[3], d[3], pole[3];
    dif(j, knee, hip, hk);
    dif(j, ankle, knee, ka);
    for (int k = 0; k < 3; k++) d[k] = target[k] - j[3 * hip + k];
    const double a = sqrt(norm2(hk)), b = sqrt(norm2(ka)), lo = fabs(a - b) + 1e-6, hi = a + b - 1e-6;
    double dist = sqrt(norm2(d));
    if (dist < 1e-9) return;
    unit(d);
    const double along = dot(hk, d);
    for (int k = 0; k < 3; k++) pole[k] = hk[k] - along * d[k];
    if (norm2(pole) < 1e-18) return;
    unit(pole);
    dist = dist < lo ? lo : dist > hi ? hi : dist;
    const double x = (a * a - b * b + dist * dist) / (2 * dist), h = sqrt(fmax(0, a * a - x * x));   /* cosine rule */
    for (int k = 0; k < 3; k++) {
        const double hip_k = j[3 * hip + k], new_ankle = hip_k + d[k] * dist;
        j[3 * toe + k] += (float)(new_ankle - j[3 * ankle + k]);
        j[3 * knee + k] = (float)(hip_k + d[k] * x + pole[k] * h);
        j[3 * ankle + k] = (float)new_ankle;
    }
}

#define BLEND 4   /* frames over which a lock fades in and out */

/* a path warp turns the body, which would swivel a planted foot: its toe keeps the yaw it had at touch-down */
static void hold_yaw(float *joints, const uint8_t *contacts, uint32_t T, int side, const double *yaw_added) {
    const int ankle = SM_L_ANKLE + side, toe = SM_L_FOOT + side, col = 2 * side;
    double at_touch = 0, offset = 0;
    uint32_t since = BLEND + 1;
    for (uint32_t t = 0; t < T; t++) {
        const uint8_t *flag = contacts + 4 * t + col;
        const int on = flag[0] || flag[1];
        if (on && (!t || !(flag[-4] || flag[-3]))) at_touch = yaw_added[t];
        if (on) {
            since = 0;
            offset = at_touch - yaw_added[t];
        }
        const double turn_back = on ? offset : ++since <= BLEND ? offset * (1 - since / (BLEND + 1.0)) : 0;
        if (turn_back == 0) continue;

        const float *a = joints + ((size_t)t * SM_J + (size_t)ankle) * 3;
        float *f = joints + ((size_t)t * SM_J + (size_t)toe) * 3;
        const double c = cos(turn_back), sn = sin(turn_back), dx = (double)f[0] - a[0], dz = (double)f[2] - a[2];
        f[0] = (float)(a[0] + dx * c + dz * sn);
        f[2] = (float)(a[2] - dx * sn + dz * c);
    }
}

typedef struct { int hip, knee, ankle, toe, column; } leg;   /* column: the ankle's contact flag */

/* each ankle contact run holds the ankle where the run starts: delta moves it there */
static void hold_ankle(const float *joints, const uint8_t *contacts, uint32_t T, leg g, double *delta) {
    double anchor[3] = {0};
    for (uint32_t t = 0; t < T; t++) {
        const float *ankle = joints + ((size_t)t * SM_J + (size_t)g.ankle) * 3;
        const uint8_t *flag = contacts + 4 * t + g.column;
        const int on = flag[0], start = on && (!t || !flag[-4]);
        for (int k = 0; on && start && k < 3; k++) anchor[k] = ankle[k];
        for (int k = 0; k < 3; k++) delta[3 * t + (uint32_t)k] = on ? anchor[k] - ankle[k] : 0;
    }
}

/* frames up to BLEND away from a run take the nearest run frame's offset, fading */
static void fade_holds(const uint8_t *contacts, uint32_t T, leg g, double *delta) {
    const uint32_t col = (uint32_t)g.column;
    for (uint32_t t = 0; t < T; t++) {
        if (contacts[4 * t + col]) continue;
        for (uint32_t d = 1; d <= BLEND; d++) {
            const int64_t src = t >= d && contacts[4 * (t - d) + col] ? (int64_t)(t - d)
                              : t + d < T && contacts[4 * (t + d) + col] ? (int64_t)(t + d) : -1;
            if (src < 0) continue;
            for (int k = 0; k < 3; k++) delta[3 * t + (uint32_t)k] = delta[3 * src + k] * (1 - d / (BLEND + 1.0));
            break;
        }
    }
}

/* the ankle moved by delta and nothing below the floor (y = 0) */
static void apply_holds(float *joints, uint32_t T, leg g, const double *delta) {
    for (uint32_t t = 0; t < T; t++) {
        float *frame = joints + (size_t)t * SM_J * 3, *ankle = frame + 3 * g.ankle;
        const double *d = delta + 3 * t, low = fmin(ankle[1], frame[3 * g.toe + 1]) + d[1];
        if (d[0] == 0 && d[1] == 0 && d[2] == 0 && low >= 0) continue;
        const double target[3] = {ankle[0] + d[0], ankle[1] + d[1] - fmin(low, 0), ankle[2] + d[2]};
        leg_ik(frame, g.hip, g.knee, g.ankle, g.toe, target);
    }
}

void sm_foot_lock(float *joints, const uint8_t *contacts, uint32_t T, double *delta, const double *yaw_added) {
    static const leg legs[2] = {
        {SM_L_HIP, SM_L_KNEE, SM_L_ANKLE, SM_L_FOOT, 0}, {SM_R_HIP, SM_R_KNEE, SM_R_ANKLE, SM_R_FOOT, 2},
    };
    for (int side = 0; side < 2; side++) {
        hold_ankle(joints, contacts, T, legs[side], delta);
        fade_holds(contacts, T, legs[side], delta);
        apply_holds(joints, T, legs[side], delta);
        if (yaw_added) hold_yaw(joints, contacts, T, side, yaw_added);
    }
}

#define GAP 20   /* frames: a foot with no contact this long has its flags missing (runs) */

#define MAX_CONTACT_SPEED 0.8   /* m/s */

static uint8_t low_and_slow(const float *joints, uint32_t T, uint32_t t, int joint, float low, double fps) {
    const uint32_t prev = t ? t - 1 : 0, next = t + 1 < T ? t + 1 : T - 1;
    const float *x = joints + ((size_t)t * SM_J + (size_t)joint) * 3;
    const float *xp = joints + ((size_t)prev * SM_J + (size_t)joint) * 3, *xn = xp + (next - prev) * SM_J * 3;
    const double dx = (double)xn[0] - xp[0], dz = (double)xn[2] - xp[2];
    return x[1] < low && sqrt(dx * dx + dz * dz) * fps < MAX_CONTACT_SPEED * (next - prev);
}

void sm_fill_contacts(const float *joints, uint8_t *contacts, uint32_t T, double fps) {
    static const int joint[4] = {SM_L_ANKLE, SM_L_FOOT, SM_R_ANKLE, SM_R_FOOT};
    static const float low[4] = {0.10f, 0.04f, 0.10f, 0.04f};   /* m, as the flags are set in walks */
    for (uint32_t k = 0; k < 4; k++) {
        uint32_t end;
        for (uint32_t start = 0; start < T; start = end + 1) {
            for (end = start; end < T && !contacts[4 * end + k]; end++) {}
            if (end - start < GAP) continue;
            for (uint32_t t = start; t < end; t++)
                contacts[4 * t + k] = low_and_slow(joints, T, t, joint[k], low[k], fps);
        }
    }
}

