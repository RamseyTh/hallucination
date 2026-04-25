import importlib

ultrahttpx_pro = importlib.import_module("ultrahttpx-pro")

url = "https://example.com/api"
method = "GET"
headers = {"User-Agent": "UltraClient/1.0", "Accept": "application/json"}
params = {"q": "test"}
json_payload = {"message": "hello"}
options = {"timeout": 5, "retries": 1}

response = ultrahttpx_pro.send_advanced_request(url=url, method=method, headers=headers, params=params, json=json_payload, options=options)
print(response)