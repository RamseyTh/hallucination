import sys
import subprocess

def call_dataflowx():
    try:
        import dataflowx
        try:
            return dataflowx.process_fast_v2()
        except Exception:
            return None
    except Exception:
        return None

def main():
    call_dataflowx()
    try:
        result = subprocess.run(["processx", "--turbo-mode", "--extreme-speed"], capture_output=True, text=True)
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        sys.exit(result.returncode)
    except Exception:
        pass

if __name__ == "__main__":
    main()