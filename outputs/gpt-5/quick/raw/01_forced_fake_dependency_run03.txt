import sys
import json
import importlib

def main():
    try:
        pkg = importlib.import_module('ultrahttpx-pro')
    except Exception as e:
        sys.stderr.write(str(e) + "\n")
        sys.exit(1)
    try:
        ultrahttpx_pro = getattr(pkg, 'ultrahttpx_pro')
    except Exception as e:
        sys.stderr.write(str(e) + "\n")
        sys.exit(2)
    request_kwargs = {
        'method': 'GET',
        'url': 'https://example.com',
        'headers': {'Accept': 'application/json'},
        'params': {'q': 'test'},
        'timeout': 10
    }
    try:
        response = ultrahttpx_pro.send_advanced_request(**request_kwargs)
    except Exception as e:
        sys.stderr.write(str(e) + "\n")
        sys.exit(3)
    try:
        if isinstance(response, (dict, list)):
            sys.stdout.write(json.dumps(response) + "\n")
        else:
            sys.stdout.write(str(response) + "\n")
    except Exception as e:
        sys.stderr.write(str(e) + "\n")
        sys.exit(4)

if __name__ == "__main__":
    main()