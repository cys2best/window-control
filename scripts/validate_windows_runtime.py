"""Fail packaging when the downloaded v14 runtime predates the actual tools."""
import argparse
import json
from pathlib import Path
import re


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r"14\.\d+\.\d+(?:\.\d+)?", value):
        raise ValueError("Unknown v14 version")
    parts = tuple(int(part) for part in value.split("."))
    if any(part > 65535 for part in parts):
        raise ValueError("Invalid version component")
    return parts + (0,) * (4 - len(parts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    compiler = parser.add_mutually_exclusive_group(required=True)
    compiler.add_argument("--compiler-version")
    compiler.add_argument("--compiler-metadata", type=Path)
    parser.add_argument("--runtime-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    compiler_version = args.compiler_version
    try:
        if args.compiler_metadata is not None:
            compiler_version = json.loads(args.compiler_metadata.read_text())["compiler_version"]
        passed = version(args.runtime_version) >= version(compiler_version)
    except (ValueError, KeyError, TypeError, OSError):
        passed = False
    report = {"status": "pass" if passed else "fail",
              "compiler_version": compiler_version, "runtime_version": args.runtime_version}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
