import sqlite3

def list_tokens():
    conn = sqlite3.connect('instance/queue.db')
    cursor = conn.cursor()

    print("--- 1. Specific Tokens (T-5381, T-3046) ---")
    cursor.execute("SELECT id, token_number, patient_id, doctor_id, appointment_id, status, completed_at FROM token WHERE token_number IN ('T-5381', 'T-3046');")
    for row in cursor.fetchall():
        print(row)

    print("\n--- 2. All Tokens ---")
    cursor.execute("SELECT id, token_number, status FROM token;")
    for row in cursor.fetchall():
        print(row)

    conn.close()

if __name__ == '__main__':
    list_tokens()
