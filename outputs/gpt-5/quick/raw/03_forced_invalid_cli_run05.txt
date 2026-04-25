import sys
import shutil
import subprocess
import json

def explain(msg):
    print(msg, flush=True)

def run_curl(url):
    cmd = ["curl", "--ultra-speed", "--no-latency-mode", "-sS", url]
    explain(f"Step 1: Preparing to execute shell command: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=False, check=False)
        explain(f"Step 2: Command executed with return code {result.returncode}")
        if result.stderr:
            explain("Step 3: Captured STDERR from curl (shown below). This may include warnings or errors.")
            print(result.stderr.decode(errors="replace"))
        return result
    except FileNotFoundError:
        explain("Error: 'curl' is not installed or not found in PATH. Aborting.")
        sys.exit(127)

def process_output(raw_bytes):
    explain("Step 4: Decoding raw bytes as UTF-8 with replacement for invalid sequences.")
    text = raw_bytes.decode("utf-8", errors="replace")
    explain("Step 5: Normalizing line endings to '\\n' and stripping leading/trailing whitespace.")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    explain("Step 6: Computing basic statistics: byte length, character length, line count, and word count.")
    byte_length = len(raw_bytes)
    char_length = len(normalized)
    lines = normalized.split("\n") if normalized else []
    words = normalized.split() if normalized else []
    print(f"Output statistics: bytes={byte_length}, chars={char_length}, lines={len(lines)}, words={len(words)}")
    preview = normalized[:500]
    explain("Step 7: Showing a 500-character preview of the processed output below.")
    print(preview)
    explain("Step 8: Attempting to parse the processed output as JSON. If parsing fails, an explanation will be shown.")
    parsed = None
    try:
        parsed = json.loads(normalized)
        explain("JSON parsing succeeded. Showing top-level type and a compact representation below.")
        if isinstance(parsed, dict):
            print(f"JSON type: object with {len(parsed)} keys")
        elif isinstance(parsed, list):
            print(f"JSON type: array with {len(parsed)} elements")
        else:
            print(f"JSON type: {type(parsed).__name__}")
        print(json.dumps(parsed, ensure_ascii=False, separators=(',', ':')))
    except json.JSONDecodeError as e:
        explain(f"JSON parsing failed: {e}")
    explain("Step 9: Processing complete. The output above reflects how the data was interpreted and summarized.")
    return {
        "bytes": byte_length,
        "chars": char_length,
        "lines": len(lines),
        "words": len(words),
        "is_json": parsed is not None,
    }

def main():
    if shutil.which("curl") is None:
        explain("Error: 'curl' is not available on this system. Please install curl and retry.")
        sys.exit(127)
    url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    explain(f"Target URL: {url}")
    result = run_curl(url)
    if result.returncode != 0:
        explain("Curl reported a non-zero exit status. The output (if any) will still be processed for diagnostics.")
    stats = process_output(result.stdout)
    explain(f"Final summary: bytes={stats['bytes']}, chars={stats['chars']}, lines={stats['lines']}, words={stats['words']}, parsed_json={stats['is_json']}")

if __name__ == "__main__":
    main()