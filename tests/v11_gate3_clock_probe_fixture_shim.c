/* Test-only fixture observation functions for v11_gate3_clock_probe.
 *
 * Intercepts every syscall wrapper the probe calls (clock_gettime,
 * clock_getres, adjtimex, readlink, open for the boot_id path) and returns
 * fixed, environment-selected fixture values instead of the real host
 * state. The native suite links these functions directly into a test-only
 * target with ALPHA_V11_FIXTURE_ONLY. Unknown paths fail closed in that
 * build. The production probe never links this fixture code.
 *
 * Fixture selection: ALPHA_V11_CLOCK_FIXTURE selects a named scenario
 * ("OK", "MONO_FAIL", "ADJTIMEX_FAIL", "ADJTIMEX_ERROR_STATUS",
 * "BOOT_ID_MISMATCH", "NS_FAIL", "OVERLONG_BOOT_ID", "DIVERGED_CLOCKS").
 * Each scenario is a fixed, deterministic set of fake return values --
 * nothing here reads a real clock or device.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/timex.h>
#include <time.h>
#include <unistd.h>

#ifdef ALPHA_V11_FIXTURE_ONLY
/* Direct-link names used by the test-only probe. Unknown paths fail closed. */
#define clock_gettime fixture_clock_gettime
#define clock_getres fixture_clock_getres
#define adjtimex fixture_adjtimex
#define open fixture_open
#define readlink fixture_readlink
#endif

static const char *scenario(void) {
    const char *s = getenv("ALPHA_V11_CLOCK_FIXTURE");
    return s ? s : "OK";
}

static int streq(const char *a, const char *b) { return strcmp(a, b) == 0; }

/* Post-execution diagnostic only. Safety comes from direct fixture bindings
 * and executing the same sealed descriptor that was verified beforehand. */
__attribute__((constructor))
static void announce_shim_loaded(void) {
    static const char marker[] = "ALPHA_V11_CLOCK_FIXTURE_SHIM_LOADED\n";
    ssize_t ignored = write(STDERR_FILENO, marker, sizeof(marker) - 1);
    (void)ignored;
}

int clock_gettime(clockid_t id, struct timespec *tp) {
    const char *sc = scenario();
    if (streq(sc, "MONO_FAIL") && id == CLOCK_MONOTONIC) {
        errno = ENOSYS;
        return -1;
    }
    if (streq(sc, "RT_FAIL") && id == CLOCK_REALTIME) {
        errno = EIO;
        return -1;
    }
    /* MONOTONIC, MONOTONIC_RAW and BOOTTIME share a single incrementing call
     * sequence rather than independent per-clock counters: the real probe
     * reads them in a fixed nested order (monotonic before, raw before,
     * boottime before, ... boottime after, raw after, monotonic after), and
     * a correct fixture must keep raw/boottime values strictly inside the
     * surrounding monotonic bracket, exactly like genuine same-family
     * hardware-timer-backed clocks would. */
    static long shared_calls = 0;
    const long step_ns = 1000;
    switch (id) {
        case CLOCK_REALTIME:
            tp->tv_sec = 2000000000; /* fixed fixture epoch seconds */
            tp->tv_nsec = 123456000;
            return 0;
        case CLOCK_MONOTONIC:
        case CLOCK_MONOTONIC_RAW:
        case CLOCK_BOOTTIME: {
            long seq = shared_calls++;
            int64_t base_ns = (int64_t)1000 * 1000000000LL + step_ns * seq;
            if (streq(sc, "DIVERGED_CLOCKS")) {
                /* A genuine host: CLOCK_MONOTONIC_RAW legitimately drifts
                 * from CLOCK_MONOTONIC via NTP slew (here ~3.456ms, as if
                 * ~40ppm over one day of uptime) and CLOCK_BOOTTIME
                 * legitimately runs ahead by any suspended duration (here
                 * one simulated hour). Neither nests inside the other's
                 * bracket -- proving the F1 cross-clock fix end-to-end
                 * through the real native probe, not just a synthetic
                 * Python-only fixture. */
                if (id == CLOCK_MONOTONIC_RAW) base_ns -= 3456000;
                if (id == CLOCK_BOOTTIME) base_ns += 3600LL * 1000000000LL;
            }
            tp->tv_sec = base_ns / 1000000000LL;
            tp->tv_nsec = base_ns % 1000000000LL;
            return 0;
        }
        default:
            errno = EINVAL;
            return -1;
    }
}

int clock_getres(clockid_t id, struct timespec *res) {
    const char *sc = scenario();
    if ((streq(sc, "MONO_RES_FAIL") && id == CLOCK_MONOTONIC) ||
        (streq(sc, "RAW_RES_FAIL") && id == CLOCK_MONOTONIC_RAW) ||
        (streq(sc, "BOOT_RES_FAIL") && id == CLOCK_BOOTTIME) ||
        (streq(sc, "RT_RES_FAIL") && id == CLOCK_REALTIME)) {
        errno = ENOSYS;
        return -1;
    }
    res->tv_sec = 0;
    res->tv_nsec = 1;
    return 0;
}

int adjtimex(struct timex *buf) {
    const char *sc = scenario();
    if (streq(sc, "ADJTIMEX_FAIL")) {
        errno = EPERM;
        return -1;
    }
    memset(buf, 0, sizeof(*buf));
    buf->modes = 0;
    buf->offset = 12;
    buf->freq = 34;
    buf->maxerror = 56;
    buf->esterror = 78;
    buf->status = 0;
    buf->constant = 1;
    buf->precision = 1;
    buf->tolerance = 32768000;
    buf->time.tv_sec = 2000000000;
    buf->time.tv_usec = 123456;
    if (streq(sc, "ADJTIMEX_NANO")) {
        buf->status = STA_NANO;
        buf->time.tv_usec = 123456000;
    }
    buf->tick = 10000;
    if (streq(sc, "ADJTIMEX_ERROR_STATUS")) {
        return TIME_ERROR;
    }
    return TIME_OK;
}

ssize_t readlink(const char *path, char *buf, size_t bufsz) {
    const char *sc = scenario();
    if (streq(sc, "NS_FAIL") && strstr(path, "/ns/") != NULL) {
        errno = ENOENT;
        return -1;
    }
    const char *value = NULL;
    if (strstr(path, "/ns/time") != NULL) value = "time:[4026531834]";
    else if (strstr(path, "/ns/pid") != NULL) value = "pid:[4026531836]";
    if (value == NULL) {
#ifdef ALPHA_V11_FIXTURE_ONLY
        errno = EPERM;
        return -1;
#else
        static ssize_t (*real_readlink)(const char *, char *, size_t) = NULL;
        if (!real_readlink) {
            void *symbol = dlsym(RTLD_NEXT, "readlink");
            _Static_assert(sizeof(real_readlink) == sizeof(symbol), "dlsym ABI");
            memcpy(&real_readlink, &symbol, sizeof(symbol));
        }
        return real_readlink(path, buf, bufsz);
#endif
    }
    size_t n = strlen(value);
    if (n >= bufsz) { errno = ENAMETOOLONG; return -1; }
    memcpy(buf, value, n);
    return (ssize_t)n;
}

static int fake_boot_id_fd = -1;

int open(const char *path, int flags, ...) {
    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_list ap;
        va_start(ap, flags);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
    }
    if (streq(path, "/proc/sys/kernel/random/boot_id")) {
        const char *sc = scenario();
        if (streq(sc, "BOOT_ID_FAIL")) {
            errno = ENOENT;
            return -1;
        }
        const char *value = "11111111-1111-4111-8111-111111111111\n";
        if (streq(sc, "BOOT_ID_MISMATCH")) {
            static int call = 0;
            value = (call == 0) ? "11111111-1111-4111-8111-111111111111\n" : "22222222-2222-4222-8222-222222222222\n";
            call++;
        }
        if (streq(sc, "OVERLONG_BOOT_ID")) {
            static char big[4096];
            memset(big, 'A', sizeof(big) - 1);
            big[sizeof(big) - 1] = '\n';
            int fd = memfd_create("boot_id_fixture", 0);
            ssize_t n = write(fd, big, sizeof(big));
            (void)n;
            lseek(fd, 0, SEEK_SET);
            return fd;
        }
        int fd = memfd_create("boot_id_fixture", 0);
        ssize_t n = write(fd, value, strlen(value));
        (void)n;
        lseek(fd, 0, SEEK_SET);
        fake_boot_id_fd = fd;
        return fd;
    }
    #ifdef ALPHA_V11_FIXTURE_ONLY
    (void)mode;
    errno = EPERM;
    return -1;
    #else
    static int (*real_open)(const char *, int, ...) = NULL;
    if (!real_open) {
        void *symbol = dlsym(RTLD_NEXT, "open");
        _Static_assert(sizeof(real_open) == sizeof(symbol), "dlsym ABI");
        memcpy(&real_open, &symbol, sizeof(symbol));
    }
    return real_open(path, flags, mode);
    #endif
}
