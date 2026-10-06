/* SPDX-License-Identifier: GPL-3.0-only
 * Native Linux ARM64 entry point for FrameDrop's executable detection.
 * Python reads the ZIP appended to this ELF; no bundled interpreter is needed.
 * Build on Linux ARM64: cc -O2 -Wall -Wextra -Werror -o frame-hev-setup tools/framedrop-launcher.c
 */
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv) {
    char executable[PATH_MAX];
    ssize_t length = readlink("/proc/self/exe", executable, sizeof(executable) - 1);
    if (length < 0 || (size_t)length >= sizeof(executable) - 1) {
        fputs("Frame HEV: could not locate the installer executable.\n", stderr);
        return 1;
    }
    executable[length] = '\0';
    char **arguments = calloc((size_t)argc + 2, sizeof(*arguments));
    if (!arguments) return 1;
    arguments[0] = "/usr/bin/python3";
    arguments[1] = executable;
    for (int i = 1; i < argc; ++i) arguments[i + 1] = argv[i];
    execv(arguments[0], arguments);
    perror("Frame HEV: could not start Python");
    free(arguments);
    return 1;
}
