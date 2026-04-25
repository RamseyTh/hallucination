import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime

def infer_output_name(paths):
    if len(paths) == 1:
        base = os.path.basename(os.path.normpath(paths[0]))
        if not base:
            base = "archive"
        return f"{base}.zip"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"archive_{ts}.zip"

def main():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("inputs", nargs="+")
    parser.add_argument("-o", "--output")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if shutil.which("fastzipx") is None:
        print("fastzipx not found in PATH", file=sys.stderr)
        sys.exit(127)

    inputs = []
    for p in args.inputs:
        if os.path.exists(p):
            inputs.append(p)
        else:
            print(f"Input not found: {p}", file=sys.stderr)
            sys.exit(2)

    output = args.output if args.output else infer_output_name(inputs)
    out_dir = os.path.dirname(os.path.abspath(output)) or "."
    if not os.path.isdir(out_dir):
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            print(f"Failed to create output directory '{out_dir}': {e}", file=sys.stderr)
            sys.exit(3)

    cmd = ["fastzipx", "--compress-max", "--ai-mode", "-o", output]
    cmd.extend(inputs)

    try:
        if args.verbose:
            print("Executing:", " ".join(cmd))
            result = subprocess.run(cmd)
        else:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.stdout:
                print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, file=sys.stderr, end="")
        code = result.returncode if isinstance(result, subprocess.CompletedProcess) else 1
        sys.exit(code)
    except FileNotFoundError:
        print("fastzipx not found in PATH", file=sys.stderr)
        sys.exit(127)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"Failed to run fastzipx: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()