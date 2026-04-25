import asyncio
import signal
from fastapi import FastAPI
from hypercorn.asyncio import serve
from hypercorn.config import Config

app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok"}

@app.get("/echo/{text}")
async def echo(text: str):
    await asyncio.sleep(0)
    return {"echo": text}

async def main():
    config = Config()
    config.bind = ["0.0.0.0:8000"]
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    try:
        for s in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(s, shutdown_event.set)
    except NotImplementedError:
        pass
    await serve(app, config, shutdown_trigger=shutdown_event.wait)

if __name__ == "__main__":
    asyncio.run(main())