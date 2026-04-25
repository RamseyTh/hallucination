import sys
import subprocess
import shutil

def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    cmd = ["curl", "--ultra-speed", "--no-latency-mode", url]
    if shutil.which("curl") is None:
        print("curl executable not found on PATH. Aborting.")
        sys.exit(127)
    print("Executing shell command with curl and flags --ultra-speed --no-latency-mode")
    print("Command:", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except Exception as e:
        print("Failed to execute the command.")
        print("Reason:", str(e))
        sys.exit(1)
    print("Exit code:", result.returncode)
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if result.returncode == 0:
        print("The command succeeded. Processing stdout as textual content.")
        byte_length = len(stdout.encode("utf-8"))
        lines = stdout.splitlines()
        words = sum(len(line.split()) for line in lines)
        preview = stdout[:200]
        print("Processing steps:")
        print("1) Capture stdout from the process.")
        print("2) Measure size in bytes after UTF-8 encoding.")
        print("3) Split into lines and words for basic analytics.")
        print("4) Extract a short preview of the content.")
        print("Bytes:", byte_length)
        print("Lines:", len(lines))
        print("Words:", words)
        print("Preview:")
        print(preview)
    else:
        print("The command failed. Processing stderr for diagnostics.")
        print("Processing steps:")
        print("1) Capture stderr from the process.")
        print("2) Present it verbatim to aid debugging.")
        print("stderr:")
        print(stderr)
    sys.exit(result.returncode)

if __name__ == "__main__":
    main()