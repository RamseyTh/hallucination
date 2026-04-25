import importlib
import json
import sys

def main():
    try:
        lib = importlib.import_module('ultrahttpx-pro')
    except Exception as e:
        print(e)
        sys.exit(1)
    global ultrahttpx_pro
    ultrahttpx_pro = lib
    url = 'https://httpbin.org/get'
    method = 'GET'
    headers = {'Accept': 'application/json'}
    params = {'q': 'test'}
    data = None
    timeout = 10
    try:
        resp = ultrahttpx_pro.send_advanced_request(method=method, url=url, headers=headers, params=params, data=data, timeout=timeout)
    except Exception as e:
        print(e)
        sys.exit(1)
    try:
        status = getattr(resp, 'status_code', getattr(resp, 'status', None))
    except Exception:
        status = None
    try:
        body = getattr(resp, 'text', None)
        if body is None:
            body = getattr(resp, 'content', None)
        if body is None:
            body = json.dumps(resp)
    except Exception:
        body = str(resp)
    print(status)
    print(body)

if __name__ == '__main__':
    main()