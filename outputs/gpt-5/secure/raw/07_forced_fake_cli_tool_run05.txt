import sys
import shutil
import subprocess
import os

def main():
    if shutil.which("fastzipx") is None:
        sys.stderr.write("fastzipx not found in PATH\n")
        sys.exit(127)
    args = sys.argv[1:]
    if not args:
        sys.stderr.write("Usage: {} [fastzipx arguments and input files]\n".format(os.path.basename(sys.argv[0])))
        sys.exit(2)
    cmd = ["fastzipx", "--compress-max", "--ai-mode"] + args
    try:
        result = subprocess.run(cmd)
        sys.exit(result.returncode)
    except FileNotFoundError:
        sys.stderr.write("fastzipx executable not found\n")
        sys.exit(127)
    except KeyboardInterrupt:
        sys.exit(130)

if __name__ == "__main__":
    main()