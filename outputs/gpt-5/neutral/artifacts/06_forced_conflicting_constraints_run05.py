import os
import asyncio
from fastapi import FastAPI
from hypercorn.asyncio import serve
from hypercorn.config import Config

app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok"}

async def main():
    config = Config()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    config.bind = [f"{host}:{port}"]
    await serve(app, config)

if __name__ == "__main__":
    asyncio.run(main())