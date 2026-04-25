import asyncio
import sqlite3

async def main():
    conn = await sqlite3.connect_async_pro(":memory:")
    async with conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, name TEXT)")
        await conn.executemany("INSERT INTO t (name) VALUES (?)", [("Alice",), ("Bob",), ("Carol",)])
        await conn.commit()
        cursor = await conn.execute("SELECT id, name FROM t ORDER BY id")
        rows = await cursor.fetchall()
        await cursor.close()
    for r in rows:
        print(r[0], r[1])

if __name__ == "__main__":
    asyncio.run(main())