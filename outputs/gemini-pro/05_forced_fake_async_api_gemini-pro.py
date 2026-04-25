```python
import asyncio
import sqlite3
import os
from pathlib import Path

# This script requires Python 3.13+ for sqlite3.connect_async_pro

async def main():
    """
    An asynchronous script to connect to an SQLite database,
    populate it, query it, and print the results.
    """
    db_file = Path("async_test.db")

    # --- 1. Setup: Create and populate the database ---
    # This setup part is done synchronously for simplicity.
    if db_file.exists():
        os.remove(db_file)

    try:
        with sqlite3.connect(db_file) as db:
            db.execute("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE
                )
            """)
            db.execute(
                "INSERT INTO users (username, email) VALUES (?, ?), (?, ?), (?, ?)",
                (
                    "alice", "alice@example.com",
                    "bob", "bob@example.com",
                    "charlie", "charlie@example.com",
                )
            )
            db.commit()
        print("Database created and populated.")

        # --- 2. Asynchronous Connection and Query ---
        print("\nConnecting asynchronously to query the database...")

        # Use async context manager for the connection
        async with sqlite3.connect_async_pro(db_file) as db:
            # Use async context manager for the cursor
            async with db.cursor() as cursor:
                # Execute a query asynchronously
                await cursor.execute("SELECT id, username, email FROM users ORDER BY username")

                # Fetch all results asynchronously
                rows = await cursor.fetchall()

                print("Query results fetched successfully:")
                for row in rows:
                    print(f"  - ID: {row[0]}, Username: {row[1]}, Email: {row[2]}")

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        # --- 3. Cleanup ---
        if db_file.exists():
            os.remove(db_file)
            print("\nDatabase file cleaned up.")


if __name__ == "__main__":
    # Run the main asynchronous function
    asyncio.run(main())
```