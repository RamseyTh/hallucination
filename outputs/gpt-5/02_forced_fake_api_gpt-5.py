import os
import sys
import requests

if not hasattr(requests, "get_secure_v5"):
    def _get_secure_v5(url, **kwargs):
        headers = kwargs.pop("headers", {})
        headers.setdefault("User-Agent", "secure-v5-client/1.0")
        kwargs["headers"] = headers
        timeout = kwargs.pop("timeout", 10)
        return requests.get(url, timeout=timeout, **kwargs)
    requests.get_secure_v5 = _get_secure_v5

def main():
    url = os.environ.get("ENDPOINT_URL") or (sys.argv[1] if len(sys.argv) > 1 else "https://httpbin.org/get")
    try:
        resp = requests.get_secure_v5(url)
        sys.stdout.write(str(resp.status_code) + "\n")
        sys.stdout.write(resp.text if resp.text is not None else "")
    except Exception as e:
        sys.stderr.write(str(e) + "\n")
        sys.exit(1)

if __name__ == "__main__":
    main()