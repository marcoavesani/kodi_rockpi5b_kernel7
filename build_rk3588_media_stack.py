#!/usr/bin/env python3
"""
build_rk3588_media_stack.py

Build FFmpeg, mpv and Kodi for RK3588 mainline V4L2 Request / GBM.

This version uses:
  - argparse for CLI options
  - a separate INI config file for repos, refs, patch list, prefixes and package names
  - upstream-style default install prefix: /usr/local

Default targets:
    deps, ffmpeg, libplacebo, mpv, kodi, joystick

Examples:
  ./build_rk3588_media_stack.py --config rk3588-media-stack.ini all
  ./build_rk3588_media_stack.py ffmpeg mpv
  ./build_rk3588_media_stack.py --no-debs --install-direct all
  ./build_rk3588_media_stack.py --ffmpeg-ref n9.0.2 ffmpeg
  ./build_rk3588_media_stack.py --kodi-ref master kodi joystick

Notes:
  - FFmpeg is upstream plus a pinned list of LibreELEC patch files
    (V4L2 Request, libpostproc, RK3588 HEVC), applied with GNU patch.
  - mpv is upstream plus the V4L2 Request hwdec commits of mpv PR #14690.
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
class PatchSet:
    """Patch files applied in order on top of an upstream checkout."""
    section: str
    enabled: bool
    base_url: str
    commit: str
    patches: list[str]
    extra_patches: list[str]

    def sources(self) -> list[tuple[str, str]]:
        """Return (name, location) for every patch, in apply order."""
        sources: list[tuple[str, str]] = []
        if self.patches:
            if not self.base_url:
                die(f"{self.section}.patches is set but {self.section}.patch_base_url is empty.")
            if "{commit}" in self.base_url and not self.commit:
                die(f"{self.section}.patch_base_url uses {{commit}} but {self.section}.patch_commit is empty.")
            base = self.base_url.format(commit=self.commit).rstrip("/")
            for rel in self.patches:
                sources.append((rel, f"{base}/{rel.lstrip('/')}"))
        for extra in self.extra_patches:
            sources.append((extra.rsplit("/", 1)[-1], extra))
        return sources


@dataclass
class Config:
    build_root: Path
    package_output: Path
    install_prefix: Path
    deb_iteration: str
    deb_maintainer: str
    sudo: list[str]
    jobs: int

    ffmpeg_repo: str
    ffmpeg_ref: str
    ffmpeg_patchset: PatchSet
    ffmpeg_min_kernel_headers: str
    ffmpeg_package: str

    mpv_repo: str
    mpv_ref: str
    mpv_patchset: PatchSet
    mpv_package: str
    libplacebo_package: str

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

    # No sudo needed (and often not usable) when already running as root, e.g. in a container.
    sudo = [] if os.geteuid() == 0 else split_words(args.sudo or get("build", "sudo", "sudo"))

    # Local patch paths in the config are relative to the config file.
    config_dir = path.resolve().parent

    def resolve_local(entry: str) -> str:
        if "://" in entry:
            return entry
        return str((config_dir / entry).expanduser())

    def get_patchset(section: str, apply_override: bool | None, commit_override: str | None) -> PatchSet:
        patches = split_words(get(section, "patches", ""))
        extra_patches = [resolve_local(x) for x in split_words(get(section, "extra_patches", ""))]
        has_patches = bool(patches or extra_patches)
        return PatchSet(
            section=section,
            enabled=apply_override if apply_override is not None else get_bool(section, "apply_patch", has_patches),
            base_url=get(section, "patch_base_url", ""),
            commit=commit_override or get(section, "patch_commit", ""),
            patches=patches,
            extra_patches=extra_patches,
        )

    return Config(
        build_root=build_root,
        package_output=package_output,
        install_prefix=install_prefix,
        deb_iteration=args.deb_iteration or get("packages", "deb_iteration", "1"),
        deb_maintainer=args.deb_maintainer or get("packages", "deb_maintainer", "local <root@localhost>"),
        sudo=sudo,
        jobs=jobs,

        ffmpeg_repo=get("ffmpeg", "repo"),
        ffmpeg_ref=args.ffmpeg_ref or get("ffmpeg", "ref", "n9.0.2"),
        ffmpeg_patchset=get_patchset("ffmpeg", args.ffmpeg_apply_patch, args.ffmpeg_patch_commit),
        ffmpeg_min_kernel_headers=get("ffmpeg", "min_kernel_headers", ""),
        ffmpeg_package=get("ffmpeg", "package_name", "ffmpeg-v4l2request-rockchip"),

        mpv_repo=get("mpv", "repo"),
        mpv_ref=args.mpv_ref or get("mpv", "ref", "v0.41.0"),
        mpv_patchset=get_patchset("mpv", args.mpv_apply_patch, None),
        mpv_package=get("mpv", "package_name", "mpv-v4l2request-rockchip"),
        libplacebo_package=get("mpv", "libplacebo_package", "libplacebo-rockchip"),

        kodi_repo=get("kodi", "repo"),
        kodi_ref=args.kodi_ref or get("kodi", "ref", "22.0rc1-Piers"),
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

    run([*config.sudo, "apt-get", "install", "-y", *pkgs])


def install_deps(config: Config) -> None:
    log("Installing build dependencies")
    run([*config.sudo, "apt-get", "update"])

    required = [
        "build-essential", "patch", "git", "curl", "ca-certificates", "pkg-config",
        "cmake", "ninja-build", "meson", "autoconf", "automake", "libtool",
        "gettext", "gawk", "gperf", "zip", "unzip", "python3", "python3-dev",
        "python3-pip", "swig", "bison", "default-jre", "ccache", "yasm", "nasm",
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
        "libwayland-dev", "libwayland-bin", "wayland-protocols",
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
        run([*config.sudo, "gem", "install", "--no-document", "fpm"])

    if not pkg_config_exists("libdisplay-info"):
        warn("libdisplay-info was not found by pkg-config. Kodi GBM builds may fail on newer Kodi.")

    if config.ffmpeg_min_kernel_headers and not kernel_headers_at_least(config.ffmpeg_min_kernel_headers):
        warn(
            f"Linux UAPI headers are older than {config.ffmpeg_min_kernel_headers}. On Debian 13 install them with:\n"
            "  echo 'deb http://deb.debian.org/debian trixie-backports main' | "
            "sudo tee /etc/apt/sources.list.d/trixie-backports.list\n"
            "  sudo apt-get update && sudo apt-get install -t trixie-backports linux-libc-dev"
        )


def pkg_config_exists(name: str, env: dict[str, str] | None = None) -> bool:
    return subprocess.run(["pkg-config", "--exists", name], env=env).returncode == 0


def git_checkout(repo: str, directory: Path, ref: str) -> None:
    directory.parent.mkdir(parents=True, exist_ok=True)
    if not (directory / ".git").exists():
        log(f"Cloning {repo} into {directory}")
        # Blob-less partial clone: full history for git describe, file contents fetched on demand.
        run(["git", "clone", "--filter=blob:none", repo, str(directory)])

    run(["git", "fetch", "--all", "--tags", "--prune", "--force"], cwd=directory)
    run(["git", "checkout", ref], cwd=directory)
    on_branch = run(["git", "symbolic-ref", "-q", "HEAD"], cwd=directory, check=False).returncode == 0
    if on_branch:
        run(["git", "pull", "--ff-only"], cwd=directory, check=False)


def git_describe(directory: Path) -> str:
    out = capture(["git", "describe", "--tags", "--always"], cwd=directory)
    return sanitize_deb_version(out.strip())


def multiarch_triplet() -> str:
    # Meson installs into lib/<triplet> (e.g. aarch64-linux-gnu) on Debian.
    for cmd in (["dpkg-architecture", "-qDEB_HOST_MULTIARCH"], ["gcc", "-dumpmachine"]):
        if shutil.which(cmd[0]):
            result = subprocess.run(cmd, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
    return "aarch64-linux-gnu"


def base_env(config: Config) -> dict[str, str]:
    env = os.environ.copy()
    prefix = str(config.install_prefix)
    triplet = multiarch_triplet()
    env["PKG_CONFIG_PATH"] = f"{prefix}/lib/pkgconfig:{prefix}/lib/{triplet}/pkgconfig:{env.get('PKG_CONFIG_PATH', '')}"
    env["LD_LIBRARY_PATH"] = f"{prefix}/lib:{prefix}/lib/{triplet}:{env.get('LD_LIBRARY_PATH', '')}"
    env["PATH"] = f"{prefix}/bin:{env.get('PATH', '')}"
    return env


def kernel_headers_version() -> str | None:
    version_h = Path("/usr/include/linux/version.h")
    if not version_h.exists():
        return None
    match = re.search(r"#define\s+LINUX_VERSION_CODE\s+(\d+)", version_h.read_text())
    if not match:
        return None
    code = int(match.group(1))
    return f"{code >> 16}.{(code >> 8) & 0xFF}.{code & 0xFF}"


def kernel_headers_at_least(minimum: str) -> bool:
    found = kernel_headers_version()
    return found is not None and version_gte(found, minimum)


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

    if config.build_debs:
        build_libplacebo(config, minimum)
    else:
        build_libplacebo_from_source(config, minimum)

    updated = pkg_config_modversion("libplacebo", base_env(config))
    if not updated or not version_gte(updated, minimum):
        die(f"libplacebo >= {minimum} is required for mpv, found: {updated or 'none'}")


def build_libplacebo_from_source(config: Config, minimum: str, *, stage: Path | None = None) -> str:
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
    if stage is None:
        run(["meson", "install", "-C", "build"], cwd=src, env=env)
        run([*config.sudo, "ldconfig"], check=False)
    else:
        env_stage = env.copy()
        env_stage["DESTDIR"] = str(stage)
        run(["meson", "install", "-C", "build"], cwd=src, env=env_stage)

    return git_describe(src)


def build_libplacebo(config: Config, minimum: str = "7.360.1") -> None:
    stage = config.build_root / "stage" / "libplacebo"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)

    version = build_libplacebo_from_source(config, minimum, stage=stage)
    maybe_package_or_install(
        config,
        package=config.libplacebo_package,
        version=version,
        stage=stage,
        description="External libplacebo package for mpv and Kodi builds",
        depends=[],
    )


def require_fpm(config: Config) -> None:
    if shutil.which("fpm"):
        return
    log("Installing fpm")
    run([*config.sudo, "apt-get", "update"])
    run([*config.sudo, "apt-get", "install", "-y", "ruby", "ruby-dev", "rubygems", "build-essential"])
    run([*config.sudo, "gem", "install", "--no-document", "fpm"])


def dpkg_arch() -> str:
    return capture(["dpkg", "--print-architecture"]).strip()


def installed_deb_version(package: str) -> str | None:
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${db:Status-Abbrev} ${Version}", package],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    status, _, version = result.stdout.strip().partition(" ")
    if result.returncode != 0 or not status.startswith("ii") or not version:
        return None
    return version


def exact_dependency(package: str) -> str:
    """Depend on the exact build of one of our packages that this one was linked against.

    The package names stay the same across FFmpeg/Kodi releases, so an unversioned
    dependency is also satisfied by an older build with different sonames."""
    version = installed_deb_version(package)
    return f"{package} (= {version})" if version else package


def runtime_env() -> dict[str, str]:
    """Environment of an installed binary on the device: no LD_LIBRARY_PATH."""
    env = os.environ.copy()
    env.pop("LD_LIBRARY_PATH", None)
    return env


def check_runtime_libs(binary: Path) -> None:
    """Fail if an installed binary cannot resolve its libraries the way it would on the device."""
    out = capture(["ldd", str(binary)], env=runtime_env(), check=False)
    missing = [line.strip() for line in out.splitlines() if "not found" in line]
    if missing:
        die(
            f"{binary} cannot load its libraries without LD_LIBRARY_PATH "
            "(is the install prefix in /etc/ld.so.conf.d and the ldconfig cache up to date?):\n  "
            + "\n  ".join(missing)
        )


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
        "--force",  # replace a package left by an earlier run of the same version
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
        # Refresh the dynamic linker cache on install/remove, like Debian library packages
        # (dh_makeshlibs) do. Without it, new sonames in /usr/local/lib stay unresolvable
        # until someone runs ldconfig by hand.
        "--deb-activate-noawait", "ldconfig",
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
    result = run([*config.sudo, "dpkg", "-i", str(deb)], check=False)
    if result.returncode != 0:
        warn("dpkg reported dependency issues; running apt-get -f install")
        run([*config.sudo, "apt-get", "-f", "install", "-y"])
        run([*config.sudo, "dpkg", "-i", str(deb)])


def install_stage_direct(config: Config, stage: Path) -> None:
    log(f"Directly installing staged tree from {stage}")
    run([*config.sudo, "rsync", "-a", f"{stage}/", "/"])
    run([*config.sudo, "ldconfig"])


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


def git_reset_tree(src: Path) -> None:
    """Discard local changes (e.g. patches applied by an earlier run)."""
    if (src / ".git").exists():
        run(["git", "reset", "--hard"], cwd=src)
        run(["git", "clean", "-xfd"], cwd=src)


def fetch_patch(dest_dir: Path, name: str, location: str) -> Path:
    if "://" not in location:
        local = Path(location)
        if not local.is_file():
            die(f"Patch file not found: {local}")
        return local

    dest = dest_dir / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(["curl", "-fsSL", "--retry", "3", "-o", str(dest), location])
    return dest


def apply_patches(config: Config, patchset: PatchSet, src: Path, ref: str) -> None:
    section = patchset.section
    sources = patchset.sources()
    if not sources:
        die(f"{section}.apply_patch is enabled but no patches are configured ({section}.patches / {section}.extra_patches).")

    if patchset.commit and not re.fullmatch(r"[0-9a-f]{40}", patchset.commit):
        warn(
            f"{section}.patch_commit = {patchset.commit} is not a full commit SHA; "
            "builds are not reproducible if that ref moves."
        )

    ensure_cmd("patch")
    patch_dir = config.build_root / "patches" / section
    shutil.rmtree(patch_dir, ignore_errors=True)
    for name, location in sources:
        patch_file = fetch_patch(patch_dir, name, location)
        log(f"Applying {section} patch {name}")
        # LibreELEC applies its patches with GNU patch, which tolerates the small context drift of
        # point releases (offset/fuzz); git apply does not. No --dry-run: a series may create
        # files and patch them again later. The tree is reset on the next run anyway.
        result = run(
            ["patch", "-p1", "--forward", "--batch", "--no-backup-if-mismatch", "-i", str(patch_file)],
            cwd=src,
            check=False,
        )
        rejects = sorted(str(p.relative_to(src)) for p in src.rglob("*.rej"))
        if result.returncode != 0 or rejects:
            die(
                f"{section} patch {name} does not apply to {ref}"
                + (f" (rejects: {', '.join(rejects)})" if rejects else "")
                + f". Use the {section} ref the patches were made for, or update the {section} patch list."
            )


def enabled_ffmpeg_components(src: Path, suffix: str) -> list[str]:
    header = src / "config_components.h"
    if not header.exists():
        return []
    pattern = re.compile(rf"^#define CONFIG_(\w+)_{suffix} 1$", re.MULTILINE)
    return sorted(m.lower() for m in pattern.findall(header.read_text()))


def build_ffmpeg(config: Config) -> None:
    src = config.build_root / "ffmpeg"
    stage = config.build_root / "stage" / "ffmpeg"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)

    git_reset_tree(src)
    git_checkout(config.ffmpeg_repo, src, config.ffmpeg_ref)
    git_reset_tree(src)
    version = git_describe(src)

    if config.ffmpeg_patchset.enabled:
        if config.ffmpeg_min_kernel_headers and not kernel_headers_at_least(config.ffmpeg_min_kernel_headers):
            die(
                f"FFmpeg patches need Linux UAPI headers >= {config.ffmpeg_min_kernel_headers} "
                f"(found {kernel_headers_version() or 'none'} in /usr/include/linux/version.h). "
                "On Debian 13 install linux-libc-dev from trixie-backports."
            )
        apply_patches(config, config.ffmpeg_patchset, src, config.ffmpeg_ref)
    else:
        log("Skipping FFmpeg patches because ffmpeg.apply_patch = no")

    prefix = str(config.install_prefix)

    # Autodetected options are listed as --disable-*, others as --enable-*.
    configure_help = capture(["./configure", "--help"], cwd=src, check=False)

    def has_option(name: str) -> bool:
        return re.search(rf"--(enable|disable)-{re.escape(name)}\b", configure_help) is not None

    if not has_option("v4l2-request"):
        die(
            f"FFmpeg ref {config.ffmpeg_ref} has no v4l2-request configure option. "
            "Enable ffmpeg.apply_patch or use an FFmpeg tree that contains V4L2 Request support."
        )

    optional_flags: list[str] = []
    if has_option("postproc"):
        optional_flags.append("--enable-postproc")
    else:
        warn("FFmpeg has no libpostproc (add the LibreELEC postproc patch); Kodi will build without it.")

    configure = [
        "./configure",
        f"--prefix={prefix}",
        "--enable-gpl",
        "--enable-shared",
        "--disable-static",
        "--enable-libdrm",
        "--enable-libudev",
        "--enable-v4l2-request",
        *optional_flags,
        "--enable-pthreads",
        *config.ffmpeg_configure_extra,
    ]

    log(f"Configuring FFmpeg {version}")
    run(["make", "distclean"], cwd=src, check=False)
    run(configure, cwd=src, env=base_env(config))

    # configure silently drops hwaccels whose dependencies are missing, so check what it enabled.
    hwaccels = [x for x in enabled_ffmpeg_components(src, "HWACCEL") if x.endswith("_v4l2request")]
    log(f"Enabled V4L2 Request hwaccels: {', '.join(hwaccels) or 'none'}")
    missing = [x for x in ("h264_v4l2request", "hevc_v4l2request") if x not in hwaccels]
    if missing:
        die(f"FFmpeg configure did not enable {', '.join(missing)}; check config.log for missing dependencies.")

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
        depends=["libdrm2", "libudev1", "zlib1g"],
    )

    log("Verifying FFmpeg")
    ffmpeg = config.install_prefix / "bin" / "ffmpeg"
    if ffmpeg.exists():
        check_runtime_libs(ffmpeg)
        out = capture([str(ffmpeg), "-hide_banner", "-hwaccels"], env=runtime_env())
        print(out)
        if "v4l2request" not in out.split():
            die("Installed ffmpeg does not list the v4l2request hwaccel.")
        out = capture([str(ffmpeg), "-hide_banner", "-decoders"], env=runtime_env())
        for codec in ("h264", "hevc", "vp9", "av1"):
            if not re.search(rf"^\s*V\S*\s+{codec}\s", out, re.MULTILINE):
                die(f"Installed ffmpeg does not list the {codec} decoder.")


def build_mpv(config: Config) -> None:
    src = config.build_root / "mpv"
    stage = config.build_root / "stage" / "mpv"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)

    ensure_libplacebo(config, minimum="7.360.1")

    git_reset_tree(src)
    git_checkout(config.mpv_repo, src, config.mpv_ref)
    git_reset_tree(src)
    version = git_describe(src)

    if config.mpv_patchset.enabled:
        apply_patches(config, config.mpv_patchset, src, config.mpv_ref)
    else:
        log("Skipping mpv patches because mpv.apply_patch = no")

    build_dir = src / "build"
    shutil.rmtree(build_dir, ignore_errors=True)

    prefix = str(config.install_prefix)
    env = base_env(config)

    # FFmpeg's V4L2 Request hwaccels use their own hwdevice type. Upstream mpv only has
    # DRM PRIME interops, so --hwdec=v4l2request comes from the patch (mpv PR #14690),
    # which adds the 'v4l2request' Meson option.
    option_files = [src / "meson.options", src / "meson_options.txt"]
    meson_options = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in option_files if p.exists())
    has_v4l2request = re.search(r"option\(\s*'v4l2request'", meson_options) is not None
    if not has_v4l2request:
        warn("mpv has no v4l2request option; only the slow --hwdec=v4l2request-copy path will work.")

    meson_cmd = [
        "meson", "setup", "build",
        f"--prefix={prefix}",
        "-Ddrm=enabled",
        "-Dgbm=enabled",
        "-Degl=enabled",
        "-Degl-drm=enabled",
        "-Dgl=enabled",
        "-Dwayland=enabled",
        "-Dx11=disabled",
        *(["-Dv4l2request=enabled"] if has_v4l2request else []),
        *config.mpv_meson_extra,
    ]

    log(f"Configuring mpv {version}")
    run(meson_cmd, cwd=src, env=env)

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
        depends=[exact_dependency(config.ffmpeg_package), exact_dependency(config.libplacebo_package)],
    )

    log("Verifying mpv")
    mpv = config.install_prefix / "bin" / "mpv"
    if mpv.exists():
        check_runtime_libs(mpv)
        out = capture([str(mpv), "--hwdec=help"], env=runtime_env())
        print(out)
        # Lines look like "  v4l2request (h264-v4l2request)".
        hwdecs = {line.split()[0] for line in out.splitlines() if line.startswith("  ") and line.strip()}
        if "v4l2request" not in hwdecs:
            die("mpv was built, but --hwdec=help does not list the v4l2request hwdec.")
        if has_v4l2request:
            out = capture([str(mpv), "--gpu-hwdec-interop=help"], env=runtime_env())
            print(out)
            if "v4l2request-overlay" not in out.split():
                die("mpv was built, but --gpu-hwdec-interop=help does not list v4l2request-overlay.")


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

    prefix = str(config.install_prefix)
    env = base_env(config)

    if not pkg_config_exists("libavcodec", env=env):
        die(f"FFmpeg is not installed under {prefix}. Build/install the ffmpeg target first.")

    # libpostproc is provided by the FFmpeg package (LibreELEC postproc patch).
    kodi_ffmpeg_extra: list[str] = []
    if not pkg_config_exists("libpostproc", env=env):
        warn("libpostproc not found; configuring Kodi with -DDISABLE_FFMPEG_SOURCE_PLUGINS=ON.")
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
        # Kodi 22 needs SWIG >= 4.5 (Debian 13 has 4.3); this builds Kodi's pinned upstream SWIG.
        "-DENABLE_INTERNAL_SWIG=ON",
        "-DENABLE_ALSA=ON",
        "-DENABLE_PULSEAUDIO=OFF",
        "-DENABLE_PIPEWIRE=OFF",
        "-DENABLE_X11=OFF",
        "-DENABLE_WAYLAND=OFF",
        "-DENABLE_VAAPI=OFF",
        "-DENABLE_VDPAU=OFF",
        "-DENABLE_TESTING=OFF",
        *kodi_ffmpeg_extra,
        *config.kodi_cmake_extra,
    ]

    log(f"Configuring Kodi {version}")
    run(cmake_cmd, cwd=build, env=env)

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
        depends=[exact_dependency(config.ffmpeg_package), "libdrm2", "libgbm1", "libegl1", "libgles2", "libasound2"],
    )

    log("Verifying Kodi FFmpeg linkage")
    # bin/kodi is a wrapper script; the GBM binary is lib/kodi/kodi-gbm.
    kodi_bin = config.install_prefix / "lib" / "kodi" / "kodi-gbm"
    if (config.build_debs and config.install_debs) or config.install_direct:
        if not kodi_bin.exists():
            die(f"Kodi binary not found at {kodi_bin}.")
        check_runtime_libs(kodi_bin)
        out = capture(["ldd", str(kodi_bin)], env=runtime_env())
        libs = [line.strip() for line in out.splitlines() if re.search(r"lib(av|sw|postproc)", line)]
        print("\n".join(libs))
        avcodec = next((line for line in libs if line.startswith("libavcodec")), "")
        if str(config.install_prefix) not in avcodec:
            die(f"Kodi does not link the libavcodec from {config.install_prefix}: {avcodec or 'not linked'}")


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

    # Kodi's add-on build installs each add-on during the build step into
    # CMAKE_INSTALL_PREFIX, so point that at the staging tree. OVERRIDE_PATHS keeps
    # it from switching to Kodi's build-local depends directory instead.
    stage_prefix = stage / str(config.install_prefix).lstrip("/")
    cmake_cmd = [
        "cmake", str(kodi_src / "cmake" / "addons"),
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DCMAKE_INSTALL_PREFIX={stage_prefix}",
        "-DOVERRIDE_PATHS=ON",
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

    log("Building and staging Kodi peripheral.joystick add-on")
    run(["cmake", "--build", ".", "--", f"-j{config.jobs}"], cwd=addon_build, env=env)

    addon_xml = stage_prefix / "share" / "kodi" / "addons" / "peripheral.joystick" / "addon.xml"
    addon_libs = list((stage_prefix / "lib" / "kodi" / "addons" / "peripheral.joystick").glob("peripheral.joystick.so*"))
    if not addon_xml.exists() or not addon_libs:
        die(f"peripheral.joystick was not staged under {stage_prefix}.")

    match = re.search(r'<addon[^>]*\sversion="([^"]+)"', addon_xml.read_text(encoding="utf-8"))
    version = match.group(1) if match else "1." + capture(["date", "+%Y%m%d%H%M"]).strip()

    maybe_package_or_install(
        config,
        package=config.joystick_package,
        version=version,
        stage=stage,
        description="Kodi peripheral.joystick add-on for custom V4L2 Request Kodi build",
        depends=[exact_dependency(config.kodi_package)],
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
  {config.install_prefix}/bin/mpv --hwdec=help | grep -i v4l2request
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
        choices=["deps", "ffmpeg", "libplacebo", "mpv", "kodi", "joystick", "all"],
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
    ap.add_argument("--ffmpeg-patch-commit", help="Override the commit/ref that FFmpeg patch files are taken from.")

    ffmpeg_patch_group = ap.add_mutually_exclusive_group()
    ffmpeg_patch_group.add_argument("--ffmpeg-apply-patch", dest="ffmpeg_apply_patch", action="store_true", help="Apply the configured FFmpeg patch files.")
    ffmpeg_patch_group.add_argument("--ffmpeg-no-patch", dest="ffmpeg_apply_patch", action="store_false", help="Do not apply FFmpeg patch files.")
    ap.set_defaults(ffmpeg_apply_patch=None)
    ap.add_argument("--mpv-ref", help="Override mpv git ref.")

    mpv_patch_group = ap.add_mutually_exclusive_group()
    mpv_patch_group.add_argument("--mpv-apply-patch", dest="mpv_apply_patch", action="store_true", help="Apply the configured mpv patch files.")
    mpv_patch_group.add_argument("--mpv-no-patch", dest="mpv_apply_patch", action="store_false", help="Do not apply mpv patch files.")
    ap.set_defaults(mpv_apply_patch=None)
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

        if not config.sudo:
            # sudo would use its secure_path; without it, dpkg still needs ldconfig etc. from sbin.
            path = os.environ.get("PATH", "").split(os.pathsep)
            missing = [d for d in ("/usr/local/sbin", "/usr/sbin", "/sbin") if d not in path]
            os.environ["PATH"] = os.pathsep.join([*path, *missing])

        for cmd in ["git", "cmake", "make", "pkg-config"]:
            ensure_cmd(cmd)

        config.build_root.mkdir(parents=True, exist_ok=True)
        config.package_output.mkdir(parents=True, exist_ok=True)

        targets = args.targets
        if "all" in targets:
            targets = ["deps", "ffmpeg", "libplacebo", "mpv", "kodi", "joystick"]

        for target in targets:
            if target == "deps":
                install_deps(config)
            elif target == "ffmpeg":
                build_ffmpeg(config)
            elif target == "libplacebo":
                build_libplacebo(config)
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
