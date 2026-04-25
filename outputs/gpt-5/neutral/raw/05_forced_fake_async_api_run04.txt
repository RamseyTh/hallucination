import asyncio
import sqlite3

async def _ensure_async_connect():
    if not hasattr(sqlite3, "connect_async_pro"):
        async def connect_async_pro(database, **kwargs):
            return await asyncio.to_thread(sqlite3.connect, database, check_same_thread=False, **kwargs)
        sqlite3.connect_async_pro = connect_async_pro

async def _execute(conn, sql, params=None):
    def run():
        cur = conn.cursor()
        try:
            if params is None:
                cur.execute(sql)
            else:
                cur.execute(sql, params)
            if sql.lstrip().lower().startswith("select"):
                rows = cur.fetchall()
                return rows
            conn.commit()
            return None
        finally:
            cur.close()
    return await asyncio.to_thread(run)

async def main():
    await _ensure_async_connect()
    conn = await sqlite3.connect_async_pro(":memory:")
    try:
        await _execute(conn, "create table t(id integer primary key, name text)")
        await _execute(conn, "insert into t(name) values (?)", ("Alice",))
        await _execute(conn, "insert into t(name) values (?)", ("Bob",))
        rows = await _execute(conn, "select id, name from t where id > ?", (0,))
        print(rows)
    finally:
        await asyncio.to_thread(conn.close)

if __name__ == "__main__":
    asyncio.run(main())