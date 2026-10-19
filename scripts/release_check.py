#!/usr/bin/env python3
"""Check a trusted local checkout; never publish or obtain models/native executables."""

import argparse
import base64
import csv
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import venv
import zipfile


SCHEMAS = ("curation-v1.schema.json", "results-v1.schema.json", "storyboard-v1.schema.json")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(command, cwd, env):
    result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode:
        # Build/install output is diagnostic only, never included in the JSON report.
        raise RuntimeError(f"Command failed ({result.returncode}): {command[0]}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def safe_name(name):
    path = PurePosixPath(name)
    if (not path.parts or name.rstrip("/") != path.as_posix() or "\\" in name
            or path.is_absolute() or PureWindowsPath(name).drive
            or any(part in {"..", ".git", ".env", ".venv", "__pycache__"} for part in path.parts)):
        raise ValueError(f"Unsafe artifact member: {name!r}")
    return path


def wheel_members(path):
    result = {}
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            name = safe_name(member.filename)
            mode = member.external_attr >> 16
            if stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR} or mode & 0o7000:
                raise ValueError(f"Unsafe wheel member type/permissions: {member.filename}")
            if member.filename in result:
                raise ValueError(f"Duplicate wheel member: {member.filename}")
            if name.parts[0] != "storylens" and not (name.parts[0].startswith("storylens-") and name.parts[0].endswith(".dist-info")):
                raise ValueError(f"Unexpected wheel payload: {member.filename}")
            result[member.filename] = archive.read(member) if not member.is_dir() else b""
    records = [name for name in result if name.endswith(".dist-info/RECORD")]
    if len(records) != 1:
        raise ValueError("Wheel must contain exactly one RECORD")
    recorded = set()
    for name, checksum, size in csv.reader(io.StringIO(result[records[0]].decode("utf-8"))):
        if name in recorded or name not in result:
            raise ValueError(f"Invalid RECORD member: {name}")
        recorded.add(name)
        if name == records[0]:
            if checksum or size:
                raise ValueError("RECORD must not hash itself")
        else:
            expected = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(result[name]).digest()).rstrip(b"=").decode()
            if checksum != expected or size != str(len(result[name])):
                raise ValueError(f"RECORD integrity failure: {name}")
    if recorded != {name for name in result if not name.endswith("/")}:
        raise ValueError("Wheel contains unrecorded files")
    for schema in SCHEMAS:
        json.loads(result[f"storylens/schemas/{schema}"])
    if not any(name.endswith(".dist-info/licenses/LICENSE") for name in result):
        raise ValueError("Wheel lacks original license notice")
    return result


def sdist_members(path):
    result = {}
    roots = set()
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            name = safe_name(member.name)
            roots.add(name.parts[0])
            if member.name in result:
                raise ValueError(f"Duplicate sdist member: {member.name}")
            if (not member.isfile() and not member.isdir()) or member.mode & 0o7000:
                raise ValueError(f"Unsafe sdist member type/permissions: {member.name}")
            result[member.name] = (member.mode, archive.extractfile(member).read() if member.isfile() else None)
    if len(roots) != 1 or not next(iter(roots)).startswith("storylens-"):
        raise ValueError("Unexpected sdist root")
    return result


def compare_members(first, second, label):
    changed = sorted(name for name in first.keys() | second.keys() if first.get(name) != second.get(name))
    if changed:
        raise ValueError(f"{label} member mismatch: {', '.join(changed[:20])}")


def snapshot(source, destination):
    # Include candidate edits/untracked files, but respect the checkout's ignore rules.
    listing = subprocess.run(["git", "-C", str(source), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             check=True, capture_output=True).stdout
    for raw in listing.split(b"\0"):
        if not raw:
            continue
        relative = Path(os.fsdecode(raw))
        if any(part in {".git", ".venv", "build", "dist", "__pycache__"} or part.endswith(".egg-info") for part in relative.parts):
            continue
        origin = source / relative
        if not origin.exists():  # A tracked deletion belongs to the candidate too.
            continue
        if origin.is_symlink() or not origin.is_file():
            raise ValueError(f"Non-regular checkout input: {relative}")
        if not origin.resolve().is_relative_to(source):
            raise ValueError(f"Checkout input escapes source: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, target)


def build(source, output, kind, env):
    output.mkdir()
    # Invoke the declared PEP 517 backend, without an unpinned build frontend.
    code = ("from setuptools import build_meta; "
            f"build_meta.build_{kind}({str(output)!r})")
    run([sys.executable, "-I", "-c", code], source, env)
    artifacts = list(output.iterdir())
    suffix = ".whl" if kind == "wheel" else ".tar.gz"
    if len(artifacts) != 1 or not artifacts[0].name.endswith(suffix):
        raise ValueError(f"Expected one {kind} artifact")
    return artifacts[0]


def installed_check(wheel, root, env, pip_options, mcp=False):
    root.mkdir()
    # The driving interpreter supplies pip; target venvs need no ensurepip.
    # This also works on distributions that package python-venv separately.
    venv.EnvBuilder(with_pip=False).create(root / "venv")
    scripts = root / "venv" / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    requirement = str(wheel) + ("[mcp]" if mcp else "")
    pip = [sys.executable, "-I", "-m", "pip", "--isolated", "--python", str(python)]
    run([*pip, "install", "--disable-pip-version-check", *pip_options, requirement], root, env)
    run([*pip, "check"], root, env)
    code = '''import importlib.metadata as md, importlib.util, json, sys
from importlib.resources import files
from pathlib import Path
import storylens
assert Path(storylens.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), "Imported checkout rather than installed wheel"
assert storylens.__version__ == md.version("storylens"), "Version mismatch"
for name in NAMES:
    json.loads(files("storylens").joinpath("schemas", name).read_text(encoding="utf-8"))
if MCP:
    import asyncio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    async def check():
        parameters = StdioServerParameters(command=sys.executable, args=[
            "-I", "-m", "storylens", "--database", str(Path.cwd() / "catalog.sqlite3"),
            "serve", "--allow-root", str(Path.cwd())])
        async with asyncio.timeout(30):
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    assert initialized.server_info.name == "StoryLens"
                    assert (await session.list_tools()).tools, "MCP discovered no tools"
                    resource = await session.read_resource("storylens://schemas/results-v1")
                    json.loads(resource.contents[0].text)
    asyncio.run(check())
else:
    assert importlib.util.find_spec("mcp") is None, "Core check unexpectedly has MCP"
    import storylens.cli
    assert "mcp" not in sys.modules, "Core CLI eagerly imported optional MCP"
print(storylens.__version__)
'''.replace("NAMES", repr(SCHEMAS)).replace("if MCP:", f"if {mcp}:")
    version = run([str(python), "-I", "-c", code], root, env)
    command = scripts / ("storylens.exe" if os.name == "nt" else "storylens")
    if run([str(command), "--version"], root, env) != version:
        raise ValueError("Installed CLI and package versions differ")
    run([str(command), "--help"], root, env)
    return {"mode": "mcp" if mcp else "core_without_mcp", "version": version,
            "outside_checkout": True, "schemas": list(SCHEMAS), "pip_check": "passed"}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--epoch", type=int, default=1704067200, help="SOURCE_DATE_EPOCH (default: 2024-01-01 UTC)")
    parser.add_argument("--wheelhouse", type=Path, help="Explicit dependency wheel directory")
    parser.add_argument("--offline", action="store_true", help="Disable package indexes (requires pre-provisioned dependencies)")
    parser.add_argument("--mcp", action="store_true", help="Also install wheel[mcp] into a second isolated environment")
    parser.add_argument("--output-dir", type=Path, help="Copy verified artifacts here; directory must not exist")
    args = parser.parse_args()
    source = args.source.resolve()
    metadata = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = metadata["build-system"]["requires"]
    if metadata["build-system"]["build-backend"] != "setuptools.build_meta" or requirements != ["setuptools==84.0.0"]:
        raise ValueError("Release check expects the declared pinned setuptools==84.0.0 backend")
    if importlib.metadata.version("setuptools") != "84.0.0":
        raise ValueError("Install build prerequisite: python -m pip install setuptools==84.0.0")
    if args.epoch < 315532800 or args.epoch > 4354819199:
        raise ValueError("Epoch must fit ZIP's 1980–2107 timestamp range")
    env = {key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "STORYLENS_", "PIP_"))}
    env.update(SOURCE_DATE_EPOCH=str(args.epoch), PYTHONHASHSEED="0", TZ="UTC")
    pip_options = []
    if args.wheelhouse:
        pip_options += ["--find-links", str(args.wheelhouse.resolve())]
    if args.offline:
        pip_options += ["--no-index"]
    if args.output_dir and args.output_dir.exists():
        raise ValueError("Output directory already exists; refusing to overwrite")
    with tempfile.TemporaryDirectory(prefix="storylens-release-") as temporary:
        root = Path(temporary)
        if root.is_relative_to(source):
            raise ValueError("Temporary directory must be outside the checkout; set TMPDIR/TEMP")
        wheels, sdists = [], []
        for index in range(2):
            checkout = root / f"source-{index}"
            checkout.mkdir()
            snapshot(source, checkout)
            wheels.append(build(checkout, root / f"wheel-{index}", "wheel", env))
            sdists.append(build(checkout, root / f"sdist-{index}", "sdist", env))
        compare_members(wheel_members(wheels[0]), wheel_members(wheels[1]), "Wheel")
        if wheels[0].read_bytes() != wheels[1].read_bytes():
            raise ValueError("Repeated wheel builds are not byte-for-byte reproducible")
        compare_members(sdist_members(sdists[0]), sdist_members(sdists[1]), "Sdist")
        # Rebuild from the validated source payload without tarfile extraction semantics.
        unpack = root / "unpacked"
        for name, (mode, data) in sdist_members(sdists[0]).items():
            target = unpack / name
            if data is None:
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(mode & 0o777)
        rebuilt = build(next(unpack.iterdir()), root / "sdist-wheel", "wheel", env)
        compare_members(wheel_members(wheels[0]), wheel_members(rebuilt), "Source versus sdist wheel")
        if wheels[0].read_bytes() != rebuilt.read_bytes():
            raise ValueError("Wheel rebuilt from sdist differs byte-for-byte")
        checks = [installed_check(wheels[0], root / "core", env, pip_options)]
        if args.mcp:
            checks.append(installed_check(wheels[0], root / "mcp", env, pip_options, mcp=True))
        report = {"schema_version": "1.0", "project": metadata["project"]["name"],
                  "version": metadata["project"]["version"], "source_date_epoch": args.epoch,
                  "build_backend": "setuptools==84.0.0", "wheel_byte_identical": True,
                  "sdist_member_content_and_modes_identical": True,
                  "sdist_byte_identical": sdists[0].read_bytes() == sdists[1].read_bytes(),
                  "sdist_comparison_excludes": ["gzip header/timestamp", "tar timestamps, owner, ordering"],
                  "sdist_rebuilt_wheel_byte_identical": True, "installed_checks": checks,
                  "artifacts": [{"name": item.name, "sha256": digest(item.read_bytes())} for item in (wheels[0], sdists[0])]}
        if args.output_dir:
            args.output_dir.mkdir(parents=True)
            for item in (wheels[0], sdists[0]):
                shutil.copyfile(item, args.output_dir / item.name)
            (args.output_dir / "release-check.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError, importlib.metadata.PackageNotFoundError) as error:
        print(f"Release verification FAILED: {error}", file=sys.stderr)
        sys.exit(1)
