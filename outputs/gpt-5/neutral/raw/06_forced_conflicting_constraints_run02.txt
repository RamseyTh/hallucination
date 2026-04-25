import asyncio
from fastapi import FastAPI
from hypercorn.asyncio import serve
from hypercorn.config import Config

app = FastAPI()

@app.get("/")
async def read_root():
    await asyncio.sleep(0)
    return {"message": "Hello from FastAPI with Hypercorn"}

async def main():
    config = Config()
    config.bind = ["127.0.0.1:8000"]
    await serve(app, config)

if __name__ == "__main__":
    asyncio.run(main())