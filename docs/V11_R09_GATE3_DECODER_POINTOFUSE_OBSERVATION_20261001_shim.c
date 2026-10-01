/* Gate 3 decoder point-of-use trace shim (LD_PRELOAD interposition).
 *
 * Observer of codes_memfs_open/codes_memfs_exists only: it never
 * calls find()/fmemopen() itself, never mutates the real return value, and
 * restores the stream position before handing the FILE* back to the real
 * caller. The captured bytes, restored position and reported decoder outputs
 * are checked; general equivalence to an uninstrumented decode is unproved.
 * Trace and dump write failures abort loudly.
 *
 * Required environment (set by the harness, not defaulted here):
 *   ALPHA_V11_MEMFS_LIBRARY_PATH - absolute path to the exact pinned
 *     libeccodes_memfs.so already mapped into this process (a dependency of
 *     libeccodes.so). Used with dlopen(..., RTLD_NOLOAD) to re-resolve a
 *     handle to the *already-loaded* mapping -- this never loads or
 *     re-initializes the library. RTLD_NEXT is deliberately not used;
 *     the exact already-mapped library handle bounds symbol resolution
 *     to the observed installed binary.
 *   ALPHA_V11_MEMFS_TRACE_PATH - file to append one JSON line per call to.
 *   ALPHA_V11_MEMFS_DUMP_DIR - existing directory to write
 *     call_<n>.bin (the exact bytes codes_memfs_open returned) into, for
 *     independent sha256 computation by the Python harness. This shim
 *     performs no hashing itself.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef FILE *(*memfs_open_fn)(const char *);
typedef int (*memfs_exists_fn)(const char *);

static memfs_open_fn real_open = NULL;
static memfs_exists_fn real_exists = NULL;
static FILE *trace_fp = NULL;
static long call_index = 0;
static char dump_dir[4096];
static int init_done = 0;
static int trace_complete = 0;

static void fatal(const char *msg) {
    fprintf(stderr, "[shim] FATAL: %s\n", msg);
    fflush(stderr);
    abort();
}

static void ensure_init(void) {
    if (trace_complete) fatal("MEMFS call after trace completion");
    if (init_done) return;
    const char *libpath = getenv("ALPHA_V11_MEMFS_LIBRARY_PATH");
    if (!libpath) fatal("ALPHA_V11_MEMFS_LIBRARY_PATH not set");
    void *handle = dlopen(libpath, RTLD_NOLOAD | RTLD_LAZY);
    if (!handle) fatal("RTLD_NOLOAD dlopen of ALPHA_V11_MEMFS_LIBRARY_PATH failed");
    real_open = (memfs_open_fn)dlsym(handle, "codes_memfs_open");
    real_exists = (memfs_exists_fn)dlsym(handle, "codes_memfs_exists");
    if (!real_open || !real_exists) fatal("dlsym of real memfs symbols failed");

    const char *tp = getenv("ALPHA_V11_MEMFS_TRACE_PATH");
    if (!tp) fatal("ALPHA_V11_MEMFS_TRACE_PATH not set");
    trace_fp = fopen(tp, "a");
    if (!trace_fp) fatal("failed to open ALPHA_V11_MEMFS_TRACE_PATH for append");

    const char *dd = getenv("ALPHA_V11_MEMFS_DUMP_DIR");
    if (!dd) fatal("ALPHA_V11_MEMFS_DUMP_DIR not set");
    if (strlen(dd) >= sizeof(dump_dir)) fatal("ALPHA_V11_MEMFS_DUMP_DIR too long");
    strcpy(dump_dir, dd);

    init_done = 1;
}

static void json_escape(const char *s, char *out, size_t outsz) {
    size_t j = 0;
    if (!s) { out[0] = 0; return; }
    for (size_t i = 0; s[i]; i++) {
        if ((unsigned char)s[i] < 0x20) fatal("control character in MEMFS path");
        if (j + 3 > outsz) fatal("MEMFS path exceeds trace field");
        if (s[i] == '"' || s[i] == '\\') out[j++] = '\\';
        out[j++] = s[i];
    }
    out[j] = 0;
}

static void check_trace_write(int printed) {
    if (printed < 0 || fflush(trace_fp) != 0) fatal("trace write or flush failed");
}

FILE *codes_memfs_open(const char *path) {
    ensure_init();
    FILE *fp = real_open(path);
    long idx = __sync_add_and_fetch(&call_index, 1);
    char esc[1024];
    json_escape(path, esc, sizeof(esc));
    if (!fp) {
        int printed = fprintf(trace_fp,
            "{\"call_index\":%ld,\"function\":\"codes_memfs_open\",\"path\":\"%s\",\"result\":null}\n",
            idx, esc);
        check_trace_write(printed);
        return fp;
    }

    long saved = ftell(fp);
    if (saved != 0) fatal("codes_memfs_open returned a stream not at position 0");
    if (fseek(fp, 0, SEEK_END) != 0) fatal("fseek(SEEK_END) on real memfs stream failed");
    long size = ftell(fp);
    if (size < 0) fatal("ftell on real memfs stream failed");
    if (fseek(fp, 0, SEEK_SET) != 0) fatal("fseek(SEEK_SET) on real memfs stream failed");

    char *buf = NULL;
    size_t nread = 0;
    if (size > 0) {
        buf = malloc((size_t)size);
        if (!buf) fatal("malloc for passive read-back failed");
        nread = fread(buf, 1, (size_t)size, fp);
        if (nread != (size_t)size) fatal("short read while passively copying real memfs stream");
    }
    if (fseek(fp, saved, SEEK_SET) != 0) fatal("failed to restore stream position for caller");

    char dumpfile[4352];
    if (snprintf(dumpfile, sizeof(dumpfile), "%s/call_%ld.bin", dump_dir, idx)
        >= (int)sizeof(dumpfile)) fatal("dump file path too long");
    FILE *d = fopen(dumpfile, "wb");
    if (!d) fatal("failed to open dump file for writing");
    if (size > 0 && fwrite(buf, 1, nread, d) != nread) fatal("short write to dump file");
    if (fclose(d) != 0) fatal("failed to close dump file");
    if (buf) free(buf);

    int printed = fprintf(trace_fp,
        "{\"call_index\":%ld,\"function\":\"codes_memfs_open\",\"path\":\"%s\",\"size\":%ld,\"dump_file\":\"%s\"}\n",
        idx, esc, size, dumpfile);
    check_trace_write(printed);
    return fp;
}

int codes_memfs_exists(const char *path) {
    ensure_init();
    int r = real_exists(path);
    long idx = __sync_add_and_fetch(&call_index, 1);
    char esc[1024];
    json_escape(path, esc, sizeof(esc));
    int printed = fprintf(trace_fp,
        "{\"call_index\":%ld,\"function\":\"codes_memfs_exists\",\"path\":\"%s\",\"result\":%d}\n",
        idx, esc, r);
    check_trace_write(printed);
    return r;
}

/* Called by the capture child only after its bounded decode returns. */
long alpha_v11_memfs_trace_complete(void) {
    ensure_init();
    long count = call_index;
    int printed = fprintf(trace_fp,
        "{\"record_type\":\"completion\",\"call_count\":%ld}\n", count);
    check_trace_write(printed);
    if (fclose(trace_fp) != 0) fatal("trace close failed");
    trace_fp = NULL;
    trace_complete = 1;
    return count;
}
