#!/usr/bin/env python3
"""Pinned runtime builds and native/KVM process validation."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib

HERE = Path(__file__).resolve().parent


def version_directory():
    major = os.environ.get("HL_PG_VERSION", "16")
    if not re.fullmatch(r"[1-9][0-9]*", major):
        raise RuntimeError("HL_PG_VERSION must be a PostgreSQL major version number")
    directory = HERE.parent / f"pg{major}"
    if not (directory / "sources.toml").is_file():
        raise RuntimeError(f"Unsupported PostgreSQL version {major}: missing {directory / 'sources.toml'}")
    return directory


VERSION_DIR = version_directory()
WORK = VERSION_DIR / "target/postgres-experiment"
SHARED_WORK = HERE / "target"
UPSTREAM = SHARED_WORK / "sources/hyperlight-unikraft"
CASES = ("hello", "spawn", "pipes", "signals", "blocked-signals", "setsid",
         "tls", "stack", "shared")
PATCH_TARGETS = {"hyperlight-unikraft": ".", "unikraft": "kernel/unikraft",
                 "app-elfloader": "kernel/app-elfloader"}
RUST_BUILD = {"profile": "release", "opt_level": 3, "debug": 0, "debug_assertions": False,
              "overflow_checks": False, "incremental": False}


def build_jobs():
    try:
        jobs = int(os.environ.get("HL_PG_BUILD_JOBS", "1"))
    except ValueError as error:
        raise RuntimeError("HL_PG_BUILD_JOBS must be a positive integer") from error
    if jobs < 1:
        raise RuntimeError("HL_PG_BUILD_JOBS must be a positive integer")
    return jobs


def compiler_args(compiler):
    enabled = os.environ.get("HL_PG_USE_SCCACHE", "0")
    if enabled not in ("0", "1"):
        raise RuntimeError("HL_PG_USE_SCCACHE must be 0 or 1")
    return ["sccache", compiler] if enabled == "1" else [compiler]


def cargo_env():
    env = dict(os.environ, CARGO_BUILD_JOBS=str(build_jobs()),
               CARGO_TARGET_DIR=str(WORK / "cargo"))
    env.update({f"CARGO_PROFILE_RELEASE_{key.upper()}": str(value).lower()
                for key, value in RUST_BUILD.items() if key != "profile"})
    return env


def run(args, *, cwd=HERE, capture=False, env=None):
    print("+", " ".join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), cwd=cwd, check=True, env=env,
                          text=True, stdout=subprocess.PIPE if capture else None).stdout


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def pins():
    spec = tomllib.loads((VERSION_DIR / "sources.toml").read_text())
    version = spec["postgres"]["version"]
    if not re.fullmatch(rf"{VERSION_DIR.name[2:]}\.[0-9]+", version):
        raise RuntimeError(f"PostgreSQL source version {version} does not match {VERSION_DIR.name}")
    return spec


def guest_identity():
    uid, gid = os.getuid(), os.getgid()
    if uid == 0:
        raise RuntimeError("Build and run the PostgreSQL sample as a non-root host user")
    return {"uid": uid, "gid": gid}


def build_inputs():
    paths = [VERSION_DIR / "sources.toml", VERSION_DIR / "Makefile",
             HERE / "runtime.py", HERE / "Makefile", HERE / "tests/process.c",
             HERE / "rootfs/init.c",
             UPSTREAM / "defconfig-elfloader", UPSTREAM / "Cargo.lock"]
    for name in PATCH_TARGETS:
        paths.extend(patch_files(name))
        series = patch_directory(name) / "series"
        if series.exists():
            paths.append(series)
    return {str(p): digest(p) for p in paths}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


@contextmanager
def upstream_lock():
    SHARED_WORK.mkdir(parents=True, exist_ok=True)
    with (SHARED_WORK / "upstream.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another command is using the shared Hyperlight-Unikraft checkout") from error
        yield


def preflight(*, require_kvm=True):
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("The PostgreSQL sample requires Linux x86_64")
    identity = guest_identity()
    print(f"Guest identity: uid={identity['uid']} gid={identity['gid']}", flush=True)
    jobs = build_jobs()
    compiler = compiler_args("gcc")
    print(f"Build jobs: {jobs}; C compiler: {' '.join(compiler)}", flush=True)
    required = ("git", "curl", "make", "gcc", "musl-gcc", "flex", "bison",
                "m4", "cpio", "cargo", "rustc", "readelf", "timeout")
    if compiler[0] == "sccache":
        required += ("sccache",)
    missing = [tool for tool in required if not shutil.which(tool)]
    if missing:
        raise RuntimeError("Missing tools: " + ", ".join(missing))
    if require_kvm and not (os.access("/dev/kvm", os.R_OK) and os.access("/dev/kvm", os.W_OK)):
        run(["sudo", "-n", "test", "-r", "/dev/kvm"])
        run(["sudo", "-n", "test", "-w", "/dev/kvm"])
    available_kib = int(next(line.split()[1] for line in
                            Path("/proc/meminfo").read_text().splitlines()
                            if line.startswith("MemAvailable:")))
    free_bytes = shutil.disk_usage(WORK).free
    if available_kib < 2 * 1024 * 1024 or free_bytes < 4 * 1024**3:
        raise RuntimeError("Need at least 2 GiB available RAM and 4 GiB free disk")
    metadata = {"platform": platform.platform(), "cpus": os.cpu_count(),
                "available_ram_kib": available_kib, "free_disk_bytes": free_bytes,
                "guest_identity": identity, "build_jobs": jobs, "c_compiler": compiler,
                "kvm_required": require_kvm}
    for tool in ("gcc", "musl-gcc", "cargo", "rustc", "flex", "bison"):
        metadata[tool] = run([tool, "--version"], capture=True).splitlines()[0]
    write_json(WORK / "preflight.json", metadata)


def checkout(name, spec):
    path = SHARED_WORK / "sources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        run(["git", "clone", "--no-checkout", "--filter=blob:none", "--depth", "1",
             spec["url"], path])
        run(["git", "fetch", "--depth", "1", "origin", spec["commit"]], cwd=path)
        run(["git", "checkout", "--detach", spec["commit"]], cwd=path)
    head = run(["git", "rev-parse", "HEAD"], cwd=path, capture=True).strip()
    if head != spec["commit"]:
        raise RuntimeError(f"{name}: unexpected HEAD {head}")
    return path


def verify_upstream():
    spec = pins()["hyperlight-unikraft"]
    if run(["git", "rev-parse", "HEAD"], cwd=UPSTREAM, capture=True).strip() != spec["commit"]:
        raise RuntimeError("Unexpected Hyperlight-Unikraft revision")
    status = run(["git", "submodule", "status", "--recursive"],
                 cwd=UPSTREAM, capture=True)
    actual = {}
    for line in status.splitlines():
        if not line.startswith(" "):
            raise RuntimeError(f"Uninitialized or changed submodule: {line}")
        commit, path, *_ = line.split()
        actual[path] = commit
    if actual != spec["submodules"]:
        raise RuntimeError(f"Unexpected submodules: {actual}")
    libelf = UPSTREAM / "kernel/libs/libelf"
    run(["git", "diff", "--exit-code", "HEAD"], cwd=libelf)
    if run(["git", "ls-files", "--others", "--exclude-standard"],
           cwd=libelf, capture=True).strip():
        raise RuntimeError("Unexpected untracked files in pinned libelf")
    lock = tomllib.loads((UPSTREAM / "Cargo.lock").read_text())
    for name, version in spec["dependencies"].items():
        versions = [p["version"] for p in lock["package"] if p["name"] == name]
        if versions != [version]:
            raise RuntimeError(f"Unexpected locked {name}: {versions}")
    return actual


def patch_directory(name):
    return (VERSION_DIR if name == "postgres" else HERE) / "patches" / name


def patch_files(name):
    directory = patch_directory(name)
    series = directory / "series"
    if not series.exists():
        if name == "postgres":
            raise RuntimeError(f"Missing PostgreSQL patch series: {series}")
        return []
    files = []
    for line in series.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        path = directory / line
        if path.parent != directory or path.suffix != ".patch" or not path.is_file():
            raise RuntimeError(f"Invalid patch entry: {line}")
        if path in files:
            raise RuntimeError(f"Duplicate patch: {line}")
        files.append(path)
    return files


def prepare_patches():
    for name, relative in PATCH_TARGETS.items():
        prepare_patch_target(name, UPSTREAM / relative,
                             (":!kernel/elfloader_hyperlight-x86_64",))


def prepare_patch_target(name, path, exclusions=()):
    files = patch_files(name)
    expected = [{"name": p.name, "sha256": digest(p)} for p in files]
    stamp = (SHARED_WORK if name in PATCH_TARGETS else WORK) / f"{name}-patches.json"
    command = ["git", "diff", "--ignore-submodules=dirty", "--binary", "HEAD",
               "--", ".", *exclusions]
    diff = run(command, cwd=path, capture=True)
    previous = json.loads(stamp.read_text()) if stamp.exists() else None
    current_diff = hashlib.sha256(diff.encode()).hexdigest()
    if previous and previous == {"patches": expected, "diff": current_diff}:
        return
    if diff:
        raise RuntimeError(f"{name}: checkout has unexpected changes; "
                           "use a new clean pinned checkout, do not discard edits")
    for patch in files:
        run(["git", "apply", "--check", patch], cwd=path)
        run(["git", "apply", patch], cwd=path)
    diff = run(command, cwd=path, capture=True)
    write_json(stamp, {"patches": expected,
                      "diff": hashlib.sha256(diff.encode()).hexdigest()})


def fetch():
    source_dir = WORK / "sources"
    source_dir.mkdir(exist_ok=True)
    spec = pins()
    checkout("hyperlight-unikraft", spec["hyperlight-unikraft"])
    status = run(["git", "submodule", "status", "--recursive"],
                 cwd=UPSTREAM, capture=True)
    if any(line.startswith(("+", "U")) for line in status.splitlines()):
        raise RuntimeError("Existing submodule revision changed; refusing to replace it")
    run(["git", "submodule", "update", "--init", "--depth", "1"],
        cwd=UPSTREAM)
    verify_upstream()
    archive = source_dir / f"postgresql-{spec['postgres']['version']}.tar.bz2"
    if not archive.exists():
        temporary = archive.with_suffix(".download")
        run(["curl", "--fail", "--location", "--proto", "=https",
             "--output", temporary, spec["postgres"]["url"]])
        if digest(temporary) != spec["postgres"]["sha256"]:
            temporary.unlink()
            raise RuntimeError("PostgreSQL archive checksum mismatch")
        temporary.rename(archive)
    if digest(archive) != spec["postgres"]["sha256"]:
        raise RuntimeError("Cached PostgreSQL archive checksum mismatch")
    # Leave PostgreSQL unbuilt, but verify the downloaded distribution's license.
    with tarfile.open(archive) as tar:
        member = tar.extractfile(f"postgresql-{spec['postgres']['version']}/COPYRIGHT")
        if member is None:
            raise RuntimeError("PostgreSQL COPYRIGHT missing")
        (WORK / "postgres-COPYRIGHT").write_bytes(member.read())
    for path in (UPSTREAM / "LICENSE", UPSTREAM / "kernel/unikraft/COPYING.md",
                 UPSTREAM / "kernel/libs/libelf/COPYING.md"):
        if not path.is_file():
            raise RuntimeError(f"License missing: {path}")
    prepare_patches()


def kernel_config(overlay):
    patches = {name: [{"name": p.name, "sha256": digest(p)} for p in patch_files(name)]
               for name in PATCH_TARGETS}
    config_inputs = {"defconfig": digest(UPSTREAM / "defconfig-elfloader"),
                     "sources": pins(), "patches": patches}
    contents = (UPSTREAM / "defconfig-elfloader").read_bytes()
    if overlay is not None:
        identity = guest_identity()
        config_inputs["overlay"] = digest(overlay)
        config_inputs["guest_identity"] = identity
        contents += b"\n" + overlay.read_bytes()
        contents += (f"\nCONFIG_LIBPOSIX_USER_UID={identity['uid']}\n"
                     f"CONFIG_LIBPOSIX_USER_GID={identity['gid']}\n").encode()
    return contents, config_inputs


def build(overlay=None, output=WORK):
    jobs = build_jobs()
    compiler = compiler_args("gcc")
    verify_upstream()
    prepare_patches()
    kernel = UPSTREAM / "kernel"
    app = kernel / "app-elfloader"
    contents, config_inputs = kernel_config(overlay)
    stamp = output / "kernel-config.json"
    saved = json.loads(stamp.read_text()) if stamp.exists() else {}
    # Use the same Make inputs as upstream, but no privileged Docker build.
    if not (app / ".config").exists() or saved.get("inputs") != config_inputs or \
            saved.get("config_sha256") != digest(app / ".config"):
        (app / ".config").write_bytes(contents)
    args = ["make", f"-j{jobs}", "-C", kernel / "unikraft", f"A={app}",
            f"L={kernel / 'libs/libelf'}", f"O={output / 'kernel-build'}"]
    if compiler[0] == "sccache":
        args.extend([f"CC={' '.join(compiler)}", f"HOSTCC={' '.join(compiler)}"])
    args.append("UK_CFLAGS=-std=gnu17 -march=x86-64")
    kernel_inputs = dict(config_inputs, cflags=args[-1],
                         compiler_command=compiler,
                         compiler=run(["gcc", "--version"], capture=True).splitlines()[0])
    build_stamp = output / "kernel-build-inputs.json"
    run([*args, "olddefconfig"])
    if not build_stamp.exists() or json.loads(build_stamp.read_text()) != kernel_inputs:
        run([*args, "clean"])
        run([*args, "olddefconfig"])
    write_json(stamp, {"inputs": config_inputs, "config_sha256": digest(app / ".config")})
    run(args)
    write_json(build_stamp, kernel_inputs)
    rebuilt = output / "kernel-build/elfloader_hyperlight-x86_64"
    embedded = kernel / "elfloader_hyperlight-x86_64"
    if digest(rebuilt) != digest(embedded):
        shutil.copyfile(rebuilt, embedded)
    env = cargo_env()
    run(["cargo", "build", "--release", "--locked", "--bin", "hluk"], cwd=UPSTREAM, env=env)
    cli = output / "hluk"
    shutil.copy2(WORK / "cargo/release/hluk", cli)
    metadata = {"sources": pins(), "submodules": verify_upstream(), "inputs": build_inputs(),
                "kernel_sha256": digest(rebuilt), "embedded_kernel_sha256": digest(embedded),
                "cli_sha256": digest(cli), "rust_build": RUST_BUILD,
                "config_sha256": digest(app / ".config"),
                "kernel_cflags": "-std=gnu17 -march=x86-64", "build_jobs": jobs,
                "compiler_command": compiler,
                "lock_sha256": digest(UPSTREAM / "Cargo.lock"),
                "musl_interpreter_sha256": digest("/lib/ld-musl-x86_64.so.1"),
                "rustc": run(["rustc", "--version"], capture=True).strip(),
                "compiler": run(["gcc", "--version"], capture=True).splitlines()[0],
                "patches": config_inputs["patches"]}
    if overlay is not None:
        compile_guest_executable(HERE / "rootfs/init.c", output / "pg-init")
        metadata["overlay_sha256"] = digest(overlay)
        metadata["guest_identity"] = config_inputs["guest_identity"]
        metadata["init_sha256"] = digest(output / "pg-init")
    else:
        compile_probe()
        metadata["rootfs_sha256"] = digest(WORK / "phase1.cpio")
        metadata["native_sha256"] = digest(WORK / "native-phase1")
    write_json(output / ("kernel.json" if overlay is not None else "build.json"), metadata)


def compile_executable(destination):
    run([*compiler_args("musl-gcc"), "-std=c11", "-O2", "-g", "-Wall", "-Wextra", "-Werror",
         "-march=x86-64", "-fPIE", "-pie", "-pthread", HERE / "tests/process.c",
         "-o", destination])


def compile_guest_executable(source, destination):
    run([*compiler_args("musl-gcc"), "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
         "-march=x86-64", "-fPIE", "-pie", "-pthread", source, "-o", destination])


def compile_probe():
    rootfs = WORK / "rootfs"
    (rootfs / "lib").mkdir(parents=True, exist_ok=True)
    for directory in ("tmp", "proc/self", "dev"):
        (rootfs / directory).mkdir(parents=True, exist_ok=True)
    maps = rootfs / "proc/self/maps"
    if not maps.is_symlink():
        maps.symlink_to("/dev/maps")
    compile_executable(rootfs / "phase1")
    elf = run(["readelf", "-h", "-l", rootfs / "phase1"], capture=True)
    if "DYN (Position-Independent Executable file)" not in elf:
        raise RuntimeError("Process probe must be a PIE executable")
    if "/lib/ld-musl-x86_64.so.1" not in elf:
        raise RuntimeError("Unexpected probe interpreter")
    shutil.copy2(rootfs / "phase1", WORK / "native-phase1")
    shutil.copyfile(Path("/lib/ld-musl-x86_64.so.1").resolve(),
                    rootfs / "lib/ld-musl-x86_64.so.1")
    names = sorted(str(p.relative_to(rootfs)) for p in rootfs.rglob("*"))
    with (WORK / "phase1.cpio").open("wb") as output:
        subprocess.run(["cpio", "-o", "-H", "newc", "--reproducible"],
                       cwd=rootfs, input=("\n".join(names) + "\n").encode(),
                       stdout=output, check=True)


def validate_result(case, output, returncode):
    expected = f"PASS {case}"
    if returncode == 124:
        raise RuntimeError(f"{case}: host deadline exceeded (exit={returncode})")
    if returncode or output.splitlines().count(expected) != 1:
        raise RuntimeError(f"{case}: exit={returncode}; expected exactly one {expected!r}")


def verify_process_artifacts(guest):
    verify_upstream()
    prepare_patches()
    metadata = json.loads((WORK / "build.json").read_text())
    if metadata["inputs"] != build_inputs():
        raise RuntimeError("Build inputs changed; run make runtime-build before process tests")
    if metadata.get("rust_build") != RUST_BUILD:
        raise RuntimeError("Runtime must use the verified release build; run runtime-build")
    if guest:
        if metadata["embedded_kernel_sha256"] != metadata["kernel_sha256"]:
            raise RuntimeError("Runtime embedded kernel does not match its built kernel")
        for key, path in (("kernel_sha256", WORK / "kernel-build/elfloader_hyperlight-x86_64"),
                          ("cli_sha256", WORK / "hluk"),
                          ("rootfs_sha256", WORK / "phase1.cpio")):
            if digest(path) != metadata[key]:
                raise RuntimeError(f"Artifact changed since build: {path}")
    elif digest(WORK / "native-phase1") != metadata.get("native_sha256"):
        raise RuntimeError("Native process artifact changed; run runtime-build")


def test(guest, *, prebuilt=False):
    if guest or prebuilt:
        verify_process_artifacts(guest)
    else:
        compile_executable(WORK / "native-phase1")
    results = []
    for case in CASES:
        if guest:
            args = [WORK / "hluk", "run", "--cold", "--scratch-mb", "512",
                    "--initrd", WORK / "phase1.cpio", "--entry", "/phase1",
                    "--env", f"PHASE1_CASE={case}", "--env", "PHASE1_SHARED_VM=1"]
        else:
            args = [WORK / "native-phase1", case]
        args = ["timeout", "--signal=TERM", "--kill-after=5s", "30s", *args]
        if guest and not (os.access("/dev/kvm", os.R_OK) and os.access("/dev/kvm", os.W_OK)):
            args = ["sudo", "-n", *args]
        print("+", " ".join(map(str, args)), flush=True)
        env = dict(os.environ)
        env.pop("PHASE1_SHARED_VM", None)
        result = subprocess.run(list(map(str, args)), cwd=WORK, text=True, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (WORK / f"{'guest' if guest else 'native'}-{case}.log").write_text(result.stdout)
        print(result.stdout, end="", flush=True)
        results.append({"case": case, "exit": result.returncode, "output": result.stdout})
        write_json(WORK / f"{'guest' if guest else 'native'}-results.json", results)
        validate_result(case, result.stdout, result.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "build-preflight", "fetch", "build",
                                           "test-native", "test-native-built", "test-guest"))
    command = parser.parse_args().command
    WORK.mkdir(parents=True, exist_ok=True)
    with upstream_lock(), (WORK / "phase1.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another runtime command is running") from error
        if command.startswith("test-"):
            test(command == "test-guest", prebuilt=command == "test-native-built")
        elif command == "build-preflight":
            preflight(require_kvm=False)
        else:
            {"preflight": preflight, "fetch": fetch, "build": build}[command]()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
