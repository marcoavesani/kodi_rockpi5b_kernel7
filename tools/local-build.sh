#!/usr/bin/env bash
#
# Rootless local build: runs build_rk3588_media_stack.py in a Debian 13 chroot
# with the same packages as docker/Dockerfile, using an unprivileged user
# namespace. Needs neither Docker nor sudo (works in WSL2), only unshare,
# curl, tar and a host C compiler.
#
# It builds for the host architecture, so on x86_64 it is a fast compile check
# of the whole stack before pushing; the arm64 packages still come from CI.
#
# Usage:
#   tools/local-build.sh ffmpeg                        # one target
#   tools/local-build.sh ffmpeg libplacebo mpv kodi joystick
#   tools/local-build.sh --ffmpeg-ref n9.0.2 ffmpeg    # any builder option
#   tools/local-build.sh --shell                       # shell in the chroot
#
# Defaults to --config /work/rk3588-media-stack.ci.ini -j <nproc>.
# State lives in $LOCAL_BUILD_DIR (default: <repo>/.local-build):
#   rootfs/  the chroot (source trees under rootfs/build), output/  .debs, ccache/
set -Eeuo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dir=${LOCAL_BUILD_DIR:-$repo/.local-build}
root=$dir/rootfs
image=${LOCAL_BUILD_IMAGE:-library/debian:trixie}

# Run a command as fake root inside the chroot, with the repo at /work.
in_root() {
  unshare -r -m -p -f --kill-child bash -c '
    set -e
    root=$1 repo=$2 dir=$3; shift 3
    mount -t proc proc "$root/proc"
    mount --rbind /dev "$root/dev"
    mount --rbind /sys "$root/sys" 2>/dev/null || true
    mkdir -p "$root/work" "$root/output" "$root/ccache"
    mount --bind "$repo" "$root/work"
    mount --bind "$dir/output" "$root/output"
    mount --bind "$dir/ccache" "$root/ccache"
    exec chroot "$root" /usr/bin/env -i HOME=/root TERM="${TERM:-xterm}" LANG=C.UTF-8 \
      DEBIAN_FRONTEND=noninteractive CCACHE_DIR=/ccache \
      PATH=/usr/lib/ccache:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin "$@"
  ' _ "$root" "$repo" "$dir" "$@"
}

docker_arch() {
  case "$(uname -m)" in
    x86_64) echo amd64 ;;
    aarch64) echo arm64 ;;
    *) echo "Unsupported host architecture: $(uname -m)" >&2; exit 1 ;;
  esac
}

# Fetch the Docker Hub image (single layer) for the host architecture as a rootfs.
create_rootfs() {
  local arch token index digest manifest layer
  arch=$(docker_arch)
  echo "==> Downloading $image ($arch) rootfs"
  token=$(curl -fsSL "https://auth.docker.io/token?service=registry.docker.io&scope=repository:${image%:*}:pull" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
  index=$(curl -fsSL -H "Authorization: Bearer $token" \
    -H "Accept: application/vnd.oci.image.index.v1+json" \
    -H "Accept: application/vnd.docker.distribution.manifest.list.v2+json" \
    "https://registry-1.docker.io/v2/${image%:*}/manifests/${image##*:}")
  digest=$(python3 -c 'import json,sys; print(next(m["digest"] for m in json.loads(sys.argv[1])["manifests"] if m["platform"]["architecture"] == sys.argv[2] and m["platform"]["os"] == "linux"))' "$index" "$arch")
  manifest=$(curl -fsSL -H "Authorization: Bearer $token" \
    -H "Accept: application/vnd.oci.image.manifest.v1+json" \
    -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
    "https://registry-1.docker.io/v2/${image%:*}/manifests/$digest")
  layer=$(python3 -c 'import json,sys; l=json.loads(sys.argv[1])["layers"]; assert len(l) == 1, "expected a single-layer image"; print(l[0]["digest"])' "$manifest")

  rm -rf "$root.tmp"
  mkdir -p "$root.tmp"
  curl -fsSL -H "Authorization: Bearer $token" "https://registry-1.docker.io/v2/${image%:*}/blobs/$layer" |
    unshare -r tar -xz -C "$root.tmp" --exclude='dev/*' 2>/dev/null || true
  [[ -x $root.tmp/usr/bin/apt-get ]] || { echo "rootfs extraction failed" >&2; exit 1; }

  # Only uid/gid 0 exist in the namespace: make chown to other ids a no-op for dpkg.
  cc -shared -fPIC -O2 -o "$root.tmp/usr/lib/libnochown.so" -x c - -ldl <<'EOF'
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <sys/types.h>
#define WRAP(name, proto, args) \
  int name proto { \
    static int (*real) proto; \
    if (!real) real = dlsym(RTLD_NEXT, #name); \
    int r = real args; \
    if (r != 0 && errno == EINVAL) { errno = 0; return 0; } \
    return r; \
  }
WRAP(chown, (const char *p, uid_t u, gid_t g), (p, u, g))
WRAP(lchown, (const char *p, uid_t u, gid_t g), (p, u, g))
WRAP(fchown, (int fd, uid_t u, gid_t g), (fd, u, g))
WRAP(fchownat, (int d, const char *p, uid_t u, gid_t g, int f), (d, p, u, g, f))
EOF
  echo /usr/lib/libnochown.so > "$root.tmp/etc/ld.so.preload"
  # apt cannot drop privileges to _apt in the namespace.
  echo 'APT::Sandbox::User "root";' > "$root.tmp/etc/apt/apt.conf.d/99local-build"
  rm -f "$root.tmp/etc/resolv.conf"
  cp /etc/resolv.conf "$root.tmp/etc/resolv.conf"
  mv "$root.tmp" "$root"
}

# Install the packages listed in docker/Dockerfile (re-run when it changes).
provision_rootfs() {
  local stamp=$root/.dockerfile.sha256 want packages
  want=$(sha256sum "$repo/docker/Dockerfile" | cut -d' ' -f1)
  [[ -f $stamp && $(cat "$stamp") == "$want" ]] && return

  packages=$(sed -n '/apt-get install -y --no-install-recommends \\$/,/&& gem install/p' "$repo/docker/Dockerfile" |
    grep -vE 'apt-get|gem install' | tr -d '\\' | xargs)
  echo "==> Installing $(wc -w <<<"$packages") packages from docker/Dockerfile"
  in_root bash -ec "
    apt-get update
    apt-get install -y --no-install-recommends $packages
    command -v fpm >/dev/null || gem install --no-document fpm
    # Same as the Dockerfile: Linux 7.x UAPI headers from trixie-backports.
    if grep -q '^VERSION_CODENAME=trixie\$' /etc/os-release; then
      echo 'deb http://deb.debian.org/debian trixie-backports main' > /etc/apt/sources.list.d/trixie-backports.list
      apt-get update
      apt-get install -y --no-install-recommends -t trixie-backports linux-libc-dev
    fi
    apt-get clean
  "
  echo "$want" > "$stamp"
}

mkdir -p "$dir/output" "$dir/ccache"
[[ -d $root ]] || create_rootfs
provision_rootfs

if [[ ${1:-} == --shell ]]; then
  in_root bash -l
  exit
fi

args=("$@")
[[ " ${args[*]} " == *" --config "* ]] || args=(--config /work/rk3588-media-stack.ci.ini "${args[@]}")
[[ " ${args[*]} " == *" -j "* || " ${args[*]} " == *" --jobs "* ]] || args=(-j "$(nproc)" "${args[@]}")
in_root bash -c 'cd /work && exec python3 /work/build_rk3588_media_stack.py "$@"' _ "${args[@]}"
