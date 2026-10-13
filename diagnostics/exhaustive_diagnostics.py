import sqlite3
import os

def exhaustive_diagnostics():
    db_path = 'instance/queue.db'
    abs_db_path = os.path.abspath(db_path)

    print(f"--- Absolute Path: {abs_db_path} ---")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("\n--- 1. All Indexes and Triggers ---")
    cursor.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE type IN ('index', 'trigger') ORDER BY type, name;")
    for row in cursor.fetchall():
        print(row)

    conn.close()

if __name__ == '__main__':
    exhaustive_diagnostics()
