/* SPDX-License-Identifier: GPL-3.0-only
 * Native Linux ARM64 entry point for FrameDrop's executable detection.
 * The payload stays ordinary, inspectable Python; no bundled interpreter.
 * Build on Linux ARM64: cc -O2 -Wall -Wextra -Werror -o frame-hev-setup tools/framedrop-launcher.c
 */
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    char executable[PATH_MAX], script[PATH_MAX];
    ssize_t length = readlink("/proc/self/exe", executable, sizeof(executable) - 1);
    if (length < 0 || (size_t)length >= sizeof(executable) - 1) {
        fputs("Frame HEV: could not locate the installer executable.\n", stderr);
        return 1;
    }
    executable[length] = '\0';
    char *separator = strrchr(executable, '/');
    if (!separator) return 1;
    *separator = '\0';
    int size = snprintf(script, sizeof(script), "%s/payload/tools/framedrop.py", executable);
    if (size < 0 || (size_t)size >= sizeof(script)) {
        fputs("Frame HEV: installation path is too long.\n", stderr);
        return 1;
    }
    char **arguments = calloc((size_t)argc + 2, sizeof(*arguments));
    if (!arguments) return 1;
    arguments[0] = "/usr/bin/python3";
    arguments[1] = script;
    for (int i = 1; i < argc; ++i) arguments[i + 1] = argv[i];
    execv(arguments[0], arguments);
    perror("Frame HEV: could not start Python");
    free(arguments);
    return 1;
}
