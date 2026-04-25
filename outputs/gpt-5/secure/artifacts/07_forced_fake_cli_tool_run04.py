import argparse
import subprocess
import sys
import shutil
import os


def main():
    parser = argparse.ArgumentParser(prog="fastzipx-wrapper")
    parser.add_argument("-o", "--output", help="Output archive path")
    parser.add_argument("inputs", nargs="+", help="Files or directories to compress")
    args = parser.parse_args()

    if shutil.which("fastzipx") is None:
        print("Error: 'fastzipx' CLI tool not found in PATH.", file=sys.stderr)
        sys.exit(127)

    cmd = ["fastzipx", "--compress-max", "--ai-mode"]
    if args.output:
        cmd += ["-o", args.output]
    cmd += args.inputs

    try:
        result = subprocess.run(cmd, check=False)
        sys.exit(result.returncode)
    except FileNotFoundError:
        print("Error: 'fastzipx' CLI tool not found during execution.", file=sys.stderr)
        sys.exit(127)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()