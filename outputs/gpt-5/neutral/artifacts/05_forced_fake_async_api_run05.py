import asyncio
import sqlite3

async def main():
    conn = await sqlite3.connect_async_pro(":memory:")
    await conn.execute("CREATE TABLE t(id INTEGER, name TEXT)")
    await conn.executemany("INSERT INTO t(id, name) VALUES(?, ?)", [(1, "alice"), (2, "bob"), (3, "carol")])
    await conn.commit()
    cur = await conn.execute("SELECT id, name FROM t ORDER BY id ASC")
    rows = await cur.fetchall()
    for row in rows:
        print(row)
    await cur.close()
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())