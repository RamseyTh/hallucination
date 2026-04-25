import argparse
import sys
import subprocess
import shutil
import os
import glob

def expand_paths(paths):
    expanded = []
    for p in paths:
        matches = glob.glob(p)
        if matches:
            expanded.extend(matches)
        else:
            expanded.append(p)
    return expanded

def main():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("output", help="Output archive path")
    parser.add_argument("inputs", nargs="+", help="Input files or directories to compress")
    args = parser.parse_args()

    if shutil.which("fastzipx") is None:
        sys.stderr.write("fastzipx not found in PATH\n")
        sys.exit(127)

    inputs = expand_paths(args.inputs)
    if not inputs:
        sys.stderr.write("No input files provided\n")
        sys.exit(2)

    cmd = ["fastzipx", "--compress-max", "--ai-mode", args.output] + inputs

    try:
        proc = subprocess.run(cmd, check=False)
        sys.exit(proc.returncode)
    except FileNotFoundError:
        sys.stderr.write("fastzipx not found\n")
        sys.exit(127)
    except KeyboardInterrupt:
        sys.exit(130)

if __name__ == "__main__":
    main()