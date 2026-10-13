import sqlite3

def check_duplicates():
    conn = sqlite3.connect('instance/queue.db')
    cursor = conn.cursor()
    cursor.execute("SELECT patient_id, status, COUNT(*) FROM token GROUP BY patient_id, status HAVING COUNT(*) > 1;")
    rows = cursor.fetchall()
    print("Duplicate (patient_id, status) rows:", rows)
    conn.close()

if __name__ == '__main__':
    check_duplicates()
