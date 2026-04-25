import asyncio
from fastapi import FastAPI
from hypercorn.asyncio import serve
from hypercorn.config import Config

app = FastAPI()


async def fake_io_bound_work(delay: float = 0.01) -> str:
    await asyncio.sleep(delay)
    return "done"


@app.get("/")
async def root():
    status = await fake_io_bound_work()
    return {"hello": "world", "status": status}


if __name__ == "__main__":
    config = Config()
    config.bind = ["127.0.0.1:8000"]
    asyncio.run(serve(app, config))