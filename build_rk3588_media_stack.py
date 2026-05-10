#!/usr/bin/env python3
"""
build_rk3588_media_stack.py

Build FFmpeg, mpv and Kodi for RK3588 mainline V4L2 Request / GBM.

This version uses:
  - argparse for CLI options
  - a separate INI config file for repos, refs, patch URL, prefixes and package names
  - upstream-style default install prefix: /usr/local

Default targets:
  deps, ffmpeg, mpv, kodi, joystick

Examples:
  ./build_rk3588_media_stack.py --config rk3588-media-stack.ini all
  ./build_rk3588_media_stack.py ffmpeg mpv
  ./build_rk3588_media_stack.py --no-debs --install-direct all
  ./build_rk3588_media_stack.py --ffmpeg-ref n7.1 ffmpeg
  ./build_rk3588_media_stack.py --kodi-ref master kodi joystick

Notes:
  - FFmpeg V4L2 Request support is patch-based here.
  - mpv V4L2 Request support is branch/PR-based here.
  - "latest" can break. Pin known-good refs in the config or CLI options.
  - Debs produced by this script are local binary packages, not Debian source packages.
"""

from __future__ import annotations

import argparse
import configparser
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


class BuildError(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"\n\033[1;32m==>\033[0m {message}", flush=True)


def warn(message: str) -> None:
    print(f"\n\033[1;33mWARNING:\033[0m {message}", file=sys.stderr, flush=True)


def die(message: str) -> None:
    raise BuildError(message)


def run(
    cmd: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    cmd_s = " ".join(shlex_quote(x) for x in cmd)
    if cwd:
        print(f"+ cd {shlex_quote(str(cwd))} && {cmd_s}", flush=True)
    else:
        print(f"+ {cmd_s}", flush=True)

    return subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        env=env,
        check=check,
        text=True,
    )


def capture(
    cmd: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> str:
    cmd_s = " ".join(shlex_quote(x) for x in cmd)
    if cwd:
        print(f"+ cd {shlex_quote(str(cwd))} && {cmd_s}", flush=True)
    else:
        print(f"+ {cmd_s}", flush=True)

    result = subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        env=env,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return result.stdout


def shlex_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)


def bool_from_config(value: str | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "yes", "true", "on", "y"}


def split_words(value: str | None) -> list[str]:
    if not value:
        return []
    return [x for x in value.replace("\n", " ").split(" ") if x.strip()]


def ensure_cmd(cmd: str) -> None:
    if not shutil.which(cmd):
        die(f"Required command not found: {cmd}")


def sanitize_deb_version(version: str) -> str:
    allowed = []
    for ch in version.strip():
        if ch.isalnum() or ch in ".+:~":
            allowed.append(ch)
        else:
            allowed.append("+")
    out = "".join(allowed).strip("+")
    if out.startswith("v") or out.startswith("n"):
        out = out[1:]
    return out or "0"


@dataclass
class Config:
    build_root: Path
    package_output: Path
    install_prefix: Path
    deb_iteration: str
    deb_maintainer: str
    sudo: str
    jobs: int

    ffmpeg_repo: str
    ffmpeg_ref: str
    ffmpeg_patch_url: str
    ffmpeg_apply_patch: bool
    ffmpeg_package: str

    mpv_repo: str
    mpv_ref: str
    mpv_package: str

    kodi_repo: str
    kodi_ref: str
    kodi_package: str

    joystick_package: str

    build_debs: bool
    install_debs: bool
    install_direct: bool
    build_joystick: bool

    ffmpeg_configure_extra: list[str]
    mpv_meson_extra: list[str]
    kodi_cmake_extra: list[str]


def load_config(path: Path, args: argparse.Namespace) -> Config:
    parser = configparser.ConfigParser()
    read = parser.read(path)
    if not read:
        die(f"Could not read config file: {path}")

    def get(section: str, key: str, fallback: str | None = None) -> str:
        return parser.get(section, key, fallback=fallback)

    def get_bool(section: str, key: str, fallback: bool) -> bool:
        if parser.has_option(section, key):
            return parser.getboolean(section, key)
        return fallback

    def get_int(section: str, key: str, fallback: int) -> int:
        if parser.has_option(section, key):
            return parser.getint(section, key)
        return fallback

    home = Path.home()

    install_prefix = Path(args.install_prefix or get("paths", "install_prefix", "/usr/local")).expanduser()
    build_root = Path(args.build_root or get("paths", "build_root", str(home / "src" / "rk3588-media-stack"))).expanduser()
    package_output = Path(args.package_output or get("paths", "package_output", str(home / "rk3588-media-stack-debs"))).expanduser()

    jobs = args.jobs or get_int("build", "jobs", 0)
    if jobs <= 0:
        jobs = os.cpu_count() or 4

    build_debs = args.debs if args.debs is not None else get_bool("packages", "build_debs", True)
    install_debs = args.install_debs if args.install_debs is not None else get_bool("packages", "install_debs", True)
    install_direct = args.install_direct or get_bool("packages", "install_direct", False)

    if not build_debs and not install_direct:
        warn("Neither deb generation nor direct installation is enabled; enabling direct installation.")
        install_direct = True

    return Config(
        build_root=build_root,
        package_output=package_output,
        install_prefix=install_prefix,
        deb_iteration=args.deb_iteration or get("packages", "deb_iteration", "1"),
        deb_maintainer=args.deb_maintainer or get("packages", "deb_maintainer", "local <root@localhost>"),
        sudo=args.sudo or get("build", "sudo", "sudo"),
        jobs=jobs,

        ffmpeg_repo=get("ffmpeg", "repo"),
        ffmpeg_ref=args.ffmpeg_ref or get("ffmpeg", "ref", "master"),
        ffmpeg_patch_url=args.ffmpeg_patch_url or get("ffmpeg", "v4l2request_patch_url", ""),
        ffmpeg_apply_patch=args.ffmpeg_apply_patch if args.ffmpeg_apply_patch is not None else get_bool("ffmpeg", "apply_patch", True),
        ffmpeg_package=get("ffmpeg", "package_name", "ffmpeg-v4l2request-rockchip"),

        mpv_repo=get("mpv", "repo"),
        mpv_ref=args.mpv_ref or get("mpv", "ref", "v4l2request"),
        mpv_package=get("mpv", "package_name", "mpv-v4l2request-rockchip"),

        kodi_repo=get("kodi", "repo"),
        kodi_ref=args.kodi_ref or get("kodi", "ref", "master"),
        kodi_package=get("kodi", "package_name", "kodi-v4l2request-rockchip"),

        joystick_package=get("kodi", "joystick_package_name", "kodi-v4l2request-peripheral-joystick-rockchip"),

        build_debs=build_debs,
        install_debs=install_debs,
        install_direct=install_direct,
        build_joystick=args.build_joystick if args.build_joystick is not None else get_bool("kodi", "build_joystick_addon", True),

        ffmpeg_configure_extra=split_words(get("ffmpeg", "configure_extra", "")),
        mpv_meson_extra=split_words(get("mpv", "meson_extra", "")),
        kodi_cmake_extra=split_words(get("kodi", "cmake_extra", "")),
    )


def apt_install(config: Config, packages: Iterable[str], *, optional: bool = False) -> None:
    pkgs = list(packages)
    if not pkgs:
        return

    if optional:
        installable = []
        for pkg in pkgs:
            result = subprocess.run(
                ["apt-cache", "show", pkg],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                installable.append(pkg)
            else:
                warn(f"Optional apt package not found: {pkg}")
        pkgs = installable
        if not pkgs:
            return

    run([config.sudo, "apt-get", "install", "-y", *pkgs])


def install_deps(config: Config) -> None:
    log("Installing build dependencies")
    run([config.sudo, "apt-get", "update"])

    required = [
        "build-essential", "git", "curl", "ca-certificates", "pkg-config",
        "cmake", "ninja-build", "meson", "autoconf", "automake", "libtool",
        "gettext", "gawk", "gperf", "zip", "unzip", "python3", "python3-dev",
        "python3-pip", "swig", "default-jre", "ccache", "yasm", "nasm",
        "linux-libc-dev", "libdrm-dev", "libudev-dev", "libgbm-dev",
        "libegl1-mesa-dev", "libgles2-mesa-dev", "libgl1-mesa-dev",
        "libxkbcommon-dev", "libplacebo-dev", "libepoxy-dev", "liblcms2-dev", "libzimg-dev",
        "libharfbuzz-dev", "libfstrcmp-dev", "libmujs-dev", "liblua5.2-dev", "lua5.2",
        "libasound2-dev", "libass-dev", "libbluray-dev",
        "libdvdnav-dev", "libdvdread-dev", "libarchive-dev", "libjpeg-dev",
        "libexiv2-dev", "libuchardet-dev", "zlib1g-dev", "libfontconfig-dev", "libfreetype-dev",
        "libfribidi-dev", "libgif-dev", "liblzo2-dev", "libmicrohttpd-dev",
        "libnfs-dev", "libpcre2-dev", "libplist-dev", "libsqlite3-dev",
        "libssl-dev", "libtag1-dev", "libtinyxml-dev", "libtinyxml2-dev",
        "libxml2-dev", "libxslt1-dev", "uuid-dev", "nlohmann-json3-dev",
        "libfmt-dev", "libspdlog-dev", "flatbuffers-compiler",
        "libflatbuffers-dev", "libinput-dev", "libevdev-dev", "libcec-dev",
        "libcdio-dev", "libcurl4-openssl-dev", "libdbus-1-dev", "liblirc-dev",
        "libshairplay-dev", "libdisplay-info-dev", "rsync",
    ]
    apt_install(config, required)

    optional = [
        "libdav1d-dev",
        "ruby",
        "ruby-dev",
        "rubygems",
    ]
    apt_install(config, optional, optional=True)

    if config.build_debs and not shutil.which("fpm"):
        log("Installing fpm for local .deb generation")
        run([config.sudo, "gem", "install", "--no-document", "fpm"])

    if not pkg_config_exists("libdisplay-info"):
        warn("libdisplay-info was not found by pkg-config. Kodi GBM builds may fail on newer Kodi.")


def pkg_config_exists(name: str, env: dict[str, str] | None = None) -> bool:
    return subprocess.run(["pkg-config", "--exists", name], env=env).returncode == 0


def git_checkout(repo: str, directory: Path, ref: str) -> None:
    directory.parent.mkdir(parents=True, exist_ok=True)
    if not (directory / ".git").exists():
        log(f"Cloning {repo} into {directory}")
        run(["git", "clone", repo, str(directory)])

    run(["git", "fetch", "--all", "--tags", "--prune"], cwd=directory)
    run(["git", "checkout", ref], cwd=directory)
    run(["git", "pull", "--ff-only"], cwd=directory, check=False)


def git_describe(directory: Path) -> str:
    out = capture(["git", "describe", "--tags", "--always"], cwd=directory)
    return sanitize_deb_version(out.strip())


def base_env(config: Config) -> dict[str, str]:
    env = os.environ.copy()
    prefix = str(config.install_prefix)
    env["PKG_CONFIG_PATH"] = f"{prefix}/lib/pkgconfig:{prefix}/lib/aarch64-linux-gnu/pkgconfig:{env.get('PKG_CONFIG_PATH', '')}"
    env["LD_LIBRARY_PATH"] = f"{prefix}/lib:{prefix}/lib/aarch64-linux-gnu:{env.get('LD_LIBRARY_PATH', '')}"
    env["PATH"] = f"{prefix}/bin:{env.get('PATH', '')}"
    return env


def parse_version_parts(version: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", version)]


def version_gte(found: str, minimum: str) -> bool:
    a = parse_version_parts(found)
    b = parse_version_parts(minimum)
    max_len = max(len(a), len(b))
    a.extend([0] * (max_len - len(a)))
    b.extend([0] * (max_len - len(b)))
    return a >= b


def pkg_config_modversion(name: str, env: dict[str, str]) -> str | None:
    result = subprocess.run(
        ["pkg-config", "--modversion", name],
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def ensure_libplacebo(config: Config, minimum: str = "7.360.1") -> None:
    env = base_env(config)
    current = pkg_config_modversion("libplacebo", env)
    if current and version_gte(current, minimum):
        log(f"Using libplacebo {current}")
        return

    if current:
        warn(f"libplacebo {current} is too old, need >= {minimum}; building from source.")
    else:
        warn("libplacebo not found via pkg-config; building from source.")

    build_libplacebo_from_source(config, minimum)

    updated = pkg_config_modversion("libplacebo", base_env(config))
    if not updated or not version_gte(updated, minimum):
        die(f"libplacebo >= {minimum} is required for mpv, found: {updated or 'none'}")


def build_libplacebo_from_source(config: Config, minimum: str) -> None:
    src = config.build_root / "libplacebo"
    build = src / "build"
    prefix = str(config.install_prefix)

    if not (src / ".git").exists():
        log(f"Cloning libplacebo into {src}")
        src.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--recursive", "https://github.com/haasn/libplacebo.git", str(src)])

    run(["git", "fetch", "--all", "--tags", "--prune"], cwd=src)

    tag = f"v{minimum}"
    has_tag = run(["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"], cwd=src, check=False).returncode == 0
    if has_tag:
        run(["git", "checkout", "tags/" + tag], cwd=src)
    else:
        warn(f"Requested libplacebo tag {tag} not found; using latest origin/master.")
        run(["git", "checkout", "master"], cwd=src)
        run(["git", "pull", "--ff-only"], cwd=src, check=False)

    # libplacebo needs 3rdparty submodules (e.g. glad) available at configure time.
    run(["git", "submodule", "sync", "--recursive"], cwd=src)
    run(["git", "submodule", "update", "--init", "--recursive"], cwd=src)

    glad_dir = src / "3rdparty" / "glad"
    if not glad_dir.exists():
        warn("libplacebo submodule glad was not populated; cloning 3rdparty/glad fallback.")
        (src / "3rdparty").mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "https://github.com/Dav1dde/glad.git", str(glad_dir)])

    shutil.rmtree(build, ignore_errors=True)

    env = base_env(config)
    setup_cmd = [
        "meson", "setup", "build",
        f"--prefix={prefix}",
        "-Ddefault_library=shared",
        "-Ddemos=false",
        "-Dvulkan=disabled",
    ]
    result = run(setup_cmd, cwd=src, env=env, check=False)
    if result.returncode != 0:
        warn("libplacebo Meson setup failed with custom options; retrying with minimal options.")
        shutil.rmtree(build, ignore_errors=True)
        run(["meson", "setup", "build", f"--prefix={prefix}"], cwd=src, env=env)

    run(["ninja", "-C", "build", f"-j{config.jobs}"], cwd=src, env=env)
    run(["meson", "install", "-C", "build"], cwd=src, env=env)
    run([config.sudo, "ldconfig"], check=False)


def build_libpostproc_from_source(config: Config, *, stage: Path | None = None) -> str:
    src = config.build_root / "libpostproc"
    prefix = str(config.install_prefix)

    log("Preparing external libpostproc from source")
    git_checkout("https://github.com/michaelni/libpostproc.git", src, "master")
    run(["git", "reset", "--hard"], cwd=src)
    run(["git", "clean", "-xfd"], cwd=src)

    env = base_env(config)
    configure = [
        "./configure",
        f"--prefix={prefix}",
        "--enable-shared",
        "--disable-static",
        "--disable-doc",
        "--disable-programs",
    ]

    run(configure, cwd=src, env=env)
    run(["make", f"-j{config.jobs}"], cwd=src, env=env)
    if stage is None:
        run(["make", "install"], cwd=src, env=env)
        run([config.sudo, "ldconfig"], check=False)
    else:
        run(["make", "install", f"DESTDIR={stage}"], cwd=src, env=env)

    return git_describe(src)


def build_libpostproc(config: Config) -> None:
    stage = config.build_root / "stage" / "libpostproc"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)

    version = build_libpostproc_from_source(config, stage=stage)
    maybe_package_or_install(
        config,
        package="libpostproc-rockchip",
        version=version,
        stage=stage,
        description="External libpostproc plugin package for FFmpeg 8+ based Kodi builds",
        depends=[],
    )


def require_fpm(config: Config) -> None:
    if shutil.which("fpm"):
        return
    log("Installing fpm")
    run([config.sudo, "apt-get", "update"])
    run([config.sudo, "apt-get", "install", "-y", "ruby", "ruby-dev", "rubygems", "build-essential"])
    run([config.sudo, "gem", "install", "--no-document", "fpm"])


def dpkg_arch() -> str:
    return capture(["dpkg", "--print-architecture"]).strip()


def create_deb(
    config: Config,
    *,
    package: str,
    version: str,
    stage: Path,
    description: str,
    depends: list[str],
) -> Path:
    require_fpm(config)
    config.package_output.mkdir(parents=True, exist_ok=True)

    cmd = [
        "fpm",
        "-s", "dir",
        "-t", "deb",
        "-n", package,
        "-v", version,
        "--iteration", config.deb_iteration,
        "-a", dpkg_arch(),
        "--maintainer", config.deb_maintainer,
        "--description", description,
        "--license", "mixed",
        "--deb-no-default-config-files",
        "-C", str(stage),
    ]

    for dep in depends:
        cmd.extend(["--depends", dep])

    cmd.append(".")

    log(f"Creating .deb package {package} {version}")
    run(cmd, cwd=config.package_output)

    candidates = sorted(config.package_output.glob(f"{package}_*.deb"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        die(f"fpm did not create a package for {package}")
    return candidates[-1]


def install_deb(config: Config, deb: Path) -> None:
    log(f"Installing .deb package {deb}")
    result = run([config.sudo, "dpkg", "-i", str(deb)], check=False)
    if result.returncode != 0:
        warn("dpkg reported dependency issues; running apt-get -f install")
        run([config.sudo, "apt-get", "-f", "install", "-y"])
        run([config.sudo, "dpkg", "-i", str(deb)])


def install_stage_direct(config: Config, stage: Path) -> None:
    log(f"Directly installing staged tree from {stage}")
    run([config.sudo, "rsync", "-a", f"{stage}/", "/"])
    run([config.sudo, "ldconfig"])


def maybe_package_or_install(
    config: Config,
    *,
    package: str,
    version: str,
    stage: Path,
    description: str,
    depends: list[str],
) -> None:
    if config.build_debs:
        deb = create_deb(
            config,
            package=package,
            version=version,
            stage=stage,
            description=description,
            depends=depends,
        )
        if config.install_debs:
            install_deb(config, deb)

    if config.install_direct:
        install_stage_direct(config, stage)

    run([config.sudo, "ldconfig"], check=False)


def build_ffmpeg(config: Config) -> None:
    src = config.build_root / "ffmpeg"
    stage = config.build_root / "stage" / "ffmpeg"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)

    git_checkout(config.ffmpeg_repo, src, config.ffmpeg_ref)
    run(["git", "reset", "--hard"], cwd=src)
    run(["git", "clean", "-xfd"], cwd=src)

    if config.ffmpeg_apply_patch:
        if not config.ffmpeg_patch_url:
            die("ffmpeg.apply_patch is enabled but ffmpeg.v4l2request_patch_url is empty.")

        patch_file = config.build_root / "ffmpeg-v4l2request.patch"
        log("Downloading FFmpeg V4L2 Request patch")
        run(["curl", "-L", config.ffmpeg_patch_url, "-o", str(patch_file)])

        log("Applying FFmpeg V4L2 Request patch")
        result = run(["git", "apply", str(patch_file)], cwd=src, check=False)
        if result.returncode != 0:
            die(
                f"FFmpeg patch failed against ref {config.ffmpeg_ref}. "
                "Set ffmpeg.apply_patch = no if your FFmpeg repo already contains v4l2request, "
                "or try a pinned known-good ref such as --ffmpeg-ref n7.1."
            )
    else:
        log("Skipping FFmpeg V4L2 Request patch because ffmpeg.apply_patch = no")

    version = git_describe(src)
    prefix = str(config.install_prefix)

    # Some FFmpeg forks/branches drop or rename configure flags.
    # Probe supported options first to avoid cryptic configure failures.
    configure_help = capture(["./configure", "--help"], cwd=src, check=False)

    optional_flags: list[str] = []
    if "--enable-v4l2-request" in configure_help:
        optional_flags.append("--enable-v4l2-request")
    else:
        warn("FFmpeg configure option --enable-v4l2-request is not supported by this ref; skipping it.")

    if "--enable-postproc" in configure_help:
        optional_flags.append("--enable-postproc")
    else:
        warn("FFmpeg configure option --enable-postproc is not supported by this ref; skipping it.")

    hwaccel_flags: list[str] = []
    if "h264_v4l2request" in configure_help:
        hwaccel_flags.append("--enable-hwaccel=h264_v4l2request")
    else:
        warn("FFmpeg hwaccel h264_v4l2request not listed by configure; skipping explicit enable flag.")

    if "hevc_v4l2request" in configure_help:
        hwaccel_flags.append("--enable-hwaccel=hevc_v4l2request")
    else:
        warn("FFmpeg hwaccel hevc_v4l2request not listed by configure; skipping explicit enable flag.")

    has_v4l2request_code = subprocess.run(
        ["git", "grep", "-q", "v4l2_request", "libavcodec", "libavutil"],
        cwd=src,
        check=False,
    ).returncode == 0
    if not has_v4l2request_code and not hwaccel_flags:
        die(
            f"FFmpeg ref {config.ffmpeg_ref} does not appear to contain V4L2 Request support. "
            "Use a v4l2request-capable FFmpeg ref or enable the external patch."
        )

    configure = [
        "./configure",
        f"--prefix={prefix}",
        "--enable-gpl",
        "--enable-shared",
        "--disable-static",
        "--enable-libdrm",
        *optional_flags,
        "--enable-pthreads",
        "--enable-decoder=h264",
        "--enable-decoder=hevc",
        *hwaccel_flags,
        *config.ffmpeg_configure_extra,
    ]

    log(f"Configuring FFmpeg {version}")
    run(["make", "distclean"], cwd=src, check=False)
    run(configure, cwd=src, env=base_env(config))

    log("Building FFmpeg")
    run(["make", f"-j{config.jobs}"], cwd=src)

    log("Staging FFmpeg install")
    run(["make", "install", f"DESTDIR={stage}"], cwd=src)

    maybe_package_or_install(
        config,
        package=config.ffmpeg_package,
        version=version,
        stage=stage,
        description="FFmpeg with V4L2 Request support for Rockchip/RK3588",
        depends=["libdrm2", "zlib1g"],
    )

    log("Verifying FFmpeg")
    ffmpeg = config.install_prefix / "bin" / "ffmpeg"
    if ffmpeg.exists():
        run([str(ffmpeg), "-hide_banner", "-hwaccels"], env=base_env(config), check=False)
        run([str(ffmpeg), "-hide_banner", "-decoders"], env=base_env(config), check=False)


def build_mpv(config: Config) -> None:
    src = config.build_root / "mpv"
    stage = config.build_root / "stage" / "mpv"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)

    ensure_libplacebo(config, minimum="7.360.1")

    git_checkout(config.mpv_repo, src, config.mpv_ref)
    version = git_describe(src)

    build_dir = src / "build"
    shutil.rmtree(build_dir, ignore_errors=True)

    prefix = str(config.install_prefix)
    env = base_env(config)

    meson_cmd = [
        "meson", "setup", "build",
        f"--prefix={prefix}",
        "-Dv4l2request=enabled",
        "-Ddrm=enabled",
        "-Dgbm=enabled",
        "-Degl-drm=enabled",
        "-Dgl=enabled",
        "-Dwayland=disabled",
        "-Dx11=disabled",
        *config.mpv_meson_extra,
    ]

    log(f"Configuring mpv {version}")
    result = run(meson_cmd, cwd=src, env=env, check=False)
    if result.returncode != 0:
        warn("mpv configure failed with full option set; retrying with reduced option set.")
        shutil.rmtree(build_dir, ignore_errors=True)
        reduced = [
            "meson", "setup", "build",
            f"--prefix={prefix}",
            "-Dv4l2request=enabled",
            "-Ddrm=enabled",
            "-Dgbm=enabled",
            "-Dgl=enabled",
            *config.mpv_meson_extra,
        ]
        run(reduced, cwd=src, env=env)

    log("Building mpv")
    run(["ninja", "-C", "build", f"-j{config.jobs}"], cwd=src, env=env)

    log("Staging mpv install")
    env_stage = env.copy()
    env_stage["DESTDIR"] = str(stage)
    run(["meson", "install", "-C", "build"], cwd=src, env=env_stage)

    maybe_package_or_install(
        config,
        package=config.mpv_package,
        version=version,
        stage=stage,
        description="mpv with V4L2 Request hwdec support for Rockchip/RK3588",
        depends=[config.ffmpeg_package],
    )

    log("Verifying mpv")
    mpv = config.install_prefix / "bin" / "mpv"
    if mpv.exists():
        out = capture([str(mpv), "--hwdec=help"], env=env, check=False)
        print(out)
        if "v4l2request" not in out:
            warn("mpv was built, but --hwdec=help did not show v4l2request.")


def build_kodi(config: Config) -> None:
    src = config.build_root / "kodi"
    build = config.build_root / "kodi-build-gbm"
    stage = config.build_root / "stage" / "kodi"

    shutil.rmtree(build, ignore_errors=True)
    shutil.rmtree(stage, ignore_errors=True)
    build.mkdir(parents=True, exist_ok=True)
    stage.mkdir(parents=True, exist_ok=True)

    git_checkout(config.kodi_repo, src, config.kodi_ref)
    version = git_describe(src)

    build_gtest_from_source(config)

    prefix = str(config.install_prefix)
    env = base_env(config)

    kodi_ffmpeg_extra: list[str] = []
    if not pkg_config_exists("libpostproc", env=env):
        warn("libpostproc not found. Attempting build from michaelni/libpostproc.")
        try:
            build_libpostproc_from_source(config)
        except (BuildError, subprocess.CalledProcessError):
            warn("Failed to build external libpostproc from source.")

        env = base_env(config)
        if not pkg_config_exists("libpostproc", env=env):
            warn(
                "libpostproc is still unavailable; "
                "configuring Kodi with -DDISABLE_FFMPEG_SOURCE_PLUGINS=ON."
            )
            kodi_ffmpeg_extra.append("-DDISABLE_FFMPEG_SOURCE_PLUGINS=ON")

    cmake_cmd = [
        "cmake", str(src),
        f"-DCMAKE_INSTALL_PREFIX={prefix}",
        f"-DCMAKE_PREFIX_PATH={prefix}",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DCORE_PLATFORM_NAME=gbm",
        "-DAPP_RENDER_SYSTEM=gles",
        "-DENABLE_INTERNAL_FFMPEG=OFF",
        f"-DFFMPEG_PATH={prefix}",
        "-DENABLE_INTERNAL_FLATBUFFERS=ON",
        "-DENABLE_ALSA=ON",
        "-DENABLE_PULSEAUDIO=OFF",
        "-DENABLE_PIPEWIRE=OFF",
        "-DENABLE_X11=OFF",
        "-DENABLE_WAYLAND=OFF",
        "-DENABLE_VAAPI=OFF",
        "-DENABLE_VDPAU=OFF",
        *kodi_ffmpeg_extra,
        *config.kodi_cmake_extra,
    ]

    log(f"Configuring Kodi {version}")
    result = run(cmake_cmd, cwd=build, env=env, check=False)
    if result.returncode != 0:
        warn("Kodi configure failed; retrying with internal Exiv2 enabled.")
        shutil.rmtree(build, ignore_errors=True)
        build.mkdir(parents=True, exist_ok=True)
        cmake_cmd_internal = [
            *cmake_cmd,
            "-DENABLE_INTERNAL_EXIV2=ON",
        ]
        run(cmake_cmd_internal, cwd=build, env=env)

    log("Building Kodi")
    run(["cmake", "--build", ".", "--", f"-j{config.jobs}"], cwd=build, env=env)

    log("Staging Kodi install")
    env_stage = env.copy()
    env_stage["DESTDIR"] = str(stage)
    run(["cmake", "--install", "."], cwd=build, env=env_stage)

    maybe_package_or_install(
        config,
        package=config.kodi_package,
        version=version,
        stage=stage,
        description="Kodi GBM/GLES build using FFmpeg V4L2 Request stack for Rockchip/RK3588",
        depends=[config.ffmpeg_package, "libdrm2", "libgbm1", "libegl1", "libgles2", "libasound2"],
    )

    log("Verifying Kodi FFmpeg linkage")
    kodi_bin_candidates = [
        config.install_prefix / "lib" / "kodi" / "kodi.bin",
        config.install_prefix / "bin" / "kodi",
    ]
    for candidate in kodi_bin_candidates:
        if candidate.exists():
            out = capture(["ldd", str(candidate)], check=False)
            for line in out.splitlines():
                if any(x in line for x in ["libavcodec", "libavformat", "libavutil", "libswscale", "libswresample"]):
                    print(line)


def build_gtest_from_source(config: Config) -> None:
    src = config.build_root / "googletest"
    build = config.build_root / "googletest-build"
    prefix = str(config.install_prefix)

    log("Preparing GoogleTest from source")
    git_checkout("https://github.com/google/googletest.git", src, "v1.14.0")

    shutil.rmtree(build, ignore_errors=True)
    build.mkdir(parents=True, exist_ok=True)

    env = base_env(config)
    cmake_cmd = [
        "cmake", str(src),
        f"-DCMAKE_INSTALL_PREFIX={prefix}",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_GMOCK=OFF",
        "-Dgtest_build_tests=OFF",
        "-DINSTALL_GTEST=ON",
    ]

    log("Configuring GoogleTest")
    run(cmake_cmd, cwd=build, env=env)

    log("Building GoogleTest")
    run(["cmake", "--build", ".", "--", f"-j{config.jobs}"], cwd=build, env=env)

    log("Installing GoogleTest")
    run(["cmake", "--install", "."], cwd=build, env=env)


def build_joystick(config: Config) -> None:
    if not config.build_joystick:
        log("Skipping Kodi peripheral.joystick add-on")
        return

    kodi_src = config.build_root / "kodi"
    kodi_build = config.build_root / "kodi-build-gbm"
    addon_build = config.build_root / "kodi-addon-build"
    stage = config.build_root / "stage" / "kodi-joystick"

    if not kodi_src.exists():
        die("Kodi source directory does not exist. Build Kodi first.")
    if not kodi_build.exists():
        die("Kodi build directory does not exist. Build Kodi first.")

    shutil.rmtree(addon_build, ignore_errors=True)
    shutil.rmtree(stage, ignore_errors=True)
    addon_build.mkdir(parents=True, exist_ok=True)
    stage.mkdir(parents=True, exist_ok=True)

    env = base_env(config)

    cmake_cmd = [
        "cmake", str(kodi_src / "cmake" / "addons"),
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DCMAKE_INSTALL_PREFIX={config.install_prefix}",
        "-DPACKAGE_ZIP=OFF",
        "-DADDONS_TO_BUILD=peripheral.joystick",
        "-DCORE_SYSTEM_NAME=linux",
        "-DCORE_PLATFORM_NAME=gbm",
        "-DAPP_RENDER_SYSTEM=gles",
        f"-DKODI_BUILD_DIR={kodi_build}",
        f"-DKODI_SOURCE_DIR={kodi_src}",
    ]

    log("Configuring Kodi peripheral.joystick add-on")
    run(cmake_cmd, cwd=addon_build, env=env)

    log("Building Kodi peripheral.joystick add-on")
    run(["cmake", "--build", ".", "--", f"-j{config.jobs}"], cwd=addon_build, env=env)

    log("Staging Kodi peripheral.joystick add-on")
    env_stage = env.copy()
    env_stage["DESTDIR"] = str(stage)
    run(["cmake", "--install", "."], cwd=addon_build, env=env_stage)

    version = "1." + capture(["date", "+%Y%m%d%H%M"]).strip()

    maybe_package_or_install(
        config,
        package=config.joystick_package,
        version=version,
        stage=stage,
        description="Kodi peripheral.joystick add-on for custom V4L2 Request Kodi build",
        depends=[config.kodi_package],
    )


def print_summary(config: Config) -> None:
    log("Summary")
    print(f"""Install prefix:
  {config.install_prefix}

Build root:
  {config.build_root}

Package output:
  {config.package_output}

Useful checks:
  {config.install_prefix}/bin/ffmpeg -hide_banner -hwaccels
  {config.install_prefix}/bin/mpv --hwdec=help | grep -i v4l2
  ldd {config.install_prefix}/lib/kodi/kodi.bin | grep -E 'avcodec|avformat|avutil'
  {config.install_prefix}/bin/kodi --standalone

mpv GBM/KMS test:
  sudo LD_LIBRARY_PATH={config.install_prefix}/lib \\
  {config.install_prefix}/bin/mpv \\
    --gpu-context=drm \\
    --vo=gpu-next \\
    --drm-connector=HDMI-A-2 \\
    --drm-mode=1 \\
    --hwdec=v4l2request \\
    --gpu-hwdec-interop=v4l2request-overlay \\
    --hwdec-software-fallback=no \\
    /path/to/video.mkv
""")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Build FFmpeg/mpv/Kodi V4L2 Request stack for RK3588.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    ap.add_argument(
        "targets",
        nargs="*",
        default=["all"],
        choices=["deps", "ffmpeg", "libpostproc", "mpv", "kodi", "joystick", "all"],
        help="Build targets to run.",
    )

    ap.add_argument("--config", default="rk3588-media-stack.ini", help="Path to config INI file.")
    ap.add_argument("--build-root", help="Override build root.")
    ap.add_argument("--package-output", help="Override .deb output directory.")
    ap.add_argument("--install-prefix", help="Override install prefix. Default config uses /usr/local.")
    ap.add_argument("-j", "--jobs", type=int, help="Parallel build jobs.")
    ap.add_argument("--sudo", help="sudo command to use.")

    deb_group = ap.add_mutually_exclusive_group()
    deb_group.add_argument("--debs", dest="debs", action="store_true", help="Create .deb packages.")
    deb_group.add_argument("--no-debs", dest="debs", action="store_false", help="Do not create .deb packages.")
    ap.set_defaults(debs=None)

    install_deb_group = ap.add_mutually_exclusive_group()
    install_deb_group.add_argument("--install-debs", dest="install_debs", action="store_true", help="Install generated .deb packages.")
    install_deb_group.add_argument("--no-install-debs", dest="install_debs", action="store_false", help="Do not install generated .deb packages.")
    ap.set_defaults(install_debs=None)

    ap.add_argument("--install-direct", action="store_true", help="Install staged files directly with rsync, in addition to any deb behavior.")
    ap.add_argument("--deb-iteration", help="Debian package iteration/revision.")
    ap.add_argument("--deb-maintainer", help="Debian package maintainer string.")

    ap.add_argument("--ffmpeg-ref", help="Override FFmpeg git ref.")
    ap.add_argument("--ffmpeg-patch-url", help="Override FFmpeg v4l2request patch URL.")

    ffmpeg_patch_group = ap.add_mutually_exclusive_group()
    ffmpeg_patch_group.add_argument("--ffmpeg-apply-patch", dest="ffmpeg_apply_patch", action="store_true", help="Apply external FFmpeg v4l2request patch.")
    ffmpeg_patch_group.add_argument("--ffmpeg-no-patch", dest="ffmpeg_apply_patch", action="store_false", help="Do not apply external FFmpeg v4l2request patch.")
    ap.set_defaults(ffmpeg_apply_patch=None)
    ap.add_argument("--mpv-ref", help="Override mpv git ref.")
    ap.add_argument("--kodi-ref", help="Override Kodi git ref.")

    joy_group = ap.add_mutually_exclusive_group()
    joy_group.add_argument("--build-joystick", dest="build_joystick", action="store_true", help="Build Kodi peripheral.joystick add-on.")
    joy_group.add_argument("--no-joystick", dest="build_joystick", action="store_false", help="Skip Kodi peripheral.joystick add-on.")
    ap.set_defaults(build_joystick=None)

    return ap.parse_args()


def main() -> int:
    args = parse_args()

    try:
        config = load_config(Path(args.config).expanduser(), args)

        for cmd in ["git", "cmake", "make", "pkg-config"]:
            ensure_cmd(cmd)

        config.build_root.mkdir(parents=True, exist_ok=True)
        config.package_output.mkdir(parents=True, exist_ok=True)

        targets = args.targets
        if "all" in targets:
            targets = ["deps", "ffmpeg", "libpostproc", "mpv", "kodi", "joystick"]

        for target in targets:
            if target == "deps":
                install_deps(config)
            elif target == "ffmpeg":
                build_ffmpeg(config)
            elif target == "libpostproc":
                build_libpostproc(config)
            elif target == "mpv":
                build_mpv(config)
            elif target == "kodi":
                build_kodi(config)
            elif target == "joystick":
                build_joystick(config)

        print_summary(config)
        return 0

    except BuildError as exc:
        print(f"\n\033[1;31mERROR:\033[0m {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"\n\033[1;31mCOMMAND FAILED:\033[0m return code {exc.returncode}", file=sys.stderr)
        return exc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
