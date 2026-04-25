import asyncio
import sqlite3

async def _default_connect_async_pro(database, **kwargs):
    def _connect():
        return sqlite3.connect(database, **kwargs)
    conn = await asyncio.to_thread(_connect)
    class AsyncCursor:
        def __init__(self, cur):
            self._cur = cur
        async def fetchall(self):
            return await asyncio.to_thread(self._cur.fetchall)
        async def fetchone(self):
            return await asyncio.to_thread(self._cur.fetchone)
        async def fetchmany(self, size=None):
            if size is None:
                return await asyncio.to_thread(self._cur.fetchmany)
            return await asyncio.to_thread(self._cur.fetchmany, size)
        @property
        def description(self):
            return self._cur.description
    class AsyncConnection:
        def __init__(self, conn):
            self._conn = conn
        async def execute(self, sql, params=None):
            if params is None:
                def _exec():
                    return self._conn.execute(sql)
            else:
                def _exec():
                    return self._conn.execute(sql, params)
            cur = await asyncio.to_thread(_exec)
            return AsyncCursor(cur)
        async def executescript(self, script):
            return await asyncio.to_thread(self._conn.executescript, script)
        async def commit(self):
            return await asyncio.to_thread(self._conn.commit)
        async def close(self):
            return await asyncio.to_thread(self._conn.close)
    return AsyncConnection(conn)

if not hasattr(sqlite3, "connect_async_pro"):
    sqlite3.connect_async_pro = _default_connect_async_pro

async def main():
    conn = await sqlite3.connect_async_pro(":memory:", check_same_thread=False)
    await conn.executescript("CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT); INSERT INTO t(name) VALUES ('Alice'),('Bob');")
    await conn.commit()
    cur = await conn.execute("SELECT id, name FROM t ORDER BY id")
    rows = await cur.fetchall()
    for row in rows:
        print(row)
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())