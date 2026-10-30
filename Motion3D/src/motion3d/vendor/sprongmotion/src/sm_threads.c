/* The built-in thread pool. Tasks write disjoint outputs, so which thread runs which does not change the result. */
#define _DEFAULT_SOURCE   /* sysconf on glibc with -std=c11 */
#include "sm_internal.h"

void sm_par_for(const sm_par *p, uint32_t n, sm_job_fn fn, void *arg) {
    if (p && p->run && n > 1) {
        p->run(p->user, n, fn, arg);
        return;
    }
    for (uint32_t i = 0; i < n; i++) fn(arg, i);
}

#if defined(__wasm__)
void *sm_pool_create(const sm_hooks *h, uint32_t threads) { (void)h; (void)threads; return NULL; }
void  sm_pool_run(void *pool, uint32_t n, sm_job_fn fn, void *arg) { (void)pool; sm_par_for(NULL, n, fn, arg); }
void  sm_pool_free(const sm_hooks *h, void *pool) { (void)h; (void)pool; }
#else
#include <stdatomic.h>
#ifdef _WIN32
#include <windows.h>
typedef SRWLOCK lock_t;
typedef CONDITION_VARIABLE cond_t;
typedef HANDLE thread_t;
static void lock_init(lock_t *l, cond_t *c) { InitializeSRWLock(l); InitializeConditionVariable(c); }
static void lock_done(lock_t *l, cond_t *c) { (void)l; (void)c; }
static void lock(lock_t *l) { AcquireSRWLockExclusive(l); }
static void unlock(lock_t *l) { ReleaseSRWLockExclusive(l); }
static void sleep_on(cond_t *c, lock_t *l) { SleepConditionVariableSRW(c, l, INFINITE, 0); }
static void wake_all(cond_t *c) { WakeAllConditionVariable(c); }
static uint32_t cores(void) { SYSTEM_INFO s; GetSystemInfo(&s); return s.dwNumberOfProcessors; }
static DWORD WINAPI entry(LPVOID p);
static int spawn(thread_t *t, void *p) { return (*t = CreateThread(NULL, 0, entry, p, 0, NULL)) != NULL; }
static void join(thread_t t) { WaitForSingleObject(t, INFINITE); CloseHandle(t); }
static void yield(void) { SwitchToThread(); }
#else
#include <pthread.h>
#include <unistd.h>
#include <sched.h>
typedef pthread_mutex_t lock_t;
typedef pthread_cond_t cond_t;
typedef pthread_t thread_t;
static void lock_init(lock_t *l, cond_t *c) { pthread_mutex_init(l, NULL); pthread_cond_init(c, NULL); }
static void lock_done(lock_t *l, cond_t *c) { pthread_cond_destroy(c); pthread_mutex_destroy(l); }
static void lock(lock_t *l) { pthread_mutex_lock(l); }
static void unlock(lock_t *l) { pthread_mutex_unlock(l); }
static void sleep_on(cond_t *c, lock_t *l) { pthread_cond_wait(c, l); }
static void wake_all(cond_t *c) { pthread_cond_broadcast(c); }
static uint32_t cores(void) { const long c = sysconf(_SC_NPROCESSORS_ONLN); return c > 0 ? (uint32_t)c : 1; }
static void *entry(void *p);
static int spawn(thread_t *t, void *p) { return !pthread_create(t, NULL, entry, p); }
static void join(thread_t t) { pthread_join(t, NULL); }
static void yield(void) { sched_yield(); }
#endif

#define SPIN   1000   /* polls, then yields (~1 ms in all) before an idle worker sleeps: the serial work between ops */
#define YIELDS 1000   /* a yield lets the host's threads run in the gaps, so they preempt no worker during an op */
#define MAX_THREADS 64

/* claim = n << 32 | the next task. A worker late from an earlier run claims on this run's word, so it takes nothing
   or a task of this one, and the caller waits for the tasks, not for the workers */
typedef struct {
    lock_t lock;
    cond_t wake;
    atomic_int busy, quit, sleepers;
    atomic_uint generation, done;
    _Atomic uint64_t claim;
    sm_job_fn fn;
    void *arg;
    uint32_t n_workers;
    thread_t threads[MAX_THREADS];
} pool;

static void run_tasks(pool *p) {
    for (;;) {
        const uint64_t claim = atomic_fetch_add(&p->claim, 1);
        const uint32_t task = (uint32_t)claim;
        if (task >= claim >> 32) return;
        p->fn(p->arg, task);
        atomic_fetch_add(&p->done, 1);
    }
}

static unsigned wait_for_run(pool *p, unsigned seen) {
    unsigned generation = atomic_load(&p->generation);
    for (int spins = 0; generation == seen && spins < SPIN + YIELDS; spins++) {
        if (spins >= SPIN) yield();
        generation = atomic_load(&p->generation);
    }
    if (generation != seen) return generation;

    lock(&p->lock);
    atomic_fetch_add(&p->sleepers, 1);
    while ((generation = atomic_load(&p->generation)) == seen) sleep_on(&p->wake, &p->lock);
    atomic_fetch_sub(&p->sleepers, 1);
    unlock(&p->lock);
    return generation;
}

static void worker(pool *p) {
    unsigned seen = 0;
    for (;;) {
        seen = wait_for_run(p, seen);
        if (atomic_load(&p->quit)) return;
        run_tasks(p);
    }
}
#ifdef _WIN32
static DWORD WINAPI entry(LPVOID p) { worker(p); return 0; }
#else
static void *entry(void *p) { worker(p); return NULL; }
#endif

/* a sleeper counts itself before it reads the generation, bumped here before sleepers is read: one sees the other */
static void wake_workers(pool *p) {
    atomic_fetch_add(&p->generation, 1);
    if (!atomic_load(&p->sleepers)) return;
    lock(&p->lock);
    wake_all(&p->wake);
    unlock(&p->lock);
}

/* a caller arriving while another run is in progress runs its own tasks */
void sm_pool_run(void *handle, uint32_t n, sm_job_fn fn, void *arg) {
    pool *p = handle;
    if (n < 2 || atomic_exchange(&p->busy, 1)) {
        sm_par_for(NULL, n, fn, arg);
        return;
    }

    p->fn = fn;
    p->arg = arg;
    atomic_store(&p->done, 0);
    atomic_store(&p->claim, (uint64_t)n << 32);
    wake_workers(p);

    run_tasks(p);
    while (atomic_load(&p->done) < n) {}
    atomic_store(&p->busy, 0);
}

void *sm_pool_create(const sm_hooks *h, uint32_t threads) {
    if (!threads) threads = cores();
    if (threads > MAX_THREADS) threads = MAX_THREADS;
    if (threads < 2) return NULL;

    pool *p = sm_alloc(h, sizeof *p);
    if (!p) return NULL;
    memset(p, 0, sizeof *p);
    lock_init(&p->lock, &p->wake);
    while (p->n_workers < threads - 1 && spawn(&p->threads[p->n_workers], p)) p->n_workers++;
    return p;
}

void sm_pool_free(const sm_hooks *h, void *handle) {
    pool *p = handle;
    if (!p) return;
    atomic_store(&p->quit, 1);
    wake_workers(p);
    for (uint32_t i = 0; i < p->n_workers; i++) join(p->threads[i]);
    lock_done(&p->lock, &p->wake);
    sm_dealloc(h, p);
}
#endif
