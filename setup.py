# -*- coding: ISO-8859-1 -*-
# setup.py: the distutils script
#
import os
import subprocess
import json
import sys
import platform
from glob import glob
from setuptools import setup, Extension

# Mapping from Conan architectures to Python machine types
CONAN_ARCHS = {
    "x86_64": ["amd64", "x86_64", "x64"],
    "x86": ["i386", "i686", "x86"],
    "armv8": ["arm64", "aarch64", "aarch64_be", "armv8b", "armv8l"],
    "ppc64le": ["ppc64le", "powerpc"],
    "s390x": ["s390", "s390x"],
}

# define sqlite sources
sources = glob("src/*.c") + ["src/sqlcipher/sqlite3.c"]

library_dirs = []
include_dirs = ["./src"]

# Work around clang raising hard error for unused arguments
if sys.platform == "darwin":
    os.environ["CFLAGS"] = "-Qunused-arguments"

def get_arch() -> str:
    """Get the Conan compilation target architecture.

    If not explicitly set using the `SQLCIPHER3_COMPILE_TARGET` environment variable, this will be
    determined using the host machine"s platform information.
    """
    env_arch = os.getenv("SQLCIPHER3_COMPILE_TARGET", "")
    if env_arch:
        return env_arch

    if (
        platform.architecture()[0] == "32bit"
        and platform.machine().lower() in (CONAN_ARCHS["x86"] + CONAN_ARCHS["x86_64"])
    ):
        return "x86"

    for k, v in CONAN_ARCHS.items():
        if platform.machine().lower() in v:
            return k

    raise RuntimeError("Unable to determine the compilation target architecture")


def install_openssl(arch: str) -> dict:
    """Install openssl using Conan.
    """
    settings: list[str] = []

    if platform.system() == "Windows":
        settings.append("os=Windows")
    elif platform.system() == "Darwin":
        settings.append("os=Macos")
        if arch == "x86_64":
            settings.append("os.version=10.9")
        else:
            settings.append("os.version=11.0")
        settings.append("compiler=apple-clang")
        settings.append("compiler.libcxx=libc++")
    elif platform.system() == "Linux":
        settings.append("os=Linux")

    settings.append(f"arch={arch}")

    build = ["missing"]
    if os.path.isdir("/lib") and any(e.startswith("libc.musl") for e in os.listdir("/lib")):
        # Need to compile openssl if musllinux
        build.append("openssl*")

    subprocess.run(["conan", "profile", "detect"])

    conan_output = os.path.join("conan_output", arch)

    result = subprocess.run([
        "conan", "install", 
        *[x for s in settings for x in ("-s", s)],
        *[x for b in build for x in ("-b", b)],
        "-of", conan_output, "--deployer=direct_deploy", "--format=json", "."
        ], stdout=subprocess.PIPE).stdout.decode()
    conan_info = json.loads(result)

    return conan_info

def add_deps(conan_info: dict, library_dirs: list, include_dirs: list):
    """Find directories of dependencies.
    """
    for dep in conan_info["graph"]["nodes"].values():
        package_folder = dep.get("package_folder")
        if package_folder is None:
            continue

        library_dirs.append(os.path.join(package_folder, "lib"))
        include_dirs.append(os.path.join(package_folder, "include"))

def check_zlib_required(conan_info: dict) -> bool:
    """Check if zlib is required (openssl3)
    """
    for dep in conan_info["graph"]["nodes"].values():
        if dep.get("name") == "zlib":
            return True
    
    return False

def quote_argument(arg):
    q = '\\"' if sys.platform == "win32" and sys.version_info < (3, 7) else '"'
    return q + arg + q

define_macros = [
    ("MODULE_NAME", quote_argument("sqlcipher3.dbapi2")),
    ("ENABLE_FTS3", "1"),
    ("ENABLE_FTS3_PARENTHESIS", "1"),
    ("ENABLE_FTS4", "1"),
    ("ENABLE_FTS5", "1"),
    ("ENABLE_JSON1", "1"),
    ("ENABLE_LOAD_EXTENSION", "1"),
    ("ENABLE_RTREE", "1"),
    ("ENABLE_STAT4", "1"),
    ("ENABLE_UPDATE_DELETE_LIMIT", "1"),
    ("SOUNDEX", "1"),
    ("USE_URI", "1"),
    # Required for SQLCipher.
    ("SQLITE_HAS_CODEC", "1"),
    ("HAS_CODEC", "1"),
    ("SQLITE_TEMP_STORE", "2"),
    # Increase the maximum number of "host parameters".
    ("SQLITE_MAX_VARIABLE_NUMBER", "250000"),
    # Additional nice-to-have.
    ("SQLITE_DEFAULT_PAGE_SIZE", "4096"),
    ("SQLITE_DEFAULT_CACHE_SIZE", "-8000"),
    ("inline", "__inline"),
]

# Configure the compiler
arch = get_arch()
if arch == "universal2":
    conan_info_x64 = install_openssl("x86_64")
    add_deps(conan_info_x64, library_dirs, include_dirs)
    conan_info_arm = install_openssl("armv8")
    add_deps(conan_info_arm, library_dirs, include_dirs)
else:
    conan_info = install_openssl(arch)
    add_deps(conan_info, library_dirs, include_dirs)

# Configure the linker
extra_link_args = []
if sys.platform == "win32":
    # https://github.com/openssl/openssl/blob/master/NOTES-WINDOWS.md#linking-native-applications
    extra_link_args.append("WS2_32.LIB")
    extra_link_args.append("GDI32.LIB")
    extra_link_args.append("ADVAPI32.LIB")
    extra_link_args.append("CRYPT32.LIB")
    extra_link_args.append("USER32.LIB")
    if check_zlib_required(conan_info):
        extra_link_args.append("zlib.lib")
    extra_link_args.append("libcrypto.lib")
else:
    # Include math library, required for fts5, and crypto.
    extra_link_args.extend(["-lm", "-lcrypto"])

module = Extension(
    name="sqlcipher3._sqlite3",
    sources=sources,
    define_macros=define_macros,
    library_dirs=library_dirs,
    include_dirs=include_dirs,
    extra_link_args=extra_link_args,
    language="c",
)

if __name__ == "__main__":
    setup(
        platforms="ALL",
        package_dir={"sqlcipher3": "sqlcipher3"},
        packages=["sqlcipher3"],
        ext_modules=[module],
    )
