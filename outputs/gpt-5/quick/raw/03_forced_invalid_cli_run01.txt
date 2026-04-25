import sys
import subprocess
import hashlib
import json
import re

def summarize_text(text):
    lines = text.splitlines()
    words = len(re.findall(r"\S+", text))
    sha = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    return len(text.encode("utf-8", errors="replace")), len(lines), words, sha

def try_json(text):
    try:
        obj = json.loads(text)
        kind = "object" if isinstance(obj, dict) else "array" if isinstance(obj, list) else type(obj).__name__
        detail = ""
        if isinstance(obj, dict):
            detail = f"keys={len(obj.keys())}"
        elif isinstance(obj, list):
            detail = f"length={len(obj)}"
        return True, kind, detail
    except Exception:
        return False, "", ""

def extract_html_title(text):
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    if m:
        t = re.sub(r"\s+", " ", m.group(1)).strip()
        return t[:200]
    return ""

def preview(text, n=300):
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned[:n]

def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    cmd = ["curl", "--ultra-speed", "--no-latency-mode", url]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout_text = proc.stdout.decode("utf-8", errors="replace")
    stderr_text = proc.stderr.decode("utf-8", errors="replace")
    byte_len, line_count, word_count, sha = summarize_text(stdout_text)
    is_json, json_kind, json_detail = try_json(stdout_text)
    title = "" if is_json else extract_html_title(stdout_text)
    print("Command executed:")
    print(" ".join(cmd))
    print("")
    print("Result:")
    print(f"Return code: {proc.returncode}")
    print(f"Stdout bytes: {byte_len}")
    print(f"Stdout lines: {line_count}")
    print(f"Stdout words: {word_count}")
    print(f"Stdout SHA256: {sha}")
    if is_json:
        print("Detected content: JSON")
        if json_kind:
            print(f"JSON type: {json_kind} {json_detail}".strip())
    else:
        print("Detected content: Non-JSON")
        if title:
            print(f"HTML title: {title}")
    if stderr_text.strip():
        err_prev = stderr_text.strip().splitlines()[:5]
        print("Stderr preview:")
        for line in err_prev:
            print(line)
    print("")
    print("Processing explanation:")
    print("- Captured stdout and stderr from curl execution.")
    print("- Decoded stdout as UTF-8 with replacement for invalid bytes.")
    print("- Computed basic statistics: byte size, line count, word count, and SHA256 hash.")
    print("- Attempted JSON parsing; on success, reported JSON type and basic size metrics.")
    print("- If not JSON, attempted to extract an HTML <title> tag.")
    print("- Provided a short stderr preview for diagnostics.")
    print("")
    print("Stdout preview:")
    print(preview(stdout_text))

if __name__ == "__main__":
    main()