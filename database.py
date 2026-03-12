import sqlite3
import os

import os as _os
# database.py自身と同じディレクトリにDBを置く（実行ディレクトリに依存しない）
DB_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'researched_items.db')

def get_connection():
    return sqlite3.connect(DB_PATH)

# セッションごとのトークン使用量をメモリ上で保持
_session_usage = {"input": 0, "output": 0, "thinking": 0}

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

        # トークン使用量記録用テーブル
        c.execute('''CREATE TABLE IF NOT EXISTS token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE DEFAULT (date('now', 'localtime')),
            model TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            timestamp DATETIME DEFAULT (datetime('now', 'localtime'))
        )''')
        
        # token_usage テーブルへの思考トークンカラム追加
        c.execute("PRAGMA table_info(token_usage)")
        token_usage_cols = [row[1] for row in c.fetchall()]
        if "thinking_tokens" not in token_usage_cols:
            print("[*] DB更新: token_usage に thinking_tokens カラムを追加中...")
            try:
                c.execute("ALTER TABLE token_usage ADD COLUMN thinking_tokens INTEGER DEFAULT 0")
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

def delete_researched_item(item_id):
    """リサーチに失敗した場合などにDBから削除する"""
    if not item_id: return
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM items WHERE item_id = ?", (item_id,))
        conn.commit()
        print(f"[*] DBロールバック: 商品 ID {item_id} をリサーチ済みリストから削除しました。")
    except Exception as e:
        print(f"[!] DBロールバック失敗: {e}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()

def log_token_usage(model, input_tokens, output_tokens, thinking_tokens=0):
    """LLMの使用トークン数をDBに記録する"""
    # セッション統計を更新
    global _session_usage
    _session_usage["input"] += (input_tokens or 0)
    _session_usage["output"] += (output_tokens or 0)
    _session_usage["thinking"] += (thinking_tokens or 0)

    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("""
            INSERT INTO token_usage (model, input_tokens, output_tokens, thinking_tokens)
            VALUES (?, ?, ?, ?)
        """, (model, input_tokens, output_tokens, thinking_tokens))
        conn.commit()
    except Exception as e:
        print(f"[!] トークンログ保存失敗: {e}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()

def get_token_usage_stats():
    """本日、今月、累計のトークン使用統計を取得する"""
    stats = {}
    try:
        conn = get_connection()
        c = conn.cursor()
        
        # 今日
        c.execute("""
            SELECT SUM(input_tokens), SUM(output_tokens), SUM(thinking_tokens)
            FROM token_usage 
            WHERE date = date('now', 'localtime')
        """)
        stats['today'] = c.fetchone() or (0, 0, 0)
        
        # 今月
        c.execute("""
            SELECT SUM(input_tokens), SUM(output_tokens), SUM(thinking_tokens)
            FROM token_usage 
            WHERE strftime('%Y-%m', date) = strftime('%Y-%m', 'now', 'localtime')
        """)
        stats['month'] = c.fetchone() or (0, 0, 0)
        
        # 累計
        c.execute("SELECT SUM(input_tokens), SUM(output_tokens), SUM(thinking_tokens) FROM token_usage")
        stats['total'] = c.fetchone() or (0, 0, 0)
        
    except Exception as e:
        print(f"[!] 統計取得失敗: {e}")
        stats = {'today': (0,0,0), 'month': (0,0,0), 'total': (0,0,0)}
    finally:
        if 'conn' in locals() and conn:
            conn.close()
            
    # セッション統計を追加
    stats['session'] = (_session_usage["input"], _session_usage["output"], _session_usage["thinking"])
    return stats

setup_db()
