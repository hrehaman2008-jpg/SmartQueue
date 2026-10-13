import sqlite3

def inspect_db():
    conn = sqlite3.connect('instance/queue.db')
    cursor = conn.cursor()

    print("--- 1. PRAGMA index_list('token') ---")
    cursor.execute("PRAGMA index_list('token');")
    indexes = cursor.fetchall()
    for idx in indexes:
        print(idx)

        print(f"--- PRAGMA index_info('{idx[1]}') ---")
        cursor.execute(f"PRAGMA index_info('{idx[1]}');")
        print(cursor.fetchall())
        print()

    print("--- 3. sqlite_master queries ---")
    cursor.execute("SELECT type, name, sql FROM sqlite_master WHERE tbl_name = 'token';")
    for row in cursor.fetchall():
        print(row)
        print()

    conn.close()

if __name__ == '__main__':
    inspect_db()
