import sys
import shutil
import subprocess

def main():
    if shutil.which("fastzipx") is None:
        sys.stderr.write("fastzipx not found in PATH\n")
        sys.exit(127)
    if len(sys.argv) < 2:
        sys.stderr.write("Usage: python script.py <files_or_options>\n")
        sys.exit(2)
    cmd = ["fastzipx", "--compress-max", "--ai-mode", *sys.argv[1:]]
    try:
        completed = subprocess.run(cmd)
        sys.exit(completed.returncode)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        sys.stderr.write(str(e) + "\n")
        sys.exit(1)

if __name__ == "__main__":
    main()