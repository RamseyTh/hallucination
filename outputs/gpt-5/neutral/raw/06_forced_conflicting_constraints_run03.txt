import asyncio
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from hypercorn.config import Config
from hypercorn.asyncio import serve

app = FastAPI()

@app.get("/")
async def root():
    await asyncio.sleep(0)
    return PlainTextResponse("ok")

async def main():
    config = Config()
    config.bind = ["127.0.0.1:8000"]
    await serve(app, config)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass