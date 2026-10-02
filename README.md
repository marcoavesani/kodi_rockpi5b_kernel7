# RK3588 V4L2 Request media stack builder

This repository builds ARM64 `.deb` packages for a ROCK Pi 5B / RK3588 media stack:

- upstream FFmpeg `n9.0.2` + LibreELEC's V4L2 Request, libpostproc and RK3588 HEVC patches
- upstream mpv `v0.41.0` + the V4L2 Request hwdec commits of [mpv PR #14690](https://github.com/mpv-player/mpv/pull/14690) (`--hwdec=v4l2request`)
- upstream libplacebo `v7.360.1`
- upstream Kodi `22.0rc1-Piers` linked against that FFmpeg
- optional Kodi `peripheral.joystick`

Everything is built from upstream repositories; the only non-upstream code is a short, pinned list of patch files.
The build runs inside a Debian 13/Trixie Docker container and is intended for GitHub Actions ARM64 runners.

## Files

```text
build_rk3588_media_stack.py       Main Python builder
rk3588-media-stack.ini            Local/default config
rk3588-media-stack.ci.ini         CI/container config
docker/Dockerfile                 Builder image
docker/entrypoint.sh              Container entrypoint
tools/local-build.sh              Rootless local build (no Docker/sudo), for compile checks
.github/workflows/build-debs.yml  GitHub Actions workflow
```


## Debian Trixie base image

The default builder image is:

```Dockerfile
ARG BASE_IMAGE=debian:trixie
```

This matches Debian 13/Trixie userland. The container does **not** reproduce the ROCK Pi kernel; it only provides the userspace build environment used to compile and package FFmpeg, mpv and Kodi.

## Local ARM64 build

On an ARM64 machine:

```bash
docker build --platform linux/arm64 --build-arg BASE_IMAGE=debian:trixie -f docker/Dockerfile -t rk3588-media-builder:arm64 .

mkdir -p artifacts ccache

docker run --rm \
  --platform linux/arm64 \
  -v "$PWD:/work" \
  -v "$PWD/artifacts:/output" \
  -v "$PWD/ccache:/ccache" \
  rk3588-media-builder:arm64 \
  --config /work/rk3588-media-stack.ci.ini \
  ffmpeg mpv kodi joystick
```

The `.deb` packages appear in:

```text
artifacts/
```

## Local compile check before pushing

A full CI run takes a long time, so check changes locally first. The builder is not tied to arm64, so on an
x86_64 machine the whole stack can be compiled natively (fast) as a check; only the published arm64 packages
need CI or an arm64 machine.

Without Docker or sudo (for example in WSL2), `tools/local-build.sh` runs the builder in a rootless Debian 13
chroot with the same packages as `docker/Dockerfile`:

```bash
tools/local-build.sh ffmpeg
tools/local-build.sh libplacebo mpv
tools/local-build.sh kodi joystick
```

It passes `--config /work/rk3588-media-stack.ci.ini -j $(nproc)` unless given, accepts every builder option
(`--ffmpeg-ref`, `--mpv-ref`, ...), and keeps its state in `.local-build/` (`rootfs/`, `output/` with the
`.deb` files, `ccache/`). Source trees persist in `.local-build/rootfs/build`, so re-runs only rebuild.
`tools/local-build.sh --shell` opens a shell in the chroot.

With Docker, the same check is the Docker build above with `linux/amd64` instead of `linux/arm64`.

## GitHub Actions build

Push this repository to GitHub and run:

```text
Actions -> Build RK3588 media stack debs -> Run workflow
```

You can override the refs in the workflow form:

```text
FFmpeg ref:          git ref (tag/branch/commit), for example n9.0.2
FFmpeg patch commit: LibreELEC.tv commit or branch the patch files come from, for example master
mpv ref:             git ref (tag/branch/commit), for example v0.41.0
Kodi ref:            git ref (tag/branch/commit), for example 22.0rc1-Piers
```

Kodi tags have no `v` prefix (`22.0rc1-Piers`, not `v22.0rc1-Piers`).

The generated packages are uploaded as the workflow artifact:

```text
rk3588-media-stack-debs-arm64
```


## Patches on top of upstream

Each component section in the `.ini` files can list patch files. They are applied in order with
GNU `patch -p1` after checking out `ref`, and the build stops if any patch fails or leaves `.rej` files.

```ini
[ffmpeg]
ref = n9.0.2
apply_patch = yes
patch_base_url = https://raw.githubusercontent.com/LibreELEC/LibreELEC.tv/{commit}/packages/multimedia/ffmpeg/patches
patch_commit = a1d24cae66825c22ba2d6c6013886347a9f66081
patches =
    postproc/0001-postproc.patch
    v4l2-request/0001-v4l2-request.patch
    v4l2-request/0002-v4l2-request-vc1.patch
    detlev/0001-hevc-Add-support-for-sps_st_rps-control.patch
extra_patches =
min_kernel_headers = 7.0
```

- `patches` are relative to `patch_base_url`, where `{commit}` is replaced by `patch_commit`.
- `extra_patches` are local files (relative to the `.ini` file) or full URLs, applied afterwards.
- `apply_patch = no` (or `--ffmpeg-no-patch` / `--mpv-no-patch`) skips the patch list, for example
  when `repo`/`ref` point at a fork that already contains the changes.

### FFmpeg

The FFmpeg patch set is the one LibreELEC uses for RK3588. It comes from LibreELEC.tv, pinned to a commit
so that the same config always produces the same FFmpeg:

| Patch | Purpose |
| --- | --- |
| `postproc` | Brings back libpostproc (removed from FFmpeg 8); Kodi uses it |
| `v4l2-request` | Jonas Karlman's (Kwiboo) V4L2 Request hwaccels: H.264, HEVC, MPEG-2, VP8, VP9, AV1, VC-1 |
| `detlev` | HEVC ext SPS RPS controls needed by the RK3588 rkvdec HEVC driver |

The `detlev` patch needs Linux 7.0 UAPI headers (`struct v4l2_ctrl_hevc_ext_sps_st_rps`). Debian 13 ships
6.12, so the Docker image installs `linux-libc-dev` from `trixie-backports`; `min_kernel_headers` makes the
build stop early with a clear message when the headers are too old.

LibreELEC generates the patches against the FFmpeg release it uses (`PKG_VERSION` in
`packages/multimedia/ffmpeg/package.mk`). To move to a newer FFmpeg, update `ref` and `patch_commit`
together, using a LibreELEC.tv commit whose `package.mk` uses that FFmpeg version.

After configure, the builder checks that the `h264_v4l2request` and `hevc_v4l2request` hwaccels are enabled
(FFmpeg's configure silently drops hwaccels with missing dependencies). After installing, it checks that
`ffmpeg -hwaccels` lists `v4l2request` and that the H.264/HEVC/VP9/AV1 decoders exist.

### mpv

Upstream mpv only has DRM PRIME interops bound to FFmpeg's DRM hwdevice. The V4L2 Request hwaccels use their own
`v4l2request` hwdevice, so mpv needs the two commits of upstream PR
[#14690](https://github.com/mpv-player/mpv/pull/14690) (`philipl/mpv`, branch `v4l2request`), pinned by commit SHA.
They add the `v4l2request` Meson option, `--hwdec=v4l2request`, and the `v4l2request` /
`v4l2request-overlay` GPU interops. The builder checks that `mpv --hwdec=help` lists `v4l2request`.

### Kodi

Kodi 22 tracks FFmpeg 9.0.2 itself and links against the external FFmpeg above (`-DFFMPEG_PATH`),
including its libpostproc. Kodi 22 needs SWIG >= 4.5 (Debian 13 has 4.3), so it is configured with
`-DENABLE_INTERNAL_SWIG=ON`, which builds the SWIG version Kodi pins.

## Pinning known-good versions

Edit `rk3588-media-stack.ci.ini`:

```ini
[ffmpeg]
ref = n9.0.2
patch_commit = a1d24cae66825c22ba2d6c6013886347a9f66081

[mpv]
ref = v0.41.0

[kodi]
ref = 22.0rc1-Piers
```

or pass overrides in the workflow (`--ffmpeg-ref`, `--ffmpeg-patch-commit`, `--mpv-ref`, `--kodi-ref`).

## Install on the ROCK Pi

Copy the `.deb` files from one build to the ROCK Pi 5B and install them together, so apt
resolves the dependencies between them:

```bash
sudo apt install ./libplacebo-rockchip_*.deb \
  ./ffmpeg-v4l2request-rockchip_*.deb \
  ./mpv-v4l2request-rockchip_*.deb \
  ./kodi-v4l2request-rockchip_*.deb \
  ./kodi-v4l2request-peripheral-joystick-rockchip_*.deb
```

mpv, Kodi and the joystick add-on depend on the exact FFmpeg/libplacebo/Kodi build they were
linked against, so apt refuses to mix packages from different builds instead of failing at runtime.
The packages refresh the dynamic linker cache (`ldconfig` trigger) on install and removal. Packages
built before that change need a manual `sudo ldconfig` after installing; otherwise Kodi fails with
`libavcodec.so.63: cannot open shared object file`.

Check:

```bash
/usr/local/bin/ffmpeg -hide_banner -hwaccels
/usr/local/bin/mpv --hwdec=help | grep -i v4l2request
ldd /usr/local/lib/kodi/kodi.bin | grep -E 'avcodec|avformat|avutil'
```

For runtime verification, use:

```bash
/usr/local/bin/mpv -v \
  --vo=gpu-next \
  --gpu-context=drm \
  --drm-connector=HDMI-A-2 \
  --drm-mode=1 \
  --hwdec=v4l2request \
  --gpu-hwdec-interop=v4l2request-overlay \
  sample-hevc.mkv
```

and confirm the log shows hardware decoding is active (for example `Using hardware decoding (v4l2request)`).

## Notes

The default install prefix is `/usr/local`, matching upstream source-install defaults. If you prefer Debian-policy-style packages under `/usr`, change this in `rk3588-media-stack.ci.ini`:

```ini
[paths]
install_prefix = /usr
```

Then rebuild the packages.
