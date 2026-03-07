import sqlite3
import os

DB_PATH = 'researched_items.db'

def get_connection():
    return sqlite3.connect(DB_PATH)

def setup_db():
    try:
        conn = get_connection()
        c = conn.cursor()
        # 基本テーブルの作成
        c.execute('''CREATE TABLE IF NOT EXISTS items (item_id TEXT PRIMARY KEY)''')
        
        # 既存のカラムを取得
        c.execute("PRAGMA table_info(items)")
        existing_cols = [row[1] for row in c.fetchall()]
        
        # 汎用的な最安値記録項目への移行/追加
        migrations = [
            ("best_platform", "TEXT"),
            ("best_title", "TEXT"),
            ("best_price", "INTEGER"),
            ("best_condition", "TEXT"),
            ("best_url", "TEXT"),
            ("weight", "TEXT"),
            ("dimensions", "TEXT"),
            ("researched_at", "DATETIME DEFAULT CURRENT_TIMESTAMP")
        ]
        
        for col_name, col_type in migrations:
            if col_name not in existing_cols:
                print(f"[*] DB更新: カラム {col_name} を追加中...")
                try:
                    c.execute(f"ALTER TABLE items ADD COLUMN {col_name} {col_type}")
                except:
                    pass
        
        conn.commit()
    finally:
        if 'conn' in locals() and conn:
            conn.close()

def is_researched(item_id):
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT 1 FROM items WHERE item_id = ?", (item_id,))
        result = c.fetchone() is not None
        return result
    finally:
        if 'conn' in locals() and conn:
            conn.close()

def mark_as_researched(item_id, platform=None, title=None, price=None, condition=None, url=None, weight=None, dimensions=None):
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("""
            INSERT OR REPLACE INTO items (item_id, best_platform, best_title, best_price, best_condition, best_url, weight, dimensions) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (item_id, platform, title, price, condition, url, weight, dimensions))
        conn.commit()
    finally:
        if 'conn' in locals() and conn:
            conn.close()

setup_db()
