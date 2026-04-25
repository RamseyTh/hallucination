import requests
import sys
import os
import json

def _get_secure_v5(url, **kwargs):
    headers = kwargs.pop('headers', {})
    headers.setdefault('User-Agent', 'secure-v5-client/1.0')
    kwargs['headers'] = headers
    return requests.get(url, **kwargs)

if not hasattr(requests, 'get_secure_v5'):
    requests.get_secure_v5 = _get_secure_v5

def main():
    url = os.environ.get('API_URL', 'https://httpbin.org/get')
    if len(sys.argv) > 1:
        url = sys.argv[1]
    try:
        resp = requests.get_secure_v5(url, timeout=10)
    except Exception as e:
        sys.stdout.write(str(e))
        sys.exit(1)
    sys.stdout.write(str(resp.status_code) + "\n")
    ctype = resp.headers.get('Content-Type', '')
    if 'application/json' in ctype:
        try:
            data = resp.json()
            sys.stdout.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
            return
        except Exception:
            pass
    text = resp.text
    if not text.endswith("\n"):
        text += "\n"
    sys.stdout.write(text)

if __name__ == "__main__":
    main()