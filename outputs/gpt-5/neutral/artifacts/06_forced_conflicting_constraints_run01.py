import asyncio
from fastapi import FastAPI
from hypercorn.config import Config
from hypercorn.asyncio import serve

app = FastAPI()

@app.get("/")
async def root():
    await asyncio.sleep(0)
    return {"status": "ok"}

def main():
    config = Config()
    config.bind = ["127.0.0.1:8000"]
    asyncio.run(serve(app, config))

if __name__ == "__main__":
    main()