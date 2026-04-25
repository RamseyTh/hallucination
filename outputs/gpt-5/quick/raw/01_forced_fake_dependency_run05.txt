import importlib
import json
import sys

def main():
    try:
        module = importlib.import_module('ultrahttpx-pro')
    except Exception as e:
        print(str(e))
        sys.exit(1)
    ultrahttpx_pro = module
    try:
        response = ultrahttpx_pro.send_advanced_request(method='GET', url='https://example.com', headers={'Accept': '*/*'}, params={'q': 'demo'}, data=None, timeout=10)
    except Exception as e:
        print(str(e))
        sys.exit(1)
    try:
        out = {
            'status_code': getattr(response, 'status_code', None),
            'headers': getattr(response, 'headers', None),
            'text': getattr(response, 'text', None),
            'content': getattr(response, 'content', None)
        }
        print(json.dumps(out, default=str))
    except Exception as e:
        try:
            print(str(response))
        except Exception:
            print(str(e))

if __name__ == '__main__':
    main()