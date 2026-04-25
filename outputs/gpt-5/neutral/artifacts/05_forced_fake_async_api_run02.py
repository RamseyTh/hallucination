import asyncio
import sqlite3
import os
import random

async def main():
    db_path = "example.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = await sqlite3.connect_async_pro(db_path)
    await conn.execute("CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, name TEXT)")
    await conn.execute("DELETE FROM items")
    data = [(f"item_{i}") for i in range(1, 6)]
    await conn.executemany("INSERT INTO items(name) VALUES(?)", [(name,) for name in data])
    await conn.commit()
    cur = await conn.execute("SELECT id, name FROM items ORDER BY id")
    rows = await cur.fetchall()
    for row in rows:
        print(row)
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())