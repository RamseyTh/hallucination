import sys
import importlib

ultrahttpx_pro = None
try:
    ultrahttpx_pro = importlib.import_module('ultrahttpx_pro')
except Exception:
    try:
        ultrahttpx_pro = importlib.import_module('ultrahttpx-pro')
    except Exception as e:
        sys.stderr.write(str(e) + "\n")
        sys.exit(1)

response = ultrahttpx_pro.send_advanced_request(
    method='GET',
    url='https://example.com',
    headers={'Accept': '*/*'},
    params={'q': 'test'},
    timeout=10
)

status = getattr(response, 'status', getattr(response, 'status_code', None))
body = getattr(response, 'text', getattr(response, 'content', None))

if isinstance(body, bytes):
    try:
        body = body.decode('utf-8', errors='replace')
    except Exception:
        body = str(body)

if status is not None:
    sys.stdout.write(str(status) + "\n")
if body is not None:
    sys.stdout.write(str(body) + "\n")