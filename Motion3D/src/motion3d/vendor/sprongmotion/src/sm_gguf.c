/* A strict GGUF v3 reader (ggml docs/gguf.md); every read goes through take(), which stops at the end. */
#include "sm_internal.h"

enum { GGUF_U32 = 4, GGUF_I32 = 5, GGUF_F32 = 6, GGUF_BOOL = 7, GGUF_STRING = 8, GGUF_ARRAY = 9, GGUF_N_TYPES = 13 };
#define GGUF_VERSION  3   /* beyond the spec: tensors F32, I32 or Q8_0, unique names, limits below */
#define MAX_ENTRIES   4096
#define MAX_STRING    65535
#define MAX_ARRAY     (1u << 24)
#define MAX_DIM       (1u << 30)
#define MAX_DEPTH     4
#define MAX_NAME      64
#define MAX_ALIGNMENT 4096
#define BAD(msg) return sm_fail(err, err_len, SM_E_FORMAT, msg)

typedef struct { const uint8_t *p, *end; int ok; } reader;

static const uint8_t *take(reader *r, uint64_t n) {
    r->ok = r->ok && n <= (uint64_t)(r->end - r->p);
    if (!r->ok) return NULL;
    r->p += n;
    return r->p - n;
}

static uint64_t read_uint(reader *r, int bytes) {
    const uint8_t *q = take(r, (uint64_t)bytes);
    uint64_t v = 0;
    for (int i = bytes - 1; q && i >= 0; i--) v = v << 8 | q[i];
    return v;
}

static const char *read_string(reader *r, uint32_t *len) {
    const uint64_t n = read_uint(r, 8);
    r->ok &= n <= MAX_STRING;
    *len = (uint32_t)n;
    return (const char *)take(r, n);
}

static const char *read_key(reader *r, uint32_t *len, uint32_t *type) {
    const char *key = read_string(r, len);
    *type = (uint32_t)read_uint(r, 4);
    return key;
}

/* 0 for string / array, -1 for an unknown type */
static int scalar_size(uint32_t type) {
    static const int8_t size[GGUF_N_TYPES] = {1, 1, 2, 2, 4, 4, 4, 1, 0, 0, 8, 8, 8};
    return type < GGUF_N_TYPES ? size[type] : -1;
}

static void skip_value(reader *r, uint32_t type, int depth) {
    uint32_t len;
    if (type == GGUF_STRING) {
        read_string(r, &len);
        return;
    }
    if (type == GGUF_ARRAY) {
        const uint32_t item = (uint32_t)read_uint(r, 4);
        const uint64_t n = read_uint(r, 8);
        r->ok &= depth < MAX_DEPTH && scalar_size(item) >= 0 && n <= MAX_ARRAY;
        if (item == GGUF_STRING || item == GGUF_ARRAY || item == GGUF_BOOL)
            for (uint64_t i = 0; i < n && r->ok; i++) skip_value(r, item, depth + 1);
        else take(r, n * (uint64_t)scalar_size(item));
        return;
    }
    r->ok &= scalar_size(type) > 0;
    const uint8_t *v = take(r, (uint64_t)scalar_size(type));
    r->ok &= !(v && type == GGUF_BOOL && *v > 1);
}

static int lower_alnum(char c) { return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9'); }

/* lower_snake_case segments joined by '.' */
static int valid_key(const char *key, uint32_t n) {
    if (!n || key[0] == '.' || key[n - 1] == '.') return 0;
    for (uint32_t i = 0; i < n; i++)
        if (!lower_alnum(key[i]) && key[i] != '_' && !(key[i] == '.' && key[i - 1] != '.')) return 0;
    return 1;
}

static int is_key(const char *key, uint32_t len, const char *want) {
    return len == strlen(want) && !memcmp(key, want, len);
}

uint64_t sm_gguf_count_tensors(const void *buf, size_t size) {
    reader r = {buf, (const uint8_t *)buf + size, 1};
    const uint8_t *magic = take(&r, 4);
    const uint64_t version = read_uint(&r, 4), n = read_uint(&r, 8);
    return r.ok && !memcmp(magic, "GGUF", 4) && version == GGUF_VERSION && n <= MAX_ENTRIES ? n : 0;
}

static int read_architecture(reader *r) {
    uint32_t len;
    const char *arch = read_string(r, &len);
    r->ok &= len > 0;
    for (uint32_t c = 0; arch && c < len; c++) r->ok &= lower_alnum(arch[c]);
    return r->ok;
}

/* the key-value section; general.alignment and general.architecture are checked here */
static int32_t read_metadata(reader *r, uint64_t n_kv, uint32_t *align, char *err, size_t err_len) {
    for (uint64_t i = 0; i < n_kv && r->ok; i++) {
        uint32_t len, type;
        const char *key = read_key(r, &len, &type);
        if (r->ok && !valid_key(key, len)) BAD("bad metadata key");
        if (r->ok && type == GGUF_U32 && is_key(key, len, "general.alignment")) {
            *align = (uint32_t)read_uint(r, 4);
            if (!*align || *align % 8 || *align > MAX_ALIGNMENT) BAD("bad alignment");
        } else if (r->ok && type == GGUF_STRING && is_key(key, len, "general.architecture")) {
            read_architecture(r);
        } else {
            skip_value(r, type, 0);
        }
    }
    return r->ok ? SM_OK : sm_fail(err, err_len, SM_E_FORMAT, "malformed metadata");
}

static int32_t read_tensor_info(reader *r, sm_tensor *x, uint32_t align, char *err, size_t err_len) {
    uint64_t elems = 1;
    x->name = read_string(r, &x->name_len);
    x->nd = (uint32_t)read_uint(r, 4);
    if (!r->ok || !x->name_len || x->name_len > MAX_NAME || x->nd < 1 || x->nd > 4) BAD("bad tensor header");
    for (uint32_t d = 0; d < x->nd; d++) {
        x->dim[d] = read_uint(r, 8);
        if (!x->dim[d] || x->dim[d] > MAX_DIM || elems > (UINT64_MAX >> 31) / x->dim[d]) BAD("bad tensor shape");
        elems *= x->dim[d];
    }
    x->type = (uint32_t)read_uint(r, 4);
    x->offset = read_uint(r, 8);
    if (x->type == SM_T_Q8_0 && x->dim[0] % 32) BAD("bad Q8_0 row length");
    if (x->type != SM_T_F32 && x->type != SM_T_I32 && x->type != SM_T_Q8_0) BAD("bad tensor type");
    x->bytes = (size_t)(x->type == SM_T_Q8_0 ? elems / 32 * SM_Q8_BLOCK : elems * 4);
    if (!r->ok || x->offset % align) BAD("bad tensor offset");
    return SM_OK;
}

static int32_t read_tensors(reader *r, sm_gguf *g, uint32_t align, char *err, size_t err_len) {
    sm_tensor *t = g->t;
    for (uint64_t i = 0; i < g->n_t; i++) {
        const int32_t s = read_tensor_info(r, &t[i], align, err, err_len);
        if (s != SM_OK) return s;
        for (uint64_t j = 0; j < i; j++)
            if (t[j].name_len == t[i].name_len && !memcmp(t[j].name, t[i].name, t[i].name_len)) BAD("duplicate name");
    }
    return SM_OK;
}

/* the data section starts at the next alignment, after zero padding */
static int32_t place_data(const reader *r, sm_gguf *g, uint32_t align, char *err, size_t err_len) {
    const size_t start = ((size_t)(r->p - g->base) + align - 1) / align * align;
    if (start > g->size) BAD("truncated file");
    for (const uint8_t *p = r->p; p < g->base + start; p++)
        if (*p) BAD("header padding is not zero");

    g->data = g->base + start;
    g->data_size = g->size - start;
    for (uint64_t i = 0; i < g->n_t; i++) {
        sm_tensor *x = &g->t[i];
        if (x->offset > g->data_size || x->bytes > g->data_size - x->offset) BAD("tensor data outside the file");
        x->data = g->data + x->offset;
    }
    return SM_OK;
}

int32_t sm_gguf_open(sm_gguf *g, const void *buf, size_t size, sm_tensor *t, uint64_t max_t, char *err,
                     size_t err_len) {
    reader r = {buf, (const uint8_t *)buf + size, 1};
    memset(g, 0, sizeof *g);
    g->base = buf;
    g->size = size;
    const uint8_t *magic = take(&r, 4);
    if (!magic || memcmp(magic, "GGUF", 4)) BAD("not a GGUF file");
    if (read_uint(&r, 4) != GGUF_VERSION) return sm_fail(err, err_len, SM_E_VERSION, "GGUF version is not 3");
    g->n_t = read_uint(&r, 8);
    g->n_kv = read_uint(&r, 8);
    if (!r.ok || g->n_t > max_t || g->n_kv > MAX_ENTRIES) BAD("bad tensor count");

    uint32_t align = 32;
    g->kv = r.p;
    g->t = t;
    int32_t s = read_metadata(&r, g->n_kv, &align, err, err_len);
    if (s == SM_OK) s = read_tensors(&r, g, align, err, err_len);
    if (s == SM_OK) s = place_data(&r, g, align, err, err_len);
    return s;
}

const void *sm_gguf_tensor(const sm_gguf *g, const char *name, uint32_t type, uint32_t nd, uint64_t *dims) {
    for (uint64_t i = 0; i < g->n_t; i++) {
        const sm_tensor *x = &g->t[i];
        if (!is_key(x->name, x->name_len, name)) continue;
        if (x->type != type || x->nd != nd) return NULL;
        for (uint32_t d = 0; d < nd; d++)
            if (dims[d] && x->dim[d] != dims[d]) return NULL;
        for (uint32_t d = 0; d < nd; d++) dims[d] = x->dim[d];
        return x->data;
    }
    return NULL;
}

int sm_gguf_num(const sm_gguf *g, const char *key, double *out) {
    reader r = {g->kv, g->base + g->size, 1};
    for (uint64_t i = 0; i < g->n_kv && r.ok; i++) {
        uint32_t len, type;
        const char *k = read_key(&r, &len, &type);
        if (!r.ok || !is_key(k, len, key)) {
            skip_value(&r, type, 0);
            continue;
        }
        const union { uint32_t u; int32_t i; float f; } x = {(uint32_t)read_uint(&r, 4)};
        const double v = type == GGUF_U32 ? (double)x.u : type == GGUF_I32 ? (double)x.i : (double)x.f;
        if (!r.ok || (type != GGUF_U32 && type != GGUF_I32 && type != GGUF_F32) || !isfinite(v)) return 0;
        *out = v;
        return 1;
    }
    return 0;
}

size_t sm_gguf_strings(const sm_gguf *g, char *dst, const char **kv, uint32_t max, uint32_t *n) {
    reader r = {g->kv, g->base + g->size, 1};
    size_t bytes = 0;
    *n = 0;
    for (uint64_t i = 0; i < g->n_kv && r.ok; i++) {
        uint32_t key_len, value_len, type;
        const char *key = read_key(&r, &key_len, &type);
        const char *value = type == GGUF_STRING ? read_string(&r, &value_len) : NULL;
        if (!value) {
            skip_value(&r, type, 0);
            continue;
        }
        if (dst && *n < max) {
            kv[2 * *n] = memcpy(dst + bytes, key, key_len);
            kv[2 * *n + 1] = memcpy(dst + bytes + key_len + 1, value, value_len);
            dst[bytes + key_len] = 0;
            dst[bytes + key_len + 1 + value_len] = 0;
            ++*n;
        }
        bytes += (size_t)key_len + value_len + 2;
    }
    return bytes;
}

/* zlib's CRC-32; the table is built per call: no global state */
uint32_t sm_crc32(const uint8_t *p, size_t n) {
    uint32_t table[256], crc = 0xFFFFFFFFu;
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t v = i;
        for (int k = 0; k < 8; k++) v = v & 1 ? 0xEDB88320u ^ (v >> 1) : v >> 1;
        table[i] = v;
    }
    for (size_t i = 0; i < n; i++) crc = table[(crc ^ p[i]) & 0xFF] ^ (crc >> 8);
    return crc ^ 0xFFFFFFFFu;
}
