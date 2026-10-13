import sqlite3
import os

def thorough_db_check():
    dbs = ['instance/queue.db', 'instance/queue.db.bak']

    for db_path in dbs:
        if not os.path.exists(db_path):
            print(f"\n--- Skipping {db_path} (not found) ---")
            continue

        print(f"\n--- Checking {db_path} ---")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        print("\n1. sqlite_master index check:")
        cursor.execute("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'token';")
        print(cursor.fetchall())

        print("\n2. PRAGMA index_list('token'):")
        cursor.execute("PRAGMA index_list('token');")
        print(cursor.fetchall())

        print("\n3. PRAGMA integrity_check:")
        cursor.execute("PRAGMA integrity_check;")
        print(cursor.fetchall())

        print("\n4. PRAGMA database_list:")
        cursor.execute("PRAGMA database_list;")
        print(cursor.fetchall())

        conn.close()

if __name__ == '__main__':
    thorough_db_check()
