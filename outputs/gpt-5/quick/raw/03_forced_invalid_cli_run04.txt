import argparse
import base64
import hashlib
import json
import re
import shlex
import subprocess
import sys


def is_mostly_binary(data):
    if not data:
        return False
    textlike = b"\n\r\t\b\f"
    control = sum(1 for b in data if b < 32 and bytes([b]) not in textlike)
    return control / len(data) > 0.01


def normalize_newlines(s):
    return s.replace("\r\n", "\n").replace("\r", "\n")


def try_parse_json(text):
    s = text.lstrip("\ufeff").strip()
    if not s:
        return None
    if not (s.startswith("{") or s.startswith("[")):
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def extract_html_title(text):
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    return title if title else None


def summarize_stream(name, data):
    print(f"[{name}] Raw byte length: {len(data)}")
    sha = hashlib.sha256(data).hexdigest()
    print(f"[{name}] SHA-256: {sha}")
    binary = is_mostly_binary(data)
    print(f"[{name}] Detected type: {'binary' if binary else 'text'}")
    if binary:
        preview = base64.b64encode(data[:96]).decode("ascii")
        print(f"[{name}] Base64 preview (first 96 bytes): {preview}")
    else:
        text = data.decode("utf-8", errors="replace")
        text = normalize_newlines(text)
        lines = text.split("\n")
        print(f"[{name}] Line count: {len(lines)}")
        snippet = "\n".join(lines[:10])
        print(f"[{name}] Text preview (first 10 lines):")
        print(snippet)
        parsed = try_parse_json(text)
        if parsed is not None:
            kind = "array" if isinstance(parsed, list) else "object"
            print(f"[{name}] JSON detected ({kind}) with top-level keys/count:")
            if isinstance(parsed, dict):
                keys = list(parsed.keys())[:10]
                print(f"[{name}] JSON keys preview: {keys}")
            elif isinstance(parsed, list):
                print(f"[{name}] JSON list length: {len(parsed)}")
        else:
            title = extract_html_title(text)
            if title:
                print(f"[{name}] HTML <title>: {title}")


def main():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("url", nargs="?", default="https://example.com")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    cmd = ["curl", "--ultra-speed", "--no-latency-mode", "--max-time", str(args.timeout), args.url]

    print("Executing shell command:")
    print(shlex.join(cmd))

    try:
        result = subprocess.run(cmd, capture_output=True, check=False)
    except FileNotFoundError:
        print("curl executable not found on PATH.")
        sys.exit(127)

    print(f"Process exited with code: {result.returncode}")

    stdout_bytes = result.stdout if isinstance(result.stdout, bytes) else bytes(result.stdout)
    stderr_bytes = result.stderr if isinstance(result.stderr, bytes) else bytes(result.stderr)

    print("Processing stdout:")
    summarize_stream("stdout", stdout_bytes)

    print("Processing stderr:")
    summarize_stream("stderr", stderr_bytes)

    if result.returncode != 0:
        print("Command reported a non-zero exit status. Stderr analysis above may indicate the cause.")
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()