# RK3588 V4L2 Request media stack builder

This repository builds ARM64 `.deb` packages for a ROCK Pi 5B / RK3588 media stack:

- FFmpeg with V4L2 Request support
- mpv with `--hwdec=v4l2request`
- Kodi GBM/GLES linked against that FFmpeg
- optional Kodi `peripheral.joystick`

The build runs inside Docker and is intended for GitHub Actions ARM64 runners.

## Files

```text
build_rk3588_media_stack.py       Main Python builder
rk3588-media-stack.ini            Local/default config
rk3588-media-stack.ci.ini         CI/container config
docker/Dockerfile                 Builder image
docker/entrypoint.sh              Container entrypoint
.github/workflows/build-debs.yml  GitHub Actions workflow
```

## Local ARM64 build

On an ARM64 machine:

```bash
docker build --platform linux/arm64 -f docker/Dockerfile -t rk3588-media-builder:arm64 .

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

## GitHub Actions build

Push this repository to GitHub and run:

```text
Actions -> Build RK3588 media stack debs -> Run workflow
```

You can override the refs in the workflow form:

```text
FFmpeg ref: master, n7.1, or a commit hash
mpv ref:    v4l2request or a commit hash
Kodi ref:   master, Omega, or a commit hash
```

The generated packages are uploaded as the workflow artifact:

```text
rk3588-media-stack-debs-arm64
```

## Pinning known-good versions

Edit `rk3588-media-stack.ci.ini`:

```ini
[ffmpeg]
ref = n7.1

[mpv]
ref = v4l2request

[kodi]
ref = master
```

or pass overrides in the workflow.

## Install on the ROCK Pi

Copy the `.deb` files to the ROCK Pi 5B, then:

```bash
sudo apt install ./ffmpeg-v4l2request-rockchip_*.deb
sudo apt install ./mpv-v4l2request-rockchip_*.deb
sudo apt install ./kodi-v4l2request-rockchip_*.deb
sudo apt install ./kodi-v4l2request-peripheral-joystick-rockchip_*.deb
sudo ldconfig
```

Check:

```bash
/usr/local/bin/ffmpeg -hide_banner -hwaccels
/usr/local/bin/mpv --hwdec=help | grep -i v4l2
ldd /usr/local/lib/kodi/kodi.bin | grep -E 'avcodec|avformat|avutil'
```

## Notes

The default install prefix is `/usr/local`, matching upstream source-install defaults. If you prefer Debian-policy-style packages under `/usr`, change this in `rk3588-media-stack.ci.ini`:

```ini
[paths]
install_prefix = /usr
```

Then rebuild the packages.
