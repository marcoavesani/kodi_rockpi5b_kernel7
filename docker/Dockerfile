# syntax=docker/dockerfile:1.7
#
# RK3588 media stack builder image.
#
# Default base is Debian 13 "trixie" to match Debian Trixie-based target systems.
# Override with:
#   docker build --build-arg BASE_IMAGE=ubuntu:24.04 ...
#   docker build --build-arg BASE_IMAGE=debian:bookworm ...
#
ARG BASE_IMAGE=debian:trixie
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC
ENV CCACHE_DIR=/ccache
ENV PATH=/usr/lib/ccache:/usr/local/bin:/usr/bin:/bin

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update && apt-get install -y --no-install-recommends \
    sudo \
    ca-certificates \
    curl \
    git \
    rsync \
    pkg-config \
    build-essential \
    cmake \
    ninja-build \
    meson \
    autoconf \
    automake \
    libtool \
    gettext \
    gawk \
    gperf \
    zip \
    unzip \
    python3 \
    python3-dev \
    python3-pip \
    swig \
    default-jre \
    ccache \
    yasm \
    nasm \
    linux-libc-dev \
    ruby \
    ruby-dev \
    rubygems \
    libdrm-dev \
    libudev-dev \
    libgbm-dev \
    libegl1-mesa-dev \
    libgles2-mesa-dev \
    libgl1-mesa-dev \
    libxkbcommon-dev \
    libasound2-dev \
    libass-dev \
    libbluray-dev \
    libdvdnav-dev \
    libdvdread-dev \
    libarchive-dev \
    libjpeg-dev \
    libuchardet-dev \
    zlib1g-dev \
    libfontconfig-dev \
    libfreetype-dev \
    libfribidi-dev \
    libgif-dev \
    liblzo2-dev \
    libmicrohttpd-dev \
    libnfs-dev \
    libpcre2-dev \
    libplist-dev \
    libsqlite3-dev \
    libssl-dev \
    libtag1-dev \
    libtinyxml-dev \
    libtinyxml2-dev \
    libxml2-dev \
    libxslt1-dev \
    uuid-dev \
    nlohmann-json3-dev \
    libfmt-dev \
    libspdlog-dev \
    flatbuffers-compiler \
    libflatbuffers-dev \
    libinput-dev \
    libevdev-dev \
    libcec-dev \
    libcdio-dev \
    libcurl4-openssl-dev \
    libdbus-1-dev \
    liblirc-dev \
    libshairplay-dev \
    libdisplay-info-dev \
    libdav1d-dev \
    && gem install --no-document fpm \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /work /build /output /ccache

COPY build_rk3588_media_stack.py /usr/local/src/rk3588-media-stack/build_rk3588_media_stack.py
COPY rk3588-media-stack.ini /usr/local/src/rk3588-media-stack/rk3588-media-stack.ini
COPY rk3588-media-stack.ci.ini /usr/local/src/rk3588-media-stack/rk3588-media-stack.ci.ini
COPY docker/entrypoint.sh /usr/local/bin/rk3588-build-entrypoint

RUN chmod +x /usr/local/src/rk3588-media-stack/build_rk3588_media_stack.py \
    /usr/local/bin/rk3588-build-entrypoint

WORKDIR /work
ENTRYPOINT ["/usr/local/bin/rk3588-build-entrypoint"]
CMD ["--config", "/work/rk3588-media-stack.ci.ini", "ffmpeg", "mpv", "kodi", "joystick"]
