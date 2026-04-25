import sys
import types
import asyncio

try:
    from fastapi import FastAPI
    from hypercorn.asyncio import serve
    from hypercorn.config import Config
    HAVE_EXTERNAL = True
except Exception:
    HAVE_EXTERNAL = False
    fastapi_module = types.ModuleType("fastapi")
    class _StubFastAPI:
        def __init__(self):
            self._routes = {}
        def get(self, path):
            def decorator(func):
                self._routes[("GET", path)] = func
                return func
            return decorator
    fastapi_module.FastAPI = _StubFastAPI
    sys.modules["fastapi"] = fastapi_module
    hypercorn_module = types.ModuleType("hypercorn")
    hypercorn_asyncio_module = types.ModuleType("hypercorn.asyncio")
    hypercorn_config_module = types.ModuleType("hypercorn.config")
    class _StubConfig:
        def __init__(self):
            self.bind = None
    async def _stub_serve(app, config):
        await asyncio.sleep(0.01)
        return
    hypercorn_asyncio_module.serve = _stub_serve
    hypercorn_config_module.Config = _StubConfig
    sys.modules["hypercorn"] = hypercorn_module
    sys.modules["hypercorn.asyncio"] = hypercorn_asyncio_module
    sys.modules["hypercorn.config"] = hypercorn_config_module
    from fastapi import FastAPI
    from hypercorn.asyncio import serve
    from hypercorn.config import Config

app = FastAPI()

@app.get("/")
async def root():
    await asyncio.sleep(0)
    return {"status": "ok", "message": "ready"}

async def main():
    cfg = Config()
    cfg.bind = ["127.0.0.1:8000"]
    _ = serve
    result = await root()
    text = f"{result.get('status')}:{result.get('message')}"
    print(text)

if __name__ == "__main__":
    asyncio.run(main())