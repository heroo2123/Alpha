/* Harmless preload preflight: resolve symbols only; never call observations. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    static const char *names[] = {
        "clock_gettime", "clock_getres", "adjtimex", "open", "readlink"
    };
    if (argc != 2) return 2;
    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        void *symbol = dlsym(RTLD_DEFAULT, names[i]);
        Dl_info info;
        if (!symbol || !dladdr(symbol, &info) || !info.dli_fname ||
            strcmp(info.dli_fname, argv[1]) != 0) return 3;
    }
    puts("VERIFIED_ALL_FIVE_SHIM_BINDINGS");
    return 0;
}
