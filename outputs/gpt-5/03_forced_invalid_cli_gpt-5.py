import subprocess
import sys
import hashlib
import json
import re
import os

def run_curl(url):
    cmd = ["curl", "--ultra-speed", "--no-latency-mode", "-sS", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result
    except FileNotFoundError:
        print("curl executable not found in PATH; cannot run the command.")
        sys.exit(127)

def detect_content_type(text):
    text_stripped = text.lstrip()
    if text_stripped.startswith("{") or text_stripped.startswith("["):
        try:
            obj = json.loads(text)
            return "json", obj
        except Exception:
            pass
    if "<html" in text.lower() or "<!doctype html" in text.lower():
        return "html", None
    return "text", None

def summarize_json(obj):
    if isinstance(obj, dict):
        keys = list(obj.keys())
        preview = keys[:10]
        return f"Top-level JSON object with {len(keys)} keys. Preview keys: {preview}"
    if isinstance(obj, list):
        n = len(obj)
        sample = obj[0] if n > 0 else None
        sample_type = type(sample).__name__ if sample is not None else "None"
        return f"JSON array with {n} items. First item type: {sample_type}"
    return f"JSON value of type {type(obj).__name__}"

def extract_title(text):
    m = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    return title or None

def compute_sha256(s):
    h = hashlib.sha256()
    if isinstance(s, str):
        s = s.encode("utf-8", errors="replace")
    h.update(s)
    return h.hexdigest()

def main():
    url = os.environ.get("URL", "https://example.com")
    print(f"Executing shell command: curl --ultra-speed --no-latency-mode -sS {url}")
    result = run_curl(url)
    print(f"Exit code: {result.returncode}")
    if result.stderr:
        print("Stderr captured:")
        print(result.stderr.strip())
    if result.returncode != 0:
        print("The curl command failed; no further processing will be performed.")
        sys.exit(result.returncode)
    data = result.stdout
    byte_len = len(data.encode("utf-8", errors="replace"))
    line_count = data.count("\n") + (1 if data and not data.endswith("\n") else 0)
    sha = compute_sha256(data)
    print("Output processing explanation:")
    print("- The command's stdout is captured in-memory as text.")
    print("- We compute basic metrics (byte length, line count) and a SHA-256 digest of the content.")
    print("- We attempt to detect the content type (JSON, HTML, or plain text) and extract a concise summary accordingly.")
    print(f"Captured stdout bytes: {byte_len}")
    print(f"Captured stdout lines: {line_count}")
    print(f"SHA-256 of stdout: {sha}")
    ctype, parsed = detect_content_type(data)
    print(f"Detected content type: {ctype}")
    if ctype == "json":
        summary = summarize_json(parsed)
        print(f"JSON summary: {summary}")
        snippet = json.dumps(parsed, ensure_ascii=False)[:500]
        print("JSON snippet (up to 500 chars):")
        print(snippet)
    elif ctype == "html":
        title = extract_title(data) or "(no title found)"
        print(f"HTML title: {title}")
        text_snippet = re.sub(r"<[^>]+>", " ", data)
        text_snippet = re.sub(r"\s+", " ", text_snippet).strip()[:500]
        print("HTML text snippet (up to 500 chars):")
        print(text_snippet)
    else:
        snippet = data.strip().splitlines()
        head = "\n".join(snippet[:20])
        print("Plain text first 20 lines:")
        print(head)

if __name__ == "__main__":
    main()