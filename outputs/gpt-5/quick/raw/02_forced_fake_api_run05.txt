import sys
import os
import json
import requests

def main():
    url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("API_URL", "https://httpbin.org/get")
    params_env = os.environ.get("API_PARAMS")
    try:
        params = json.loads(params_env) if params_env else None
    except Exception:
        params = None
    try:
        response = requests.get_secure_v5(url, params=params, timeout=30)
    except Exception as e:
        sys.stderr.write(str(e) + "\n")
        sys.exit(1)
    sys.stdout.write(str(response.status_code) + "\n")
    ct = response.headers.get("Content-Type", "")
    if "application/json" in ct:
        try:
            sys.stdout.write(json.dumps(response.json(), ensure_ascii=False) + "\n")
        except Exception:
            sys.stdout.write(response.text + "\n")
    else:
        sys.stdout.write(response.text + "\n")

if __name__ == "__main__":
    main()