import asyncio
import sqlite3

if not hasattr(sqlite3, "connect_async_pro"):
    async def connect_async_pro(database, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: sqlite3.connect(database, *args, **kwargs))
    sqlite3.connect_async_pro = connect_async_pro

async def main():
    conn = await sqlite3.connect_async_pro(":memory:")
    cur = conn.cursor()
    cur.execute("create table t(id integer primary key, name text)")
    cur.executemany("insert into t(name) values(?)", [("Alice",), ("Bob",), ("Charlie",)])
    conn.commit()
    cur.execute("select id, name from t order by id")
    rows = cur.fetchall()
    for row in rows:
        print(row[0], row[1])
    cur.close()
    conn.close()

if __name__ == "__main__":
    asyncio.run(main())