/* Root paths: a centripetal Catmull-Rom curve walked by arc length, and the root warped onto it (Gleicher 2001). */
#include "sm_internal.h"

#define PI     3.14159265358979323846
#define CHORDS 32     /* per curve segment */
#define SIGMA  0.25   /* s: the root's smoothing, about half a step cycle */
#define STILL  0.1    /* m/s: below it the root's heading holds (its tangent spins at a pause) */
#define CREEP  1e-4   /* m/s added to the root's arc length, so timed points stay ordered while it stands */

/* "path[i].x = v is out of range lo .. hi"; a time not after the one before says so */
static sm_status bad(sm_request *r, uint32_t i, int k, double v, double lo, double hi) {
    static const char *const what[4] = {"time", "x", "z", "heading"};
    char name[32];
    snprintf(name, sizeof name, "path[%u].%s", i, what[k]);
    if (k || !(v >= lo && v <= hi)) return sm_fail_range(r->err, sizeof r->err, SM_E_ARG, name, v, lo, hi);
    sm_buf b = {r->err, sizeof r->err - 1, 0};
    sm_puts(&b, name);
    sm_puts(&b, " = ");
    sm_putn(&b, v);
    sm_puts(&b, " is not after the time before it");
    r->err[b.n < b.cap ? b.n : b.cap] = 0;
    return SM_E_ARG;
}

sm_status sm_set_path(sm_request *r, const float *points, int count) {
    if (count && !points) return sm_fail(r->err, sizeof r->err, SM_E_ARG, "path is NULL");
    if (count < 0 || count == 1 || count > SM_MAX_PATH)
        return sm_fail_range(r->err, sizeof r->err, SM_E_ARG, "path_count", count, 2, SM_MAX_PATH);
    const uint32_t n = (uint32_t)count;
    double last = -1;   /* a free first point is at 0 */
    for (uint32_t i = 0; i < n; i++) {
        const float *p = points + 4 * i;
        for (int k = 1; k < 4; k++)
            if (!(fabsf(p[k]) <= 1000) && !(k == 3 && isnan(p[k]))) return bad(r, i, k, p[k], -1000, 1000);
        if (isnan(p[0])) last = i ? last : 0;
        else if (!(p[0] > last && p[0] <= 600)) return bad(r, i, 0, p[0], last < 0 ? 0 : last, 600);
        else last = p[0];
    }
    if (n) memcpy(r->path.p, points, sizeof(float) * 4 * n);
    r->path.n = n;
    return SM_OK;
}

sm_status sm_path_check(const sm_model_impl *m, const sm_path *p, uint32_t tokens, char *err, size_t err_len) {
    const double seconds = (double)tokens * m->frames_per_token / m->fps;
    for (uint32_t i = 0; i < p->n; i++)
        if (p->p[4 * i] > seconds + 1e-3) {
            char name[24];
            snprintf(name, sizeof name, "path[%u].time", i);
            return sm_fail_range(err, err_len, SM_E_ARG, name, p->p[4 * i], 0, seconds);
        }
    return SM_OK;
}

/* Gaussian; past the ends the root is mirrored through its end point, so steady travel is kept up to the ends */
void sm_root_smooth(const float *joints, uint32_t T, double fps, double *g, float *speed) {
    enum { R = 15 };
    const double sd = SIGMA * fps;
    const int64_t last = (int64_t)T - 1;
    double w[R + 1], sum = 0;
    for (int k = 0; k <= R; k++) {
        w[k] = sm_exp(-0.5 * k * k / (sd * sd));
        sum += k ? 2 * w[k] : w[k];
    }
    for (int64_t t = 0; t < T; t++)
        for (int a = 0; a < 2; a++) {
            double x = 0;
            for (int64_t k = -R; k <= R; k++) {
                const int64_t i = t + k, m = i < 0 ? -i : i > last ? 2 * last - i : i;
                const int64_t c = m < 0 ? 0 : m > last ? last : m;
                const double v = joints[(size_t)c * SM_J * 3 + 2 * (size_t)a];
                const double end = joints[(i < 0 ? 0 : (size_t)last) * SM_J * 3 + 2 * (size_t)a];
                x += w[k < 0 ? -k : k] * (c == i ? v : 2 * end - v);
            }
            g[2 * t + a] = x / sum;
        }
    for (uint32_t t = 0; t < T; t++) {
        const uint32_t a = t ? t - 1 : 0, b = t + 1 < T ? t + 1 : T - 1;
        const double dx = g[2 * b] - g[2 * a], dz = g[2 * b + 1] - g[2 * a + 1];
        speed[t] = b > a ? (float)(sqrt(dx * dx + dz * dz) * fps / (b - a)) : 0;
    }
}

/* the curve through the distinct points; closed: the last point repeats the first */
typedef struct { const double *x, *z; int64_t n; int closed; } curve;

static void point(const curve *c, int64_t i, double q[2]) {
    const int64_t n = c->n;
    if (c->closed) i = (i % n + n) % n;
    const int64_t a = i < 0 ? 0 : n - 1, b = i < 0 ? 1 : n - 2;   /* outside: mirrored through the end */
    if (i >= 0 && i < n) {
        q[0] = c->x[i];
        q[1] = c->z[i];
        return;
    }
    q[0] = 2 * c->x[a] - c->x[b];
    q[1] = 2 * c->z[a] - c->z[b];
}

static double dist(const double a[2], const double b[2]) {
    return sqrt((a[0] - b[0]) * (a[0] - b[0]) + (a[1] - b[1]) * (a[1] - b[1]));
}

/* segment s at u in 0..1: position and derivative, knots t_k+1 = t_k + |P_k+1 - P_k|^0.5 (centripetal: no cusps or
   self-intersections, Yuksel et al. 2011) */
static void eval(const curve *c, uint32_t s, double u, double p[2], double d[2]) {
    double P[4][2], t[4] = {0};
    for (int k = 0; k < 4; k++) point(c, (int64_t)s + k - 1, P[k]);
    for (int k = 1; k < 4; k++) t[k] = t[k - 1] + sqrt(dist(P[k], P[k - 1]));
    const double h = t[2] - t[1], u2 = u * u, u3 = u2 * u;
    for (int a = 0; a < 2; a++) {
        const double m1 = h * ((P[1][a] - P[0][a]) / (t[1] - t[0]) - (P[2][a] - P[0][a]) / (t[2] - t[0]) +
                               (P[2][a] - P[1][a]) / h);
        const double m2 = h * ((P[2][a] - P[1][a]) / h - (P[3][a] - P[1][a]) / (t[3] - t[1]) +
                               (P[3][a] - P[2][a]) / (t[3] - t[2]));
        p[a] = (2 * u3 - 3 * u2 + 1) * P[1][a] + (u3 - 2 * u2 + u) * m1 + (3 * u2 - 2 * u3) * P[2][a] + (u3 - u2) * m2;
        d[a] = (6 * u2 - 6 * u) * (P[1][a] - P[2][a]) + (3 * u2 - 4 * u + 1) * m1 + (3 * u2 - 2 * u) * m2;
    }
}

static double heading(const double d[2], double near) {   /* 0 = +Z, pi/2 = +X; the turn nearest to near */
    return near + remainder(atan2(d[0], d[1]) - near, 2 * PI);
}

/* walks the curve one chord at a time: chord A..B starts at arc length a */
typedef struct { const curve *c; uint32_t seg, k, segs; double a, len, A[2], B[2], hA, hB; } cursor;

static void next_chord(cursor *q) {
    double d[2];
    q->A[0] = q->B[0];
    q->A[1] = q->B[1];
    q->hA = q->hB;
    eval(q->c, q->seg, (q->k + 1.0) / CHORDS, q->B, d);
    q->hB = heading(d, q->hA);
    q->len = dist(q->A, q->B);
}

static void start(cursor *q, const curve *c) {
    double d[2];
    *q = (cursor){c, 0, 0, (uint32_t)(c->closed ? c->n : c->n - 1), 0, 0, {0}, {0}, 0, 0};
    eval(c, 0, 0, q->B, d);
    q->hB = atan2(d[0], d[1]);
    next_chord(q);
}

static int step(cursor *q) {   /* 0 at the curve's end */
    if (q->k + 1 == CHORDS && q->seg + 1 == q->segs) return 0;
    q->a += q->len;
    if (++q->k == CHORDS) {
        q->k = 0;
        q->seg++;
    }
    next_chord(q);
    return 1;
}

/* monotone cubic through (x_k, y_k) with Fritsch-Butland tangents (harmonic mean of the secants, 0 at an extremum) */
static double monotone(const double *x, const double *y, const double *m, uint32_t n, uint32_t *j, double v) {
    while (*j + 2 < n && v > x[*j + 1]) ++*j;
    const uint32_t i = *j;
    const double h = x[i + 1] - x[i], w = (v - x[i]) / h, w2 = w * w, w3 = w2 * w;
    return (2 * w3 - 3 * w2 + 1) * y[i] + (w3 - 2 * w2 + w) * h * m[i] + (3 * w2 - 2 * w3) * y[i + 1] +
           (w3 - w2) * h * m[i + 1];
}

static void tangents(const double *x, const double *y, double *m, uint32_t n) {
    for (uint32_t i = 0; i < n; i++) {
        const double l = i ? (y[i] - y[i - 1]) / (x[i] - x[i - 1]) : 0;
        const double r = i + 1 < n ? (y[i + 1] - y[i]) / (x[i + 1] - x[i]) : 0;
        m[i] = !i ? r : i + 1 == n ? l : l * r > 0 ? 2 * l * r / (l + r) : 0;
    }
}

/* the root's heading psi from the smoothed root g, held below STILL; a root that never moves faces as its hips do */
static void root_heading(const float *joints, const double *g, uint32_t T, double fps, double *psi) {
    int64_t first = -1;
    for (uint32_t t = 0; t < T; t++) {
        const uint32_t a = t ? t - 1 : 0, b = t + 1 < T ? t + 1 : T - 1;
        const double d[2] = {g[2 * b] - g[2 * a], g[2 * b + 1] - g[2 * a + 1]};
        const int moving = b > a && sqrt(d[0] * d[0] + d[1] * d[1]) * fps / (b - a) >= STILL;
        psi[t] = moving ? heading(d, first < 0 ? 0 : psi[t - 1]) : t ? psi[t - 1] : 0;
        for (uint32_t k = 0; moving && first < 0 && k < t; k++) psi[k] = psi[t];
        if (moving && first < 0) first = t;
    }
    if (first >= 0) return;
    const double ax = (double)joints[3] - joints[6], az = (double)joints[5] - joints[8];   /* l_hip - r_hip */
    for (uint32_t t = 0; t < T; t++) psi[t] = atan2(-az, ax);
}

/* the distinct points of p; index: each given point's distinct one */
static uint32_t distinct_points(const sm_path *p, double *x, double *z, uint32_t *index) {
    uint32_t n = 0;
    for (uint32_t i = 0; i < p->n; i++) {
        const double px = p->p[4 * i + 1], pz = p->p[4 * i + 2];
        if (!n || fabs(px - x[n - 1]) + fabs(pz - z[n - 1]) > 1e-6) {
            x[n] = px;
            z[n] = pz;
            n++;
        }
        index[i] = n - 1;
    }
    return n;
}

/* arc[i]: the curve's arc length at distinct point i; returns its length */
static double curve_arcs(const curve *c, uint32_t n, double *arc) {
    cursor q;
    start(&q, c);
    do {
        if (!q.k) arc[q.seg] = q.a;
    } while (step(&q));
    arc[n - 1] = q.a + q.len;
    return arc[n - 1];
}

/* the smoothed root's own arc length per frame, creeping on while it stands */
static void root_arcs(const double *g, uint32_t T, double fps, double *u) {
    u[0] = 0;
    for (uint32_t t = 1; t < T; t++) {
        const double dx = g[2 * t] - g[2 * t - 2], dz = g[2 * t + 1] - g[2 * t - 1];
        u[t] = u[t - 1] + sqrt(dx * dx + dz * dz) + CREEP / fps;
    }
}

/* knots (root arc length at the point's time, curve arc length of the point), rising, from (0, 0) to the end */
static uint32_t timed_knots(const sm_path *p, const uint32_t *index, const double *arc, uint32_t n, const double *u,
                            uint32_t T, double fps, double length, double *kx, double *ky) {
    uint32_t nk = 1;
    kx[0] = ky[0] = 0;
    for (uint32_t i = 0; i < p->n; i++) {
        if (isnan(p->p[4 * i])) continue;
        const double f = fmin(p->p[4 * i] * fps, T - 1.0), w = f - floor(f);
        const uint32_t a = (uint32_t)f, b = a + 1 < T ? a + 1 : a;
        const double x = u[a] + w * (u[b] - u[a]);
        if (x <= kx[nk - 1]) continue;
        kx[nk] = x;
        ky[nk++] = n > 1 ? arc[index[i]] : 0;
    }
    if (u[T - 1] > kx[nk - 1]) {
        kx[nk] = u[T - 1];
        ky[nk++] = length;
    }
    return nk;
}

/* every joint about the smoothed root g, turned by angle, onto P */
static void move_frame(float *frame, const double g[2], const double P[2], double angle) {
    const double cs = cos(angle), sn = sin(angle);
    for (int k = 0; k < SM_J; k++) {
        const double dx = frame[3 * k] - g[0], dz = frame[3 * k + 2] - g[1];
        frame[3 * k] = (float)(P[0] + dx * cs + dz * sn);
        frame[3 * k + 2] = (float)(P[1] - dx * sn + dz * cs);
    }
}

double sm_path_warp(const sm_path *p, float *joints, uint32_t T, double fps, const double *g, int warp,
                    double *yaw_added, double *tmp) {
    double *psi = tmp, *u = psi + T, *px = u + T, *pz = px + SM_MAX_PATH, *arc = pz + SM_MAX_PATH;
    double *kx = arc + SM_MAX_PATH, *ky = kx + SM_MAX_PATH + 2, *km = ky + SM_MAX_PATH + 2;
    uint32_t index[SM_MAX_PATH];
    const uint32_t n = distinct_points(p, px, pz, index);
    const double first[2] = {px[0], pz[0]}, last[2] = {px[n - 1], pz[n - 1]};
    const int closed = n >= 4 && dist(first, last) < 1e-3;
    const curve c = {px, pz, closed ? n - 1 : n, closed};
    const double length = n > 1 ? curve_arcs(&c, n, arc) : 0;

    root_heading(joints, g, T, fps, psi);
    root_arcs(g, T, fps, u);
    const uint32_t nk = timed_knots(p, index, arc, n, u, T, fps, length, kx, ky);
    tangents(kx, ky, km, nk);

    cursor q;
    if (n > 1) start(&q, &c);
    double err = 0;
    uint32_t knot = 0;
    for (uint32_t t = 0; t < T; t++) {
        const double S = nk > 1 ? fmin(fmax(monotone(kx, ky, km, nk, &knot, u[t]), 0), length) : 0;
        double P[2] = {px[0], pz[0]}, heading_at = psi[t];
        if (n > 1) {
            while (S > q.a + q.len && step(&q)) {}
            const double f = q.len > 0 ? fmin(fmax((S - q.a) / q.len, 0), 1) : 0;
            P[0] = q.A[0] + f * (q.B[0] - q.A[0]);
            P[1] = q.A[1] + f * (q.B[1] - q.A[1]);
            heading_at = q.hA + f * (q.hB - q.hA);
        }
        err = fmax(err, dist(g + 2 * t, P));
        if (!warp) continue;
        yaw_added[t] = heading_at - psi[t];
        move_frame(joints + (size_t)t * SM_J * 3, g + 2 * t, P, yaw_added[t]);
    }
    return err;
}
