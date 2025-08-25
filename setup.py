# -*- coding: ISO-8859-1 -*-
# setup.py: the distutils script
#
import os
import subprocess
import json
import sys
import shutil
import platform
from glob import glob
from setuptools import setup, Extension
from typing import Any, Optional

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

def get_native_arch() -> str:
    for k, v in CONAN_ARCHS.items():
        if platform.machine().lower() in v:
            return k

    # Failover
    return platform.machine().lower()


def get_arch() -> str:
    arch_env = os.getenv("SQLCIPHER3_COMPILE_TARGET")
    if isinstance(arch_env, str):
        arch = arch_env
    else:
        arch = get_native_arch()

    return arch


def install_openssl(arch: str) -> "dict[Any, Any]":
    """Install openssl using Conan.
    """
    settings: list[str] = []
    options: list[str] = []

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
    options.append("openssl/*:no_zlib=True")

    build = ["missing"]
    if os.path.isdir("/lib") and any(e.startswith("libc.musl") for e in os.listdir("/lib")):
        # Need to compile openssl if musllinux
        build.append("openssl*")

    subprocess.run(["conan", "profile", "detect", "-f"])
    # Latest openssl need center2.conan.io instead of center.conan.io
    subprocess.run(["conan", "remote", "update", "conancenter", "--url=https://center2.conan.io"])

    conan_output = os.path.join("conan_output", arch)
    result = subprocess.run([
        "conan", "install", 
        *[x for s in settings for x in ("-s", s)],
        *[x for b in build for x in ("-b", b)],
        *[x for o in options for x in ("-o", o)],
        "-of", conan_output, "--deployer=direct_deploy", "--format=json", "."
        ], stdout=subprocess.PIPE).stdout.decode()
    conan_info = json.loads(result)

    return conan_info

def add_deps(conan_info: "dict[Any, Any]") -> "tuple[list[str], list[str]]":
    """Find directories of dependencies.
    """
    library_dirs: list[str] = []
    include_dirs: list[str] = []
    for dep in conan_info["graph"]["nodes"].values():
        package_folder = dep.get("package_folder")
        if package_folder is None:
            continue

        library_dirs.append(os.path.join(package_folder, "lib"))
        include_dirs.append(os.path.join(package_folder, "include"))
    
    return library_dirs, include_dirs

def quote_argument(arg: str) -> str:
    is_cibuildwheel = os.environ.get("CIBUILDWHEEL", "0") == "1"

    if sys.platform == "win32" and (
        (is_cibuildwheel and sys.version_info < (3, 7))
        or (not is_cibuildwheel and sys.version_info < (3, 9))
    ):
        q = '\\"'
    else:
        q = '"'

    return q + arg + q

if __name__ == "__main__":
    define_macros: "list[tuple[str, Optional[str]]]" = [
        ("MODULE_NAME", quote_argument("sqlcipher3.dbapi2")),
        ("SQLITE_ENABLE_FTS3", "1"),
        ("SQLITE_ENABLE_FTS3_PARENTHESIS", "1"),
        ("SQLITE_ENABLE_FTS4", "1"),
        ("SQLITE_ENABLE_FTS5", "1"),
        ("SQLITE_ENABLE_JSON1", "1"),
        ("SQLITE_ENABLE_LOAD_EXTENSION", "1"),
        ("SQLITE_ENABLE_RTREE", "1"),
        ("SQLITE_ENABLE_STAT4", "1"),
        ("SQLITE_ENABLE_UPDATE_DELETE_LIMIT", "1"),
        ("SQLITE_SOUNDEX", "1"),
        ("SQLITE_USE_URI", "1"),
        # Required for SQLCipher.
        ("SQLITE_HAS_CODEC", "1"),
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
        conan_build_folder_x64: str = conan_info_x64["graph"]["nodes"]["0"]["build_folder"]
        library_dirs_x64, include_dirs_x64 = add_deps(conan_info_x64)
        conan_info_arm = install_openssl("armv8")
        conan_build_folder_arm: str = conan_info_arm["graph"]["nodes"]["0"]["build_folder"]
        library_dirs_arm, include_dirs_arm = add_deps(conan_info_arm)

        if get_native_arch() == "x86_64":
            lipo_dir_merge_src = conan_build_folder_x64
            lipo_dir_merge_dst = conan_build_folder_arm
            library_dirs = library_dirs_x64
            include_dirs = include_dirs_x64
        elif get_native_arch() == "armv8":
            lipo_dir_merge_src = conan_build_folder_arm
            lipo_dir_merge_dst = conan_build_folder_x64
            library_dirs = library_dirs_arm
            include_dirs = include_dirs_arm
        else:
            raise RuntimeError("Invalid arch: " + arch)

        lipo_dir_merge_result = conan_build_folder_arm.replace("armv8", "universal2")
        shutil.rmtree(lipo_dir_merge_result, ignore_errors=True)

        subprocess.run(
            [
                "python3",
                "-m",
                "lipomerge",
                lipo_dir_merge_src,
                lipo_dir_merge_dst,
                lipo_dir_merge_result,
            ]
        )

        shutil.rmtree(lipo_dir_merge_src)
        shutil.move(lipo_dir_merge_result, lipo_dir_merge_src)
    else:
        conan_info = install_openssl(arch)
        library_dirs, include_dirs = add_deps(conan_info)

    extra_compile_args: "list[str]" = ["-Qunused-arguments"] if sys.platform == "darwin" else []

    # Configure the linker
    extra_link_args: "list[str]" = []
    if sys.platform == "win32":
        # https://github.com/openssl/openssl/blob/master/NOTES-WINDOWS.md#linking-native-applications
        extra_link_args.append("WS2_32.LIB")
        extra_link_args.append("GDI32.LIB")
        extra_link_args.append("ADVAPI32.LIB")
        extra_link_args.append("CRYPT32.LIB")
        extra_link_args.append("USER32.LIB")
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
        extra_compile_args=extra_compile_args,
        extra_link_args=extra_link_args,
        language="c",
    )

    setup(
        # With pyproject.toml, all are not necessary except ext_modules
        # However, they are kept for building python 3.6 wheels
        name="sqlcipher3-wheels",
        version="0.5.5.post0",
        package_dir={"sqlcipher3": "sqlcipher3"},
        packages=["sqlcipher3"],
        ext_modules=[module],
    )
