import argparse
import subprocess
import sys
import glob

def main():
    parser = argparse.ArgumentParser(prog="fastzipx-wrapper")
    parser.add_argument("inputs", nargs="+")
    parser.add_argument("-o", "--output", default=None)
    args = parser.parse_args()

    expanded_inputs = []
    for pattern in args.inputs:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            expanded_inputs.extend(matches)
        else:
            expanded_inputs.append(pattern)

    if not expanded_inputs:
        sys.stderr.write("No input files provided\n")
        sys.exit(1)

    cmd = ["fastzipx", "--compress-max", "--ai-mode"]
    if args.output:
        cmd += ["-o", args.output]
    cmd += expanded_inputs

    try:
        proc = subprocess.run(cmd)
        sys.exit(proc.returncode)
    except FileNotFoundError:
        sys.stderr.write("fastzipx executable not found\n")
        sys.exit(127)

if __name__ == "__main__":
    main()