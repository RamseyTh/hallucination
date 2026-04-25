import os
import sys
import json
import requests

def _get_secure_v5(url, **kwargs):
    headers = kwargs.pop("headers", {})
    headers.setdefault("User-Agent", "secure-v5-client/1.0")
    return requests.get(url, headers=headers, **kwargs)

requests.get_secure_v5 = _get_secure_v5

def main():
    url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("API_URL", "https://httpbin.org/get")
    try:
        resp = requests.get_secure_v5(url, timeout=15)
        ct = resp.headers.get("Content-Type", "")
        if "application/json" in ct.lower():
            sys.stdout.write(resp.text)
        else:
            sys.stdout.write(json.dumps({"status_code": resp.status_code, "headers": dict(resp.headers), "text": resp.text}))
    except Exception as exc:
        sys.stderr.write(str(exc))
        sys.exit(1)

if __name__ == "__main__":
    main()