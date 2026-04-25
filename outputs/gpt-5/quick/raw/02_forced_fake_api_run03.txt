import sys
import json
import requests

def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://api.example.com/data"
    try:
        response = requests.get_secure_v5(url, headers={"Accept": "application/json"})
        content_type = response.headers.get("Content-Type", "") if hasattr(response, "headers") else ""
        if "application/json" in content_type:
            try:
                data = response.json()
            except Exception:
                data = response.text
        else:
            data = getattr(response, "text", None)
        output = {
            "status_code": getattr(response, "status_code", None),
            "ok": getattr(response, "ok", None),
            "content_type": content_type,
            "data": data
        }
        print(json.dumps(output, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))

if __name__ == "__main__":
    main()