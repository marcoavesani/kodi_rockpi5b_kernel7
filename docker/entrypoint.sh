#!/usr/bin/env bash
set -Eeuo pipefail

mkdir -p /work /build /output /ccache

# If the repository checkout mounted at /work does not contain the builder,
# fall back to the copies baked into the image.
if [[ ! -f /work/build_rk3588_media_stack.py ]]; then
  cp /usr/local/src/rk3588-media-stack/build_rk3588_media_stack.py /work/
  chmod +x /work/build_rk3588_media_stack.py
fi

if [[ ! -f /work/rk3588-media-stack.ci.ini ]]; then
  cp /usr/local/src/rk3588-media-stack/rk3588-media-stack.ci.ini /work/
fi

if [[ ! -f /work/rk3588-media-stack.ini ]]; then
  cp /usr/local/src/rk3588-media-stack/rk3588-media-stack.ini /work/
fi

cd /work

echo "Container architecture: $(uname -m)"
echo "Output directory: /output"
echo "CCACHE_DIR=${CCACHE_DIR:-/ccache}"

exec python3 /work/build_rk3588_media_stack.py "$@"
