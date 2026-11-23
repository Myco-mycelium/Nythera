#!/usr/bin/env bash
#
# setup_win_toolchain.sh -- build the Windows toolchain cache from scratch.
#
# WHAT IT PRODUCES
#
#   The exact tree tools/win_toolchain.sh expects under
#   ${NYKERNEL_WIN_TOOLS:-$HOME/.cache/nyrqis-win-tools} (see that script's
#   header for why each piece is there):
#
#     msys64/                          MSYS2 base + pacman packages: MinGW
#                                      binutils, gcc, QEMU, mtools, xorriso
#     elf/bin/x86_64-elf-*             the ELF cross binutils (GCC 15.2.0
#                                      bundle); the kernel and userprogs are
#                                      ELF64 and need a second ld emulation
#     grub/grub-2.06-for-windows/      grub-mkimage + the i386-pc module tree
#                                      (no grub-mkrescue: it is a Unix shell
#                                      wrapper over mkimage and xorriso)
#     ovmf/x64/{code,vars}.fd          the UEFI firmware the boot gate loads
#                                      (kept beside the upstream tag it came
#                                      from, ovmf/<tag>/x64/, as provenance)
#
#   Plus the rustup side: the pinned 1.99.1 toolchain in its
#   x86_64-pc-windows-gnu port (the MSVC port cannot link a host build script
#   without Visual Studio + the Windows SDK) with rust-src and clippy, and the
#   x86_64-unknown-uefi target that the signed stub builds for.
#
# WHY THIS IS NOT `apt install`
#
#   None of this exists on a stock Windows box, and none of it needs an
#   administrator: it is a directory tree plus files the tree's own scripts
#   read. MSYS2 is installed from its relocatable base tarball rather than its
#   installer so nothing touches the registry or PATH system-wide, and every
#   other piece is a portable zip/tar from its upstream release page.
#
# IDEMPOTENT AND RESUMABLE
#
#   Each step is skipped when its marker exists, and downloads resume from
#   whatever is already on disk (the cross binutils bundle is ~900 MB). Re-run
#   it after an interrupted attempt and it continues; run it on a complete
#   cache and it only re-verifies.
#
#   The download directory is separate from the install tree
#   (NYKERNEL_WIN_TOOLS_DL, default .../dl) so a fresh tree can be built from
#   already-downloaded archives -- that is how this script's own extraction and
#   layout logic is tested without a second 900 MB fetch.
#
# USAGE
#
#   tools/setup_win_toolchain.sh              # install, or top up what is there
#   tools/setup_win_toolchain.sh --verify     # no downloads; check the tree only
#   tools/setup_win_toolchain.sh --plan       # print the steps and exit
#
#   Needs: Git Bash or MSYS2, curl, rustup, and about 3 GB free under
#   $HOME/.cache (archives + extracted tools).
#
set -euo pipefail

WIN_TOOLS="${NYKERNEL_WIN_TOOLS:-$HOME/.cache/nyrqis-win-tools}"
DL="${NYKERNEL_WIN_TOOLS_DL:-$WIN_TOOLS/dl}"
HERE="$(cd "$(dirname "$0")" && pwd)"
TREE="$(cd "$HERE/.." && pwd)"

MODE=install
case "${1:-}" in
    "")        MODE=install ;;
    --verify)  MODE=verify ;;
    --plan)    MODE=plan ;;
    -h|--help) sed -n '2,50p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)         echo "usage: $0 [--verify|--plan]" >&2; exit 2 ;;
esac

case "$(uname -s)" in
    MINGW*|MSYS*) ;;
    *) echo "FAIL: this sets up a WINDOWS toolchain (run it from Git Bash or MSYS2);" >&2
       echo "      on Linux the distro packages are what build.sh expects." >&2
       exit 1 ;;
esac

say() { printf '==> %s\n' "$*"; }
die() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

# Pinned versions, all of them exact. A floating "latest" would let the
# toolchain a build was proved with change underneath the repository: the ELF
# binutils version is named in tools/verify_prog_layout.sh's PE/ELF catalogue
# comments, and the GRUB/OVMF tags are what the boot gate's transcripts were
# earned with.
MSYS2_URL="https://repo.msys2.org/distrib/msys2-x86_64-latest.tar.xz"
ELF_URL="https://github.com/lordmilko/i686-elf-tools/releases/download/15.2.0/x86_64-elf-tools-windows.zip"
GRUB_URL="https://ftp.gnu.org/gnu/grub/grub-2.06-for-windows.zip"
OVMF_TAG="edk2-stable202605-r1"
OVMF_URL="https://github.com/rust-osdev/ovmf-prebuilt/releases/download/${OVMF_TAG}/${OVMF_TAG}-bin.tar.xz"
RUST_TOOLCHAIN="1.99.1-x86_64-pc-windows-gnu"

MSYS="$WIN_TOOLS/msys64"
PACMAN="$MSYS/usr/bin/pacman.exe"
BSTAR="$MSYS/usr/bin/bsdtar.exe"

# The pacman set win_toolchain.sh's header documents. gcc is here because the
# host-side Rust build scripts link through it (the gnu rustc port calls gcc,
# not ld); qemu and mtools are the boot gate's and the ESP's; xorriso writes
# both ISOs. Note what is NOT here: no grub package (MSYS2 has none) and no
# edk2-ovmf (also absent from its repos) -- those come from upstream releases
# below.
PACMAN_PKGS=(
    mingw-w64-ucrt-x86_64-binutils
    mingw-w64-ucrt-x86_64-gcc
    mingw-w64-ucrt-x86_64-qemu
    mingw-w64-ucrt-x86_64-mtools
    xorriso
)

# ---- download helper -----------------------------------------------------
# -C - resumes a partial file, which matters for the ~900 MB bundle. The size
# probe follows redirects (every GitHub asset does) and takes the LAST
# Content-Length header, because the redirect chain reports one per hop and
# only the final response's is the body's size.
fetch() {
    local url="$1" out="$2" want have
    mkdir -p "$(dirname "$out")"
    want="$(curl -fsSLI "$url" 2>/dev/null \
            | tr -d '\r' \
            | awk 'tolower($1)=="content-length:"{v=$2} END{print v}')"
    have="$(stat -c %s "$out" 2>/dev/null || echo 0)"
    if [[ -n "$want" && "$have" == "$want" ]]; then
        say "$(basename "$out") already complete ($have bytes)"
        return 0
    fi
    say "fetching $(basename "$out") (${want:-?} bytes; $have present)"
    curl -fL --retry 5 --retry-delay 2 -C - -o "$out" "$url"
    have="$(stat -c %s "$out" 2>/dev/null || echo 0)"
    if [[ -n "$want" && "$have" != "$want" ]]; then
        die "$(basename "$out") is $have bytes, expected $want (re-run to resume)"
    fi
}

# ---- steps ---------------------------------------------------------------
step_msys2() {
    if [[ -x "$PACMAN" ]]; then
        say "MSYS2 base already unpacked"
        return 0
    fi
    fetch "$MSYS2_URL" "$DL/msys2.tar.xz"
    say "unpacking the MSYS2 base root ($MSYS)"
    # The tarball's own root is msys64/, so it is unpacked beside itself.
    mkdir -p "$WIN_TOOLS"
    tar -xf "$DL/msys2.tar.xz" -C "$WIN_TOOLS"
    [[ -x "$PACMAN" ]] || die "the MSYS2 tarball did not produce $PACMAN"
}

step_pacman() {
    # A freshly unpacked MSYS2 base has an EMPTY pacman keyring, so the first
    # `pacman -Sy` dies with "key ... is unknown" + "keyring is not writable"
    # and never sees a package database. It has to be initialised and populated
    # with the msys2 keyset once, through MSYS2's OWN bash: pacman-key is a
    # shell script, so there is no pacman-key.exe to call directly. This is the
    # slowest single step of the whole setup (it generates a GPG key), which is
    # why it is guarded by a marker rather than repeated on every run.
    if [[ ! -s "$MSYS/etc/pacman.d/gnupg/pubring.gpg" ]]; then
        say "initialising the pacman keyring (slow: it generates a GPG key)"
        MSYSTEM=UCRT64 "$MSYS/usr/bin/bash.exe" -lc \
            'pacman-key --init && pacman-key --populate msys2' \
            || die "pacman-key failed; re-run to resume (this step is idempotent)"
    fi

    local have_missing=0
    for p in "${PACMAN_PKGS[@]}"; do
        MSYSTEM=UCRT64 "$PACMAN" -Q "$p" >/dev/null 2>&1 || have_missing=1
    done
    if [[ "$have_missing" == "0" ]]; then
        say "pacman packages already installed"
        return 0
    fi
    # -Sy refreshes the package databases; a full -Su is deliberately NOT run,
    # because the base tarball and the repo can legitimately be a few days
    # apart and an unattended whole-system upgrade is a bigger act than
    # installing five tools.
    say "installing pacman packages: ${PACMAN_PKGS[*]}"
    MSYSTEM=UCRT64 "$PACMAN" -Sy --noconfirm >/dev/null
    MSYSTEM=UCRT64 "$PACMAN" -S --noconfirm --needed "${PACMAN_PKGS[@]}"
}

step_elf() {
    if [[ -x "$WIN_TOOLS/elf/bin/x86_64-elf-ld.exe" ]]; then
        say "ELF cross binutils already unpacked"
        return 0
    fi
    fetch "$ELF_URL" "$DL/x86_64-elf-tools-windows.zip"
    say "unpacking the ELF cross binutils (elf/)"
    # The zip's root is bin/, so it is unpacked INTO elf/.
    mkdir -p "$WIN_TOOLS/elf"
    [[ -x "$BSTAR" ]] || die "bsdtar missing (run the MSYS2 step first)"
    "$BSTAR" -xf "$DL/x86_64-elf-tools-windows.zip" -C "$WIN_TOOLS/elf"
    [[ -x "$WIN_TOOLS/elf/bin/x86_64-elf-ld.exe" ]] \
        || die "the ELF bundle did not produce elf/bin/x86_64-elf-ld.exe"
}

step_grub() {
    if [[ -x "$WIN_TOOLS/grub/grub-2.06-for-windows/grub-mkimage.exe" ]]; then
        say "GRUB for Windows already unpacked"
        return 0
    fi
    fetch "$GRUB_URL" "$DL/grub-2.06-for-windows.zip"
    say "unpacking GRUB (grub/grub-2.06-for-windows)"
    mkdir -p "$WIN_TOOLS/grub"
    "$BSTAR" -xf "$DL/grub-2.06-for-windows.zip" -C "$WIN_TOOLS/grub"
    [[ -x "$WIN_TOOLS/grub/grub-2.06-for-windows/grub-mkimage.exe" ]] \
        || die "the GRUB zip did not produce grub-mkimage.exe"
}

step_ovmf() {
    # The release archive's own root is "${tag}-bin", not the bare tag; taking
    # that name from the extraction instead of assuming it is what a fresh
    # install test caught.
    local tag_dir="$WIN_TOOLS/ovmf/${OVMF_TAG}-bin"
    if [[ -s "$WIN_TOOLS/ovmf/x64/code.fd" && -s "$WIN_TOOLS/ovmf/x64/vars.fd" ]]; then
        say "OVMF $OVMF_TAG already unpacked"
        return 0
    fi
    if [[ ! -s "$tag_dir/x64/code.fd" ]]; then
        fetch "$OVMF_URL" "$DL/ovmf.tar.xz"
        say "unpacking OVMF ($OVMF_TAG)"
        mkdir -p "$WIN_TOOLS/ovmf"
        "$BSTAR" -xf "$DL/ovmf.tar.xz" -C "$WIN_TOOLS/ovmf"
        [[ -s "$tag_dir/x64/code.fd" ]] \
            || die "the OVMF tarball did not produce $tag_dir/x64/code.fd"
    fi
    # tools/win_toolchain.sh's documented layout is ovmf/x64/{code,vars}.fd,
    # and the boot gate copies from there, so the per-release directory is
    # provenance only and these two are the stable names.
    mkdir -p "$WIN_TOOLS/ovmf/x64"
    cp "$tag_dir/x64/code.fd" "$tag_dir/x64/vars.fd" "$WIN_TOOLS/ovmf/x64/"
    say "OVMF blobs at ovmf/x64 (from $OVMF_TAG)"
}

step_rust() {
    command -v rustup >/dev/null 2>&1 || die "rustup is not on PATH"
    if rustup toolchain list | grep -qx "$RUST_TOOLCHAIN"; then
        say "rust toolchain $RUST_TOOLCHAIN already installed"
    else
        # --profile minimal + the two components the tree actually needs:
        # rust-src for build-std (the freestanding kernel target has no
        # prebuilt std) and clippy for the mandatory shadow/lint gates.
        say "installing rust toolchain $RUST_TOOLCHAIN (rust-src, clippy)"
        rustup toolchain install "$RUST_TOOLCHAIN" --profile minimal \
            --component rust-src --component clippy
    fi
    # The stub is built for x86_64-unknown-uefi on the GNU host port (the port
    # win_toolchain.sh selects), so the target must be installed FOR THAT
    # toolchain -- the default toolchain's copy would not be found.
    if ! rustup target list --toolchain "$RUST_TOOLCHAIN" --installed \
            | grep -qx "x86_64-unknown-uefi"; then
        say "adding the x86_64-unknown-uefi target to $RUST_TOOLCHAIN"
        rustup target add --toolchain "$RUST_TOOLCHAIN" x86_64-unknown-uefi
    fi
}

# The boot gate reads its firmware from the TREE (uefi/tools/OVMF_*.fd), not
# from this cache, because the discs and their receipt travel together with
# the blobs that booted them. Copy them when run from a kernel checkout, which
# is the only way this script is meant to be used.
step_ovmf_into_tree() {
    local src="$WIN_TOOLS/ovmf/x64"
    [[ -d "$TREE/uefi/tools" ]] || return 0
    for f in code vars; do
        local to="$TREE/uefi/tools/OVMF_$(echo "$f" | tr 'a-z' 'A-Z')_4M.fd"
        if cmp -s "$src/$f.fd" "$to"; then
            say "uefi/tools/$(basename "$to") already matches"
        else
            cp "$src/$f.fd" "$to"
            say "copied $(basename "$to") into the tree"
        fi
    done
}

# ---- verification: the same checks tools/win_toolchain.sh makes ----------
verify() {
    local missing=0
    for d in elf/bin msys64/ucrt64/bin msys64/usr/bin \
             "grub/grub-2.06-for-windows" "ovmf/x64"; do
        [[ -d "$WIN_TOOLS/$d" ]] || { echo "  missing dir  $WIN_TOOLS/$d"; missing=1; }
    done
    for f in elf/bin/x86_64-elf-ld.exe elf/bin/x86_64-elf-as.exe \
             elf/bin/x86_64-elf-objdump.exe elf/bin/x86_64-elf-strip.exe \
             elf/bin/x86_64-elf-readelf.exe \
             msys64/ucrt64/bin/ld.exe msys64/ucrt64/bin/as.exe \
             msys64/ucrt64/bin/objdump.exe msys64/ucrt64/bin/gcc.exe \
             msys64/ucrt64/bin/qemu-system-x86_64.exe \
             msys64/ucrt64/bin/mcopy.exe msys64/usr/bin/xorriso.exe \
             grub/grub-2.06-for-windows/grub-mkimage.exe \
             ovmf/x64/code.fd ovmf/x64/vars.fd; do
        [[ -e "$WIN_TOOLS/$f" ]] || { echo "  missing file $WIN_TOOLS/$f"; missing=1; }
    done
    # grub-mkimage is only useful with its module tree beside it, and the
    # module the BIOS disc needs is the one this build must NOT name: GRUB 2
    # has no cdrom module at all. Assert the tree is genuinely populated.
    local moddir="$WIN_TOOLS/grub/grub-2.06-for-windows/i386-pc"
    if [[ -d "$moddir" ]]; then
        for m in biosdisk.mod iso9660.mod normal.mod multiboot2.mod search.mod; do
            [[ -e "$moddir/$m" ]] || { echo "  missing module $m"; missing=1; }
        done
    fi
    [[ "$missing" == "0" ]] || return 1

    # Versions, so the transcript says WHICH toolchain was just proved: the
    # layout gate's catalogue keys on the PE ld version.
    echo "  elf binutils : $("$WIN_TOOLS/elf/bin/x86_64-elf-ld.exe" --version | head -1)"
    echo "  mingw ld     : $("$WIN_TOOLS/msys64/ucrt64/bin/ld.exe" --version | head -1)"
    echo "  mingw gcc    : $("$WIN_TOOLS/msys64/ucrt64/bin/gcc.exe" -dumpfullversion)"
    echo "  qemu         : $("$WIN_TOOLS/msys64/ucrt64/bin/qemu-system-x86_64.exe" --version | head -1)"
    echo "  xorriso      : $("$WIN_TOOLS/msys64/usr/bin/xorriso.exe" --version | head -1)"
    echo "  grub-mkimage : $("$WIN_TOOLS/grub/grub-2.06-for-windows/grub-mkimage.exe" --version | head -1)"
    if command -v rustup >/dev/null 2>&1; then
        echo "  rustc (gnu)  : $(rustup run "$RUST_TOOLCHAIN" rustc -vV | grep -E '^(release|host):' | tr '\n' ' ')"
    fi
    return 0
}

if [[ "$MODE" == "plan" ]]; then
    cat <<EOF
cache root : $WIN_TOOLS
downloads  : $DL
steps      :
  1. MSYS2 base tarball            $MSYS2_URL
  2. pacman: ${PACMAN_PKGS[*]}
  3. ELF cross binutils 15.2.0     $ELF_URL
  4. GRUB 2.06 for Windows         $GRUB_URL
  5. OVMF ${OVMF_TAG}              $OVMF_URL
  6. rustup $RUST_TOOLCHAIN + x86_64-unknown-uefi
  7. copy OVMF blobs into $TREE/uefi/tools (if that tree is a checkout)
EOF
    exit 0
fi

if [[ "$MODE" == "verify" ]]; then
    say "verifying $WIN_TOOLS"
    verify || die "the cache is incomplete (run without --verify to finish it)"
    say "cache is complete"
    exit 0
fi

step_msys2
step_pacman
step_elf
step_grub
step_ovmf
step_rust
step_ovmf_into_tree

say "verifying"
verify || die "the cache is incomplete after setup (see the missing entries above)"
say "ready: source tools/win_toolchain.sh (build.sh does it on MINGW/MSYS hosts)"
