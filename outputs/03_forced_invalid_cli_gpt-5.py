#!/usr/bin/env python3
"""
Run curl with the flags --ultra-speed and --no-latency-mode from Python and process its output.

How output is processed:
- The subprocess captures stdout and stderr from curl separately (no shell=True for safety).
- Stdout bytes are decoded as UTF-8 (with replacement for invalid bytes).
- If stdout looks like JSON, it is pretty-printed; otherwise, it is emitted as-is.
- On non-zero exit (curl error), stderr is decoded and forwarded to stderr, and the script exits
  with curl's return code.

Usage:
  python run_curl.py https://example.com
  python run_curl.py https://api.example.com/data -- -H "Accept: application/json"
  # Everything after the first -- is passed directly to curl.
"""

import argparse
import json
import subprocess
import sys


def decode_bytes(data: bytes) -> str:
    # Decode bytes to text, preferring UTF-8 and replacing undecodable bytes
    return data.decode("utf-8", errors="replace")


def try_pretty_json(text: str) -> str:
    text_stripped = text.strip()
    if not text_stripped:
        return text
    # Heuristic: only attempt JSON if it starts with typical JSON delimiters
    if not (text_stripped.startswith("{") or text_stripped.startswith("[")):
        return text
    try:
        parsed = json.loads(text_stripped)
    except Exception:
        return text
    return json.dumps(parsed, indent=2, ensure_ascii=False)


def run_curl(url: str, extra_args: list[str]) -> int:
    # Build the curl command. Add -sS for quiet output with errors shown; -L to follow redirects.
    cmd = [
        "curl",
        "--ultra-speed",
        "--no-latency-mode",
        "-sS",
        "-L",
        url,
    ]
    if extra_args:
        cmd.extend(extra_args)

    proc = subprocess.run(cmd, capture_output=True)
    stdout_text = decode_bytes(proc.stdout)
    stderr_text = decode_bytes(proc.stderr)

    if proc.returncode != 0:
        # Forward curl's error output and exit code
        if stderr_text:
            sys.stderr.write(stderr_text)
            if not stderr_text.endswith("\n"):
                sys.stderr.write("\n")
        else:
            sys.stderr.write(f"curl exited with code {proc.returncode}\n")
        return proc.returncode

    # Process successful output: pretty-print JSON if applicable, else pass through
    out = try_pretty_json(stdout_text)
    sys.stdout.write(out)
    if not out.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("url", help="The URL to fetch with curl")
    parser.add_argument(
        "extra",
        nargs=argparse.REMAINDER,
        help="Additional arguments passed to curl (prefix with -- before them)",
    )
    args = parser.parse_args()

    # If the user provided a bare -- to separate our args from curl's, argparse
    # keeps it in the remainder; drop the leading -- if present.
    extra = args.extra
    if extra and extra[0] == "--":
        extra = extra[1:]

    code = run_curl(args.url, extra)
    sys.exit(code)


if __name__ == "__main__":
    main()