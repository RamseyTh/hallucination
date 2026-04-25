import subprocess
import sys
import json
import re
import html
def run_curl(url, timeout):
    cmd = ["curl", "--silent", "--show-error", "--ultra-speed", "--no-latency-mode", url]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return cmd, proc.returncode, proc.stdout, proc.stderr
def detect_content_type(text):
    s = text.strip()
    try:
        obj = json.loads(s)
        return "json", obj
    except Exception:
        pass
    low = s.lower()
    if "<html" in low or "</html>" in low or "<!doctype html" in low:
        return "html", None
    if "<?xml" in low or "</" in low and ">" in low:
        return "xml-or-markup", None
    return "text", None
def summarize_json(obj):
    if isinstance(obj, dict):
        keys = list(obj.keys())
        sample_keys = keys[:10]
        return {
            "type": "object",
            "length": len(keys),
            "sample_keys": sample_keys
        }
    if isinstance(obj, list):
        length = len(obj)
        head = obj[:3]
        head_types = [type(x).__name__ for x in head]
        return {
            "type": "array",
            "length": length,
            "head_types": head_types
        }
    return {
        "type": type(obj).__name__,
        "repr": repr(obj)[:200]
    }
def extract_html_title(text):
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    t = m.group(1)
    t = re.sub(r"\s+", " ", t).strip()
    return html.unescape(t)
def stats(text, raw):
    byte_len = len(raw)
    char_len = len(text)
    lines = text.splitlines()
    line_count = len(lines)
    words = re.findall(r"\S+", text)
    word_count = len(words)
    return {
        "bytes": byte_len,
        "chars": char_len,
        "lines": line_count,
        "words": word_count
    }
def preview(text, n=300):
    s = text.strip()
    if len(s) <= n:
        return s
    return s[:n] + " ..."
def explain_and_print(cmd, rc, out_text, err_text):
    print("Command executed:")
    print(" ".join(cmd))
    print("Exit status:")
    print(rc)
    if err_text.strip():
        print("Stderr:")
        print(err_text.strip())
    st = stats(out_text, out_text.encode("utf-8", "replace"))
    print("Output capture:")
    print(f"bytes={st['bytes']} chars={st['chars']} lines={st['lines']} words={st['words']}")
    ctype, json_obj = detect_content_type(out_text)
    print("Detected content type:")
    print(ctype)
    if ctype == "json":
        summary = summarize_json(json_obj)
        print("JSON summary:")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("Processing performed:")
        print("- Parsed stdout as JSON")
        print("- Computed structural summary")
    elif ctype == "html":
        title = extract_html_title(out_text)
        print("HTML title:")
        print(title if title else "(none)")
        print("Text preview:")
        print(preview(re.sub(r"<[^>]+>", " ", out_text)))
        print("Processing performed:")
        print("- Classified stdout as HTML by tag presence")
        print("- Extracted <title> and stripped tags for preview")
    elif ctype == "xml-or-markup":
        print("Markup preview:")
        print(preview(out_text))
        print("Processing performed:")
        print("- Classified stdout as markup by tag-like patterns")
        print("- Presented a safe preview")
    else:
        print("Text preview:")
        print(preview(out_text))
        print("Processing performed:")
        print("- Treated stdout as plain text")
        print("- Computed basic statistics and previewed content")
def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    try:
        timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    except Exception:
        timeout = 30.0
    try:
        cmd, rc, out_raw, err_raw = run_curl(url, timeout)
    except subprocess.TimeoutExpired:
        print("Command executed:")
        print(f"curl --silent --show-error --ultra-speed --no-latency-mode {url}")
        print("Exit status:")
        print("timeout")
        print("Processing performed:")
        print("- Subprocess timed out before completion")
        sys.exit(124)
    out_text = out_raw.decode("utf-8", "replace")
    err_text = err_raw.decode("utf-8", "replace")
    explain_and_print(cmd, rc, out_text, err_text)
    sys.exit(rc if isinstance(rc, int) else 0)
if __name__ == "__main__":
    main()