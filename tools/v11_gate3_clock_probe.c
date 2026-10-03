/* Gate 3 passive Linux clock probe: LINUX_PASSIVE_CLOCK_V1 (one-shot).
 *
 * Unprivileged, standalone, native. Fixed compile-time read/query allowlist:
 *   - clock_gettime/clock_getres for CLOCK_REALTIME, CLOCK_MONOTONIC,
 *     CLOCK_MONOTONIC_RAW and CLOCK_BOOTTIME.
 *   - read-only adjtimex() with a zero-initialized struct timex and
 *     modes=0 hardcoded (no ADJ_* operation, no clock_settime).
 *   - readlink() of /proc/self/ns/time and /proc/self/ns/pid.
 *   - open/read/close of /proc/sys/kernel/random/boot_id.
 * Nothing else: no socket, subprocess, exec, shell, config read, environment
 * read or privilege escalation. This program makes no claim about UTC
 * accuracy, synchronization or host identity; it only serializes exactly
 * what the above calls returned, bracketed around one observation, as
 * bounded JSON on stdout. It takes exactly one observation per invocation
 * and never retries or loops waiting for a favorable sample.
 *
 * Exit status 0 means a complete "OK" record was printed; exit status 1
 * means a bounded "REFUSED" record (with whatever partial raw fields were
 * already collected) was printed. Any other exit status means even the
 * bounded refusal path failed (e.g. output would exceed the 16 KiB cap).
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/timex.h>
#include <time.h>
#include <unistd.h>

/* Test-only target: link fixed observation functions into the executable. */
#ifdef ALPHA_V11_FIXTURE_ONLY
extern int fixture_clock_gettime(clockid_t, struct timespec *);
extern int fixture_clock_getres(clockid_t, struct timespec *);
extern int fixture_adjtimex(struct timex *);
extern int fixture_open(const char *, int, ...);
extern ssize_t fixture_readlink(const char *, char *, size_t);
#define clock_gettime fixture_clock_gettime
#define clock_getres fixture_clock_getres
#define adjtimex fixture_adjtimex
#define open fixture_open
#define readlink fixture_readlink
#endif

#define MAX_OUTPUT 16384
#define IDENT_BUF 128

static char out_buf[MAX_OUTPUT + 1];
static size_t out_len = 0;

typedef struct {
    int64_t before_ns, after_ns;
    int before_result, after_result;
    int before_errno, after_errno;
    int before_attempted, after_attempted;
    long res_sec, res_nsec;
    int res_result, res_errno;
    int res_attempted;
    int have;
} bracket_t;

typedef struct {
    int64_t value_ns;
    int result, err;
    int attempted;
    long res_sec, res_nsec;
    int res_result, res_errno;
    int res_attempted;
    int have;
} realtime_t;

typedef struct {
    struct timex tx;
    int call_result;
    int err;
    int have;
} adjtimex_t;

static char boot_id_before[IDENT_BUF];
static char boot_id_after[IDENT_BUF];
static char ns_time_buf[IDENT_BUF];
static char ns_pid_buf[IDENT_BUF];
static int have_boot_before = 0, have_boot_after = 0, have_ns_time = 0, have_ns_pid = 0;

static bracket_t b_monotonic, b_raw, b_boottime;
static realtime_t r_realtime;
static adjtimex_t a_adjtimex;

static void out_putc(char c) {
    if (out_len < MAX_OUTPUT) out_buf[out_len++] = c;
    else out_len = MAX_OUTPUT + 1; /* mark overflow */
}

static void out_str(const char *s) {
    for (; *s; s++) out_putc(*s);
}

static void out_json_escape(const char *s, size_t n) {
    out_putc('"');
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c == '"' || c == '\\') { out_putc('\\'); out_putc((char)c); }
        else if (c < 0x20) {
            char tmp[8];
            snprintf(tmp, sizeof(tmp), "\\u%04x", c);
            out_str(tmp);
        } else {
            out_putc((char)c);
        }
    }
    out_putc('"');
}

static void out_i64(int64_t v) {
    char tmp[32];
    snprintf(tmp, sizeof(tmp), "%lld", (long long)v);
    out_str(tmp);
}

static void out_long(long v) {
    char tmp[32];
    snprintf(tmp, sizeof(tmp), "%ld", v);
    out_str(tmp);
}

/* Boot UUID at /proc/sys/kernel/random/boot_id: no machine-ID or arbitrary
 * /proc scan, bounded read, trailing newline stripped. */
static int read_boot_id(char *dst, size_t dstsz) {
    int fd = open("/proc/sys/kernel/random/boot_id", O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return -1;
    ssize_t n = read(fd, dst, dstsz - 1);
    int saved_errno = errno;
    close(fd);
    if (n < 0) { errno = saved_errno; return -1; }
    /* A read that exactly fills the buffer cannot be distinguished from one
     * that was silently truncated; refuse rather than report a possibly-cut
     * identity as if it were complete (matches read_ns_link's same check). */
    if ((size_t)n == dstsz - 1) { errno = ENAMETOOLONG; return -1; }
    while (n > 0 && (dst[n - 1] == '\n' || dst[n - 1] == '\r')) n--;
    dst[n] = 0;
    if (n == 0) { errno = ENODATA; return -1; }
    return 0;
}

static int read_ns_link(const char *path, char *dst, size_t dstsz) {
    ssize_t n = readlink(path, dst, dstsz - 1);
    if (n < 0) return -1;
    if ((size_t)n >= dstsz - 1) { errno = ENAMETOOLONG; return -1; }
    dst[n] = 0;
    return 0;
}

/* Convert a timespec to a signed-64 nanosecond count without ever reading an
 * uninitialized timespec or invoking undefined-behavior signed overflow on
 * the multiply/add (both were previously reachable: an invalid tv_nsec from
 * a malfunctioning clock source, or a tv_sec large enough to overflow once
 * scaled by 1e9). Returns 0 and sets `*out_ns` on success, -1 with errno set
 * otherwise. */
static int ts_to_ns(const struct timespec *ts, int64_t *out_ns) {
    if (ts->tv_nsec < 0 || ts->tv_nsec >= 1000000000L) { errno = ERANGE; return -1; }
    /* __builtin_*_overflow never themselves invoke the undefined behavior
     * they're checking for, unlike a hand-written bounds pre-check whose own
     * arithmetic (e.g. `INT64_MIN - tv_nsec`) can overflow first. */
    int64_t scaled;
    if (__builtin_mul_overflow((int64_t)ts->tv_sec, (int64_t)1000000000LL, &scaled)) {
        errno = EOVERFLOW; return -1;
    }
    if (__builtin_add_overflow(scaled, (int64_t)ts->tv_nsec, out_ns)) {
        errno = EOVERFLOW; return -1;
    }
    return 0;
}

static int read_clock(clockid_t id, bracket_t *out, int which /* 0=before,1=after */) {
    struct timespec ts;
    int r = clock_gettime(id, &ts);
    int saved_errno = errno;
    int64_t ns = 0;
    if (r == 0 && ts_to_ns(&ts, &ns) != 0) { r = -1; saved_errno = errno; }
    if (which == 0) {
        out->before_attempted = 1;
        out->before_result = r;
        out->before_errno = (r == 0) ? 0 : saved_errno;
        out->before_ns = (r == 0) ? ns : 0;
    } else {
        out->after_attempted = 1;
        out->after_result = r;
        out->after_errno = (r == 0) ? 0 : saved_errno;
        out->after_ns = (r == 0) ? ns : 0;
    }
    return r;
}

static int read_clock_res(clockid_t id, bracket_t *out) {
    struct timespec ts;
    int r = clock_getres(id, &ts);
    int saved_errno = errno;
    out->res_attempted = 1;
    out->have = 1;
    out->res_result = r;
    out->res_errno = (r == 0) ? 0 : saved_errno;
    out->res_sec = (r == 0) ? (long)ts.tv_sec : 0;
    out->res_nsec = (r == 0) ? (long)ts.tv_nsec : 0;
    return r;
}

static void emit_bracket(const char *name, const bracket_t *v) {
    char tmp[256];
    snprintf(tmp, sizeof(tmp),
        "\"%s\":{\"before_ns\":", name);
    out_str(tmp);
    out_i64(v->before_ns);
    out_str(",\"before_result\":"); out_i64(v->before_result);
    out_str(",\"before_errno\":"); out_i64(v->before_errno);
    out_str(",\"before_attempted\":"); out_i64(v->before_attempted);
    out_str(",\"after_ns\":"); out_i64(v->after_ns);
    out_str(",\"after_result\":"); out_i64(v->after_result);
    out_str(",\"after_errno\":"); out_i64(v->after_errno);
    out_str(",\"after_attempted\":"); out_i64(v->after_attempted);
    out_str(",\"res_sec\":"); out_long(v->res_sec);
    out_str(",\"res_nsec\":"); out_long(v->res_nsec);
    out_str(",\"res_result\":"); out_i64(v->res_result);
    out_str(",\"res_errno\":"); out_i64(v->res_errno);
    out_str(",\"res_attempted\":"); out_i64(v->res_attempted);
    out_str("}");
}

static void emit_adjtimex_fields(const struct timex *tx) {
    out_str("\"modes\":"); out_i64(tx->modes);
    out_str(",\"offset\":"); out_long(tx->offset);
    out_str(",\"freq\":"); out_long(tx->freq);
    out_str(",\"maxerror\":"); out_long(tx->maxerror);
    out_str(",\"esterror\":"); out_long(tx->esterror);
    out_str(",\"status\":"); out_long(tx->status);
    out_str(",\"constant\":"); out_long(tx->constant);
    out_str(",\"precision\":"); out_long(tx->precision);
    out_str(",\"tolerance\":"); out_long(tx->tolerance);
    out_str(",\"time_sec\":"); out_i64((int64_t)tx->time.tv_sec);
    out_str(",\"time_usec\":"); out_i64((int64_t)tx->time.tv_usec);
    out_str(",\"tick\":"); out_long(tx->tick);
    out_str(",\"ppsfreq\":"); out_long(tx->ppsfreq);
    out_str(",\"jitter\":"); out_long(tx->jitter);
    out_str(",\"shift\":"); out_long(tx->shift);
    out_str(",\"stabil\":"); out_long(tx->stabil);
    out_str(",\"jitcnt\":"); out_long(tx->jitcnt);
    out_str(",\"calcnt\":"); out_long(tx->calcnt);
    out_str(",\"errcnt\":"); out_long(tx->errcnt);
    out_str(",\"stbcnt\":"); out_long(tx->stbcnt);
    out_str(",\"tai\":"); out_long(tx->tai);
}

static void emit_refused(const char *code, int detail_errno) {
    out_len = 0;
    out_str("{\"schema\":\"ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1\",\"status\":\"REFUSED\",\"code\":");
    out_json_escape(code, strlen(code));
    out_str(",\"detail_errno\":"); out_i64(detail_errno);
    out_str(",\"partial\":{");
    int first = 1;
#define SEP() do { if (!first) out_str(","); first = 0; } while (0)
    if (have_boot_before) { SEP(); out_str("\"boot_id_before\":"); out_json_escape(boot_id_before, strlen(boot_id_before)); }
    if (have_boot_after) { SEP(); out_str("\"boot_id_after\":"); out_json_escape(boot_id_after, strlen(boot_id_after)); }
    if (have_ns_time) { SEP(); out_str("\"ns_time\":"); out_json_escape(ns_time_buf, strlen(ns_time_buf)); }
    if (have_ns_pid) { SEP(); out_str("\"ns_pid\":"); out_json_escape(ns_pid_buf, strlen(ns_pid_buf)); }
    if (b_monotonic.have) { SEP(); emit_bracket("monotonic", &b_monotonic); }
    if (b_raw.have) { SEP(); emit_bracket("monotonic_raw", &b_raw); }
    if (b_boottime.have) { SEP(); emit_bracket("boottime", &b_boottime); }
    /* Previously omitted entirely: any realtime/adjtimex data already
     * collected before the failure that triggered this refusal must still
     * be retained, not silently dropped (F8). */
    if (r_realtime.have) {
        SEP(); out_str("\"realtime\":{\"value_ns\":"); out_i64(r_realtime.value_ns);
        out_str(",\"result\":"); out_i64(r_realtime.result);
        out_str(",\"errno\":"); out_i64(r_realtime.err);
        out_str(",\"attempted\":"); out_i64(r_realtime.attempted);
        out_str(",\"res_sec\":"); out_long(r_realtime.res_sec);
        out_str(",\"res_nsec\":"); out_long(r_realtime.res_nsec);
        out_str(",\"res_result\":"); out_i64(r_realtime.res_result);
        out_str(",\"res_errno\":"); out_i64(r_realtime.res_errno);
        out_str(",\"res_attempted\":"); out_i64(r_realtime.res_attempted);
        out_str("}");
    }
    if (a_adjtimex.have) {
        SEP(); out_str("\"adjtimex\":{\"call_result\":"); out_i64(a_adjtimex.call_result);
        out_str(",\"errno\":"); out_i64(a_adjtimex.err);
        out_str(",");
        emit_adjtimex_fields(&a_adjtimex.tx);
        out_str("}");
    }
#undef SEP
    out_str("}}");
    out_putc('\n');
    if (out_len > MAX_OUTPUT) {
        /* Even the bounded refusal overflowed (checked only after appending
         * the trailing newline, so the newline itself can never be the
         * unaccounted byte that silently pushes a would-be-exact-fit record
         * over the cap): emit nothing beyond this fixed minimal record and
         * signal a distinct exit status. */
        static const char fallback[] =
            "{\"schema\":\"ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1\",\"status\":\"REFUSED\","
            "\"code\":\"OUTPUT_BOUNDS\",\"detail_errno\":0,\"partial\":{}}\n";
        ssize_t ignored = write(STDOUT_FILENO, fallback, strlen(fallback));
        (void)ignored;
        return;
    }
    ssize_t ignored = write(STDOUT_FILENO, out_buf, out_len);
    (void)ignored;
}

int main(void) {
    /* Mark the "after" half of every bracket as not-yet-attempted (any
     * nonzero result is already treated as unavailable by the parser): a
     * refusal emitted after the "before" half succeeds but before the
     * "after" half is ever read must not fabricate an after_result of 0,
     * which would read as a successful close of a read that never ran. */
    b_monotonic.before_result = b_raw.before_result = b_boottime.before_result = -1;
    b_monotonic.after_result = b_raw.after_result = b_boottime.after_result = -1;
    r_realtime.result = -1;

    /* 1. Boot identity before. Unavailable identity reads refuse the method. */
    if (read_boot_id(boot_id_before, sizeof(boot_id_before)) != 0) {
        emit_refused("CLOCK_SOURCE_UNAVAILABLE", errno);
        return 1;
    }
    have_boot_before = 1;

    if (read_ns_link("/proc/self/ns/time", ns_time_buf, sizeof(ns_time_buf)) != 0) {
        emit_refused("CLOCK_SOURCE_UNAVAILABLE", errno);
        return 1;
    }
    have_ns_time = 1;

    if (read_ns_link("/proc/self/ns/pid", ns_pid_buf, sizeof(ns_pid_buf)) != 0) {
        emit_refused("CLOCK_SOURCE_UNAVAILABLE", errno);
        return 1;
    }
    have_ns_pid = 1;

    /* 2. Resolutions (static; no ordering requirement against the bracket). */
    if (read_clock_res(CLOCK_MONOTONIC, &b_monotonic) != 0) {
        emit_refused("CLOCK_SOURCE_UNAVAILABLE", b_monotonic.res_errno); return 1;
    }
    if (read_clock_res(CLOCK_MONOTONIC_RAW, &b_raw) != 0) {
        emit_refused("CLOCK_SOURCE_UNAVAILABLE", b_raw.res_errno); return 1;
    }
    if (read_clock_res(CLOCK_BOOTTIME, &b_boottime) != 0) {
        emit_refused("CLOCK_SOURCE_UNAVAILABLE", b_boottime.res_errno); return 1;
    }
    {
        struct timespec ts;
        int result = clock_getres(CLOCK_REALTIME, &ts);
        int saved_errno = errno;
        r_realtime.have = 1;
        r_realtime.res_attempted = 1;
        r_realtime.res_result = result;
        r_realtime.res_errno = result == 0 ? 0 : saved_errno;
        if (result != 0) {
            emit_refused("CLOCK_SOURCE_UNAVAILABLE", r_realtime.res_errno);
            return 1;
        }
        r_realtime.res_sec = (long)ts.tv_sec;
        r_realtime.res_nsec = (long)ts.tv_nsec;
    }

    /* 3. Ordered monotonic/raw/boottime reads bracketing the query and the
     * realtime read, opening and closing symmetrically. */
    if (read_clock(CLOCK_MONOTONIC, &b_monotonic, 0) != 0) { b_monotonic.have = 1; emit_refused("CLOCK_SOURCE_UNAVAILABLE", b_monotonic.before_errno); return 1; }
    b_monotonic.have = 1;
    if (read_clock(CLOCK_MONOTONIC_RAW, &b_raw, 0) != 0) { b_raw.have = 1; emit_refused("CLOCK_SOURCE_UNAVAILABLE", b_raw.before_errno); return 1; }
    b_raw.have = 1;
    if (read_clock(CLOCK_BOOTTIME, &b_boottime, 0) != 0) { b_boottime.have = 1; emit_refused("CLOCK_SOURCE_UNAVAILABLE", b_boottime.before_errno); return 1; }
    b_boottime.have = 1;

    /* 4. Read-only kernel discipline query: zero-initialized struct timex,
     * modes=0 hardcoded. No ADJ_* operation is ever requested. */
    memset(&a_adjtimex.tx, 0, sizeof(a_adjtimex.tx));
    a_adjtimex.tx.modes = 0;
    a_adjtimex.call_result = adjtimex(&a_adjtimex.tx);
    a_adjtimex.err = (a_adjtimex.call_result < 0) ? errno : 0;
    a_adjtimex.have = 1;
    if (a_adjtimex.call_result < 0) {
        emit_refused("CLOCK_SOURCE_UNAVAILABLE", a_adjtimex.err);
        return 1;
    }

    /* 5. Realtime read inside the bracket. */
    {
        struct timespec ts;
        int r = clock_gettime(CLOCK_REALTIME, &ts);
        int saved_errno = errno;
        int64_t ns = 0;
        if (r == 0 && ts_to_ns(&ts, &ns) != 0) { r = -1; saved_errno = errno; }
        r_realtime.result = r;
        r_realtime.attempted = 1;
        r_realtime.err = (r == 0) ? 0 : saved_errno;
        r_realtime.value_ns = (r == 0) ? ns : 0;
        r_realtime.have = 1;
        if (r != 0) {
            emit_refused("CLOCK_SOURCE_UNAVAILABLE", r_realtime.err);
            return 1;
        }
    }

    /* 6. Close the bracket in reverse order (boottime, raw, monotonic). */
    if (read_clock(CLOCK_BOOTTIME, &b_boottime, 1) != 0) { emit_refused("CLOCK_SOURCE_UNAVAILABLE", b_boottime.after_errno); return 1; }
    if (read_clock(CLOCK_MONOTONIC_RAW, &b_raw, 1) != 0) { emit_refused("CLOCK_SOURCE_UNAVAILABLE", b_raw.after_errno); return 1; }
    if (read_clock(CLOCK_MONOTONIC, &b_monotonic, 1) != 0) { emit_refused("CLOCK_SOURCE_UNAVAILABLE", b_monotonic.after_errno); return 1; }

    /* 7. Boot identity after. */
    if (read_boot_id(boot_id_after, sizeof(boot_id_after)) != 0) {
        emit_refused("CLOCK_SOURCE_UNAVAILABLE", errno);
        return 1;
    }
    have_boot_after = 1;

    /* 8. Emit the complete bounded "OK" record. */
    out_len = 0;
    out_str("{\"schema\":\"ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1\",\"status\":\"OK\",");
    out_str("\"boot_id_before\":"); out_json_escape(boot_id_before, strlen(boot_id_before));
    out_str(",\"boot_id_after\":"); out_json_escape(boot_id_after, strlen(boot_id_after));
    out_str(",\"ns_time\":"); out_json_escape(ns_time_buf, strlen(ns_time_buf));
    out_str(",\"ns_pid\":"); out_json_escape(ns_pid_buf, strlen(ns_pid_buf));
    out_str(","); emit_bracket("monotonic", &b_monotonic);
    out_str(","); emit_bracket("monotonic_raw", &b_raw);
    out_str(","); emit_bracket("boottime", &b_boottime);
    out_str(",\"realtime\":{\"value_ns\":"); out_i64(r_realtime.value_ns);
    out_str(",\"result\":"); out_i64(r_realtime.result);
    out_str(",\"errno\":"); out_i64(r_realtime.err);
    out_str(",\"attempted\":"); out_i64(r_realtime.attempted);
    out_str(",\"res_sec\":"); out_long(r_realtime.res_sec);
    out_str(",\"res_nsec\":"); out_long(r_realtime.res_nsec);
    out_str(",\"res_result\":"); out_i64(r_realtime.res_result);
    out_str(",\"res_errno\":"); out_i64(r_realtime.res_errno);
    out_str(",\"res_attempted\":"); out_i64(r_realtime.res_attempted);
    out_str("}");
    out_str(",\"adjtimex\":{\"call_result\":"); out_i64(a_adjtimex.call_result);
    out_str(",\"errno\":"); out_i64(a_adjtimex.err);
    out_str(",");
    emit_adjtimex_fields(&a_adjtimex.tx);
    out_str("}}");
    out_putc('\n');

    if (out_len > MAX_OUTPUT) {
        /* Checked only after appending the trailing newline (see
         * emit_refused for why), and labeled for what actually happened --
         * the record did not fit, not that a clock source was unavailable. */
        emit_refused("OUTPUT_BOUNDS", 0);
        return 1;
    }
    ssize_t written = write(STDOUT_FILENO, out_buf, out_len);
    if (written < 0 || (size_t)written != out_len) return 2;
    return 0;
}
