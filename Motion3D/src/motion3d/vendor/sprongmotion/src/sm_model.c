/* Loading a model, the settings, errors and the library basics. */
#define _POSIX_C_SOURCE 200809L   /* mmap, open */
#include "sm_internal.h"
#include <stdatomic.h>
#include <stdlib.h>
#ifdef _WIN32
#include <windows.h>
#elif defined(__unix__) || defined(__APPLE__)
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

int32_t sm_fail(char *err, size_t err_len, int32_t status, const char *msg) {
    if (err && err_len) snprintf(err, err_len, "%s", msg);
    return status;
}

int32_t sm_fail_range(char *err, size_t err_len, int32_t status, const char *name, double v, double lo, double hi) {
    sm_buf b = {err, err_len ? err_len - 1 : 0, 0};
    sm_puts(&b, name);
    sm_puts(&b, " = ");
    if (isfinite(v)) sm_putn(&b, v);
    else sm_puts(&b, v != v ? "nan" : v > 0 ? "inf" : "-inf");
    sm_puts(&b, lo <= v && v <= hi ? " is not a whole number in " : " is out of range ");
    sm_putn(&b, lo);
    sm_puts(&b, " .. ");
    sm_putn(&b, hi);
    if (err && err_len) err[b.n < b.cap ? b.n : b.cap] = 0;
    return status;
}

#define ALIGN 64       /* SIMD rows, cache lines */
#define SPARE_CTXS 8   /* idle contexts a model keeps for the next calls */
#define ERROR_SIZE 256

static sm_hooks settings;
static _Thread_local char last_error[ERROR_SIZE];

const sm_hooks *sm_settings(void) { return &settings; }
void sm_set_error(const char *msg) { snprintf(last_error, sizeof last_error, "%s", msg); }

/* an empty input keeps the error that made it, so a chain of calls reports the first failure */
int sm_check_input(const void *internal, const char *msg) {
    if (internal) {
        last_error[0] = 0;
        return 1;
    }
    if (!last_error[0]) sm_set_error(msg);
    return 0;
}
const char *sm_get_error(void) { return last_error; }
const char *sm_get_version(void) { return SM_VERSION; }

const char *sm_get_build_info(void) {
#if defined(__wasm_simd128__)
    return SM_VERSION " wasm-simd128 exact";
#elif defined(__wasm__)
    return SM_VERSION " wasm-plain exact";
#elif defined(SM_FMA)
    return SM_VERSION " native fma (not bit-identical to other builds)";
#else
    return SM_VERSION " native exact";
#endif
}

void sm_set_thread_count(int count) { settings.n_threads = count > 0 ? (uint32_t)count : 0; }

void sm_set_parallel_for(void (*parallel_for)(void *user, int n, sm_task_fn task, void *arg), void *user) {
    settings.parallel_for = parallel_for;
    settings.user = user;
}

void sm_set_memory_functions(void *(*alloc)(size_t size, size_t align), void (*free_fn)(void *ptr)) {
    sm_set_error("");
    if (!alloc != !free_fn) {
        sm_set_error("set both alloc and free, or neither");
        return;
    }
    settings.alloc = alloc;
    settings.free = free_fn;
}

static const struct { sm_status s; const char *msg; } STATUS[] = {
    {SM_OK, "ok"}, {SM_DONE, "done"}, {SM_E_ARG, "invalid argument"},
    {SM_E_FORMAT, "not a valid SprongMotion model file"}, {SM_E_VERSION, "the model file needs a newer runtime"},
    {SM_E_CHECKSUM, "the model file is corrupted (checksum)"}, {SM_E_NOMEM, "out of memory"},
    {SM_E_CANCELLED, "cancelled"}, {SM_E_NUMERIC, "NaN or Inf met"},
    {SM_E_UNSUPPORTED, "this model file has no such input"}, {SM_E_IO, "file read error"},
    {SM_E_STATE, "call out of order"},
};

const char *sm_status_message(sm_status s) {
    for (size_t i = 0; i < sizeof STATUS / sizeof STATUS[0]; i++)
        if (STATUS[i].s == s) return STATUS[i].msg;
    return "unknown status";
}

void *sm_alloc(const sm_hooks *h, size_t size) {
    if (h->alloc) return h->alloc(size, ALIGN);
#ifdef _WIN32
    return _aligned_malloc(size, ALIGN);
#else
    void *p = NULL;
    return posix_memalign(&p, ALIGN, size ? size : 1) ? NULL : p;
#endif
}

void sm_dealloc(const sm_hooks *h, void *p) {
    if (h->free) {
        if (p) h->free(p);
        return;
    }
#ifdef _WIN32
    _aligned_free(p);
#else
    free(p);
#endif
}

// Memory handed to the caller keeps the free function it was made with in front of it, for sm_free.
typedef struct { void (*free)(void *ptr); } caller_header;
#define CALLER_HEADER_SIZE ALIGN

void *sm_alloc_for_caller(const sm_hooks *h, size_t size) {
    char *base = sm_alloc(h, CALLER_HEADER_SIZE + size);
    if (!base) return NULL;
    ((caller_header *)base)->free = h->free;
    return base + CALLER_HEADER_SIZE;
}

void sm_free(void *ptr) {
    if (!ptr) return;
    char *base = (char *)ptr - CALLER_HEADER_SIZE;
    const sm_hooks h = {.free = ((caller_header *)base)->free};
    sm_dealloc(&h, base);
}

typedef struct { sm_job_fn fn; void *arg; } job;

static void run_job(void *arg, int index) {
    const job *j = arg;
    j->fn(j->arg, (uint32_t)index);
}

/* the host's parallel_for takes int indices */
static void host_parallel_for(void *model, uint32_t n, sm_job_fn fn, void *arg) {
    const sm_model_impl *m = model;
    job j = {fn, arg};
    m->h.parallel_for(m->h.user, (int)n, run_job, &j);
}

struct sm_spare_ctxs {
    atomic_flag lock;
    uint32_t n;
    sm_ctx *idle[SPARE_CTXS];
};

static void lock_spares(sm_spare_ctxs *s) {
    while (atomic_flag_test_and_set_explicit(&s->lock, memory_order_acquire)) {}
}

static void unlock_spares(sm_spare_ctxs *s) { atomic_flag_clear_explicit(&s->lock, memory_order_release); }

sm_ctx *sm_borrow_ctx(const sm_model_impl *m) {
    sm_spare_ctxs *s = m->spares;
    lock_spares(s);
    sm_ctx *c = s->n ? s->idle[--s->n] : NULL;
    unlock_spares(s);
    if (!c && sm_ctx_create(m, &c) != SM_OK) {
        sm_set_error("out of memory");
        return NULL;
    }
    c->err[0] = 0;
    return c;
}

void sm_return_ctx(sm_ctx *c) {
    sm_spare_ctxs *s = c->m->spares;
    lock_spares(s);
    if (s->n < SPARE_CTXS) {
        s->idle[s->n++] = c;
        c = NULL;
    }
    unlock_spares(s);
    sm_ctx_free(c);
}

sm_status sm_load(const void *bytes, size_t size, uint32_t flags, sm_model_impl **out, char *err, size_t err_len) {
    const sm_hooks h = settings;
    *out = NULL;
    const uint64_t n_tensors = sm_gguf_count_tensors(bytes, size);
    if (!n_tensors) return sm_fail(err, err_len, SM_E_FORMAT, "not a GGUF v3 file");

    sm_model_impl *m = sm_alloc(&h, sizeof *m);
    if (!m) return sm_fail(err, err_len, SM_E_NOMEM, "out of memory");
    memset(m, 0, sizeof *m);
    m->h = h;
    const uint8_t *buf = bytes;
    if (!(flags & SM_LOAD_BORROW)) {   /* own a copy, so the caller may free theirs */
        if ((m->own = sm_alloc(&h, size))) memcpy(m->own, bytes, size);
        buf = m->own;
    }
    m->tensors = buf ? sm_alloc(&h, sizeof(sm_tensor) * (size_t)n_tensors) : NULL;
    m->spares = sm_alloc(&h, sizeof *m->spares);
    sm_status s = m->tensors && m->spares ? sm_gguf_open(&m->g, buf, size, m->tensors, n_tensors, err, err_len)
                                          : sm_fail(err, err_len, SM_E_NOMEM, "out of memory");
    if (m->spares) {
        m->spares->n = 0;
        atomic_flag_clear(&m->spares->lock);
    }
    if (s == SM_OK) s = sm_bind_all(m, flags, err, err_len);
    if (s != SM_OK) {
        sm_unload(m);
        return s;
    }

    if (h.parallel_for) m->par = (sm_par){host_parallel_for, m};
    else if ((m->pool = sm_pool_create(&h, h.n_threads))) m->par = (sm_par){sm_pool_run, m->pool};
    *out = m;
    return SM_OK;
}

#ifdef _WIN32
static wchar_t *widen(const char *utf8) {   /* UTF-8 -> UTF-16, malloc'd */
    const int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, utf8, -1, NULL, 0);
    wchar_t *w = n > 0 ? malloc(sizeof(wchar_t) * (size_t)n) : NULL;
    if (w && MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, utf8, -1, w, n) != n) {
        free(w);
        w = NULL;
    }
    return w;
}

static const void *map_file(const char *path, size_t *size) {
    wchar_t *w = widen(path);
    HANDLE f = w ? CreateFileW(w, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL)
                 : INVALID_HANDLE_VALUE;
    free(w);
    if (f == INVALID_HANDLE_VALUE) return NULL;
    LARGE_INTEGER n;
    HANDLE map = GetFileSizeEx(f, &n) && n.QuadPart > 0 ? CreateFileMappingW(f, NULL, PAGE_READONLY, 0, 0, NULL) : NULL;
    const void *p = map ? MapViewOfFile(map, FILE_MAP_READ, 0, 0, 0) : NULL;
    if (map) CloseHandle(map);
    CloseHandle(f);
    *size = p ? (size_t)n.QuadPart : 0;
    return p;
}

static void unmap(const void *p, size_t n) { (void)n; UnmapViewOfFile(p); }

FILE *sm_open_file(const char *path, const char *mode) {
    wchar_t *w = widen(path), wmode[4] = {0};
    for (int i = 0; i < 3 && mode[i]; i++) wmode[i] = (wchar_t)mode[i];
    FILE *f = w ? _wfopen(w, wmode) : NULL;
    free(w);
    return f;
}
#elif defined(__unix__) || defined(__APPLE__)
static const void *map_file(const char *path, size_t *size) {
    const int fd = open(path, O_RDONLY);
    struct stat st;
    void *p = fd >= 0 && !fstat(fd, &st) && st.st_size > 0
            ? mmap(NULL, (size_t)st.st_size, PROT_READ, MAP_PRIVATE, fd, 0) : MAP_FAILED;
    if (fd >= 0) close(fd);
    *size = p == MAP_FAILED ? 0 : (size_t)st.st_size;
    return p == MAP_FAILED ? NULL : p;
}

static void unmap(const void *p, size_t n) { munmap((void *)(uintptr_t)p, n); }
FILE *sm_open_file(const char *path, const char *mode) { return fopen(path, mode); }
#else
static const void *map_file(const char *path, size_t *size) { (void)path; *size = 0; return NULL; }
static void unmap(const void *p, size_t n) { (void)p; (void)n; }
FILE *sm_open_file(const char *path, const char *mode) { return fopen(path, mode); }
#endif

static sm_status load_mapped(const char *path, sm_model_impl **out, char *err, size_t err_len) {
    size_t n = 0;
    const void *map = map_file(path, &n);
    if (!map) return SM_E_IO;
    const sm_status s = sm_load(map, n, SM_LOAD_BORROW, out, err, err_len);
    if (s != SM_OK) {
        unmap(map, n);
        return s;
    }
    (*out)->map = map;
    (*out)->map_size = n;
    return SM_OK;
}

static sm_status load_read(const char *path, sm_model_impl **out, char *err, size_t err_len) {
    FILE *f = sm_open_file(path, "rb");
    if (!f) return sm_fail(err, err_len, SM_E_IO, "cannot open the model file");
    const long size = fseek(f, 0, SEEK_END) == 0 ? ftell(f) : -1;
    void *buf = size > 0 && fseek(f, 0, SEEK_SET) == 0 ? malloc((size_t)size) : NULL;
    const int ok = buf && fread(buf, 1, (size_t)size, f) == (size_t)size;
    fclose(f);
    const sm_status s = ok ? sm_load(buf, (size_t)size, 0, out, err, err_len)
                           : sm_fail(err, err_len, SM_E_IO, "cannot read the model file");
    free(buf);
    return s;
}

void sm_unload(sm_model_impl *m) {
    if (!m) return;
    const sm_hooks h = m->h;
    const void *map = m->map;
    const size_t map_size = m->map_size;
    for (uint32_t i = 0; m->spares && i < m->spares->n; i++) sm_ctx_free(m->spares->idle[i]);
    sm_pool_free(&h, m->pool);
    void *owned[] = {m->packed, m->meta_mem, m->vocab, m->tok_table, m->tok.remap, m->tensors, m->spares, m->own, m};
    for (size_t i = 0; i < sizeof owned / sizeof owned[0]; i++) sm_dealloc(&h, owned[i]);
    if (map) unmap(map, map_size);
}

const char *sm_meta(const sm_model_impl *m, const char *key) {
    for (uint32_t i = 0; key && i < m->n_meta; i++)
        if (!strcmp(m->meta[2 * i], key)) return m->meta[2 * i + 1];
    return NULL;
}

static sm_model wrap_model(sm_model_impl *m) {
    if (!m) return (sm_model){0};
    for (int j = 0; j < SM_J; j++) m->parents[j] = sm_parent[j];
    return (sm_model){
        .id = m->id, .fps = (int)m->fps, .frames_per_token = (int)m->frames_per_token,
        .max_seconds = (float)m->param[SM_P_SECONDS][1], .code_layers = SM_VQ_LAYERS, .feature_size = SM_F,
        .text_vector_size = (int)m->text.out.n, .joint_count = SM_J, .joint_names = sm_joint_names,
        .joint_parents = m->parents, .rest_offsets = m->rest, .has_generator = m->gen.layers > 0,
        .has_encoder = m->vq.has_enc != 0, .follows_path = false, .internal = m,
    };
}

sm_model sm_load_model(const char *file_name) {
    char err[ERROR_SIZE] = "";
    sm_model_impl *m = NULL;
    sm_set_error("");
    if (!file_name) {
        sm_set_error("file_name is NULL");
        return wrap_model(NULL);
    }
    sm_status s = load_mapped(file_name, &m, err, sizeof err);
    if (s == SM_E_IO) s = load_read(file_name, &m, err, sizeof err);
    if (s != SM_OK) sm_set_error(err);
    return wrap_model(m);
}

sm_model sm_load_model_from_memory(const void *data, size_t size) {
    char err[ERROR_SIZE] = "";
    sm_model_impl *m = NULL;
    sm_set_error("");
    if (!data) {
        sm_set_error("data is NULL");
        return wrap_model(NULL);
    }
    if (sm_load(data, size, 0, &m, err, sizeof err) != SM_OK) sm_set_error(err);
    return wrap_model(m);
}

bool sm_is_model_valid(sm_model model) { return model.internal != NULL; }
void sm_unload_model(sm_model model) { sm_unload(model.internal); }

const char *sm_get_model_meta(sm_model model, const char *key) {
    return model.internal ? sm_meta(model.internal, key) : NULL;
}

const char *sm_get_model_word(sm_model model, int index) {
    const sm_model_impl *m = model.internal;
    return m && index >= 0 && (uint32_t)index < m->n_vocab ? m->vocab[index] : NULL;
}
