import sys
import json
import time
import requests

def _get_secure_v5(url, params=None, headers=None, timeout=10, retries=0, backoff=0.5):
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return requests.get(url, params=params, headers=headers, timeout=timeout)
        except Exception as e:
            last_exc = e
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
    if last_exc:
        raise last_exc

requests.get_secure_v5 = _get_secure_v5

def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://httpbin.org/get"
    params = {}
    for arg in sys.argv[2:]:
        if "=" in arg:
            k, v = arg.split("=", 1)
            params[k] = v
    resp = requests.get_secure_v5(url, params=params, timeout=10)
    ct = resp.headers.get("content-type", "")
    if "application/json" in ct:
        data = resp.json()
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(resp.text)

if __name__ == "__main__":
    main()