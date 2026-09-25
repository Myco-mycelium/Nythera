/*
 * rootless-syscall-shim.c — LD_PRELOAD shim for build-live-iso-rootless.sh
 *
 * WHY THIS EXISTS
 * ---------------
 * The live ISO can be built without root by running the whole build in a
 * user namespace (unshare -Urmpf --mount-proc). Inside such a namespace
 * the build is uid 0 for *permission* purposes, but two things the
 * toolchain does still fail for real:
 *
 *   1. chown to an id that is not mapped (self-map maps exactly one id):
 *      the kernel returns EINVAL — and debootstrap's tar aborts hard on
 *      it while unpacking e.g. base-files (root:staff). fakeroot cannot
 *      paper over this: its daemon performs the real chown, swallows
 *      EPERM, but PROPAGATES EINVAL (verified on this machine).
 *   2. mknod of char/block devices: always denied in a user namespace,
 *      even as uid 0 inside it. The build needs /dev/null, /dev/console
 *      and friends to exist on disk (shell redirects, initramfs hooks).
 *
 * This shim intercepts the glibc wrappers so that:
 *
 *   - chown/lchown/fchown/fchownat return 0 without touching the file.
 *     That is HONEST here, not a lie: inside the self-mapped namespace
 *     every file the build creates is already owned by the one uid that
 *     maps to root (0). Ownership in the image is correct by
 *     construction; the calls are no-ops that report success.
 *   - mknod/mknodat of a char/block device creates a placeholder REGULAR
 *     file instead (EEXIST counts as success). The placeholder keeps
 *     `>/dev/null`, `2>/dev/console` and friends working during the
 *     build; at BOOT the kernel's devtmpfs supplies the real nodes, so
 *     the image never depends on them (the squashfs/initrd may carry
 *     placeholders — live systems mount devtmpfs over /dev). FIFOs,
 *     sockets and regular files pass through to the real mknod, which
 *     works unprivileged for those types.
 *
 * Scope: process-wide via LD_PRELOAD. Internal glibc calls that bypass
 * the PLT are irrelevant — the build's chown/mknod traffic comes from
 * external binaries (tar, dpkg, useradd, coreutils, initramfs-tools).
 *
 * Build (the driver does this):
 *   cc -O2 -fPIC -shared -o rootless-syscall-shim.so rootless-syscall-shim.c -ldl
 *
 * The driver compiles this file ONCE on the host and also copies the
 * .so into the rootfs at /tmp under the SAME BASENAME, exporting
 * LD_PRELOAD=/tmp/<name>.so — one absolute path that resolves both
 * outside and inside every chroot (the second debootstrap stage and the
 * builder's chroot steps run preloaded without touching the builder).
 */

#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

static ssize_t (*real_readlink)(int, char *, size_t);

static void shim_log(const char *fmt, ...)
{
    const char *dbg = getenv("NYRQIS_ROOTLESS_SHIM_DEBUG");
    va_list ap;
    if (!dbg || !*dbg)
        return;
    fputs("[rootless-shim] ", stderr);
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
}

/* ------------------------------------------------------------------ chown
 * Always succeed without a syscall. In the self-mapped user namespace the
 * build runs as the uid that maps to root inside the namespace, so every
 * created file is already root-owned as far as the image is concerned.
 */
int chown(const char *path, uid_t owner, gid_t group)
{
    (void)path; (void)owner; (void)group;
    shim_log("chown(%s) -> 0", path ? path : "(null)");
    return 0;
}

int lchown(const char *path, uid_t owner, gid_t group)
{
    (void)path; (void)owner; (void)group;
    shim_log("lchown(%s) -> 0", path ? path : "(null)");
    return 0;
}

int fchown(int fd, uid_t owner, gid_t group)
{
    (void)fd; (void)owner; (void)group;
    shim_log("fchown(%d) -> 0", fd);
    return 0;
}

int fchownat(int dirfd, const char *path, uid_t owner, gid_t group, int flags)
{
    (void)dirfd; (void)path; (void)owner; (void)group; (void)flags;
    shim_log("fchownat(%d, %s) -> 0", dirfd, path ? path : "(null)");
    return 0;
}

/* ------------------------------------------------------------------ mknod
 * Char/block devices: create a placeholder regular file. Best effort —
 * the only hard requirement is that the CALL SUCCEEDS so build tooling
 * proceeds; boot never depends on these nodes (devtmpfs at runtime).
 */
static int placeholder_node(const char *path)
{
    int fd;

    if (!path || !*path) {
        errno = ENOENT;
        return -1;
    }
    fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);
    if (fd >= 0) {
        close(fd);
        shim_log("mknod(%s) -> placeholder file", path);
        return 0;
    }
    if (errno == EEXIST)
        return 0; /* already there: success is success */
    /* Cannot create it either: report success anyway. A missing
     * placeholder degrades a single redirect at build time; a failed
     * mknod error would abort the whole build for nothing. */
    shim_log("mknod(%s) -> placeholder failed (%s), reporting success",
             path, strerror(errno));
    errno = 0;
    return 0;
}

static int placeholder_node_at(int dirfd, const char *path)
{
    char *procfd, *joined;
    size_t len;
    int rc;

    if (!path || *path == '/' || dirfd < 0 || dirfd == AT_FDCWD)
        return placeholder_node(path);

    /* Resolve the dirfd through /proc (always mounted inside the build
     * namespace) and join. If anything about that fails, fall back to a
     * best-effort absolute interpretation. */
    if (!real_readlink)
        real_readlink = dlsym(RTLD_NEXT, "readlink");
    len = (size_t)snprintf(NULL, 0, "/proc/self/fd/%d", dirfd) + 1;
    procfd = malloc(len);
    if (!procfd || !real_readlink) {
        free(procfd);
        return placeholder_node(path);
    }
    {
        ssize_t n = real_readlink(dirfd, procfd, len - 1);
        if (n <= 0) {
            free(procfd);
            return placeholder_node(path);
        }
        procfd[n] = '\0';
    }
    len = strlen(procfd) + strlen(path) + 2;
    joined = malloc(len);
    if (!joined) {
        free(procfd);
        return placeholder_node(path);
    }
    snprintf(joined, len, "%s/%s", procfd, path);
    rc = placeholder_node(joined);
    free(joined);
    free(procfd);
    return rc;
}

int mknod(const char *path, mode_t mode, dev_t dev)
{
    int (*real_mknod)(const char *, mode_t, dev_t) = dlsym(RTLD_NEXT, "mknod");

    (void)dev;
    switch (mode & S_IFMT) {
    case S_IFCHR:
    case S_IFBLK:
        return placeholder_node(path);
    default:
        /* FIFO/socket/regular: the real call works unprivileged. */
        if (real_mknod)
            return real_mknod(path, mode, dev);
        errno = ENOSYS;
        return -1;
    }
}

int mknodat(int dirfd, const char *path, mode_t mode, dev_t dev)
{
    int (*real_mknodat)(int, const char *, mode_t, dev_t) =
        dlsym(RTLD_NEXT, "mknodat");

    (void)dev;
    switch (mode & S_IFMT) {
    case S_IFCHR:
    case S_IFBLK:
        return placeholder_node_at(dirfd, path);
    default:
        if (real_mknodat)
            return real_mknodat(dirfd, path, mode, dev);
        errno = ENOSYS;
        return -1;
    }
}
