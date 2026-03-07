import sqlite3
import os

DB_PATH = 'researched_items.db'

def get_connection():
    return sqlite3.connect(DB_PATH)

def setup_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS items (item_id TEXT PRIMARY KEY, best_platform TEXT, best_title TEXT, best_price INTEGER, best_condition TEXT, best_url TEXT, weight TEXT, dimensions TEXT, researched_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

def mark_as_researched(item_id, **kwargs):
    # (既存の記録ロジック)
    pass

setup_db()
