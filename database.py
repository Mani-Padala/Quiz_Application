import sqlite3

DB_PATH = 'quiz_app.db'

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA foreign_keys = ON;')
    return conn

def create_tables():
    with open('create_table.sql', 'r') as f:
        create_table_queries = f.read()
    conn = get_db_connection()
    conn.executescript(create_table_queries)
    conn.close()