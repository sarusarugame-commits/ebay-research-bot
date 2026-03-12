"""
clip_judge_client.py — model_server.py への通信クライアント

main.py / validate_ebay_search_v3.py は
  from clip_judge_client import judge_similarity
に変えるだけで既存コードが動く。

サーバーが落ちていれば自動起動する。
"""

import socket
import pickle
import struct
import subprocess
import time
import sys
import os

HOST  = '127.0.0.1'
PORT  = 55823
MAGIC = b'DINO'

# model_server.py のパス（このファイルと同じディレクトリ）
_SERVER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_server.py")

# ─── 通信ヘルパー ─────────────────────────────────────────────
def _send_msg(sock, data: bytes):
    sock.sendall(MAGIC + struct.pack('>I', len(data)) + data)

def _recv_msg(sock) -> bytes:
    magic = b''
    while len(magic) < 4:
        chunk = sock.recv(4 - len(magic))
        if not chunk:
            raise ConnectionError("切断")
        magic += chunk
    if magic != MAGIC:
        raise ValueError(f"不正なマジック: {magic}")
    raw_len = b''
    while len(raw_len) < 4:
        chunk = sock.recv(4 - len(raw_len))
        if not chunk:
            raise ConnectionError("切断")
        raw_len += chunk
    length = struct.unpack('>I', raw_len)[0]
    data = b''
    while len(data) < length:
        chunk = sock.recv(min(65536, length - len(data)))
        if not chunk:
            raise ConnectionError("切断")
        data += chunk
    return data

def _call(req: dict, timeout: int = 300) -> dict:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect((HOST, PORT))
    try:
        _send_msg(sock, pickle.dumps(req))
        raw = _recv_msg(sock)
        return pickle.loads(raw)
    finally:
        sock.close()

# ─── サーバー起動ヘルパー ─────────────────────────────────────
def _is_server_alive() -> bool:
    try:
        res = _call({"cmd": "ping"}, timeout=3)
        return res.get("status") == "ok"
    except Exception:
        return False

def _start_server():
    print("[*] model_server.py を起動しています（初回のみ時間がかかります）...")
    # Windows: 新しいコンソールウィンドウで起動
    if sys.platform == "win32":
        subprocess.Popen(
            [sys.executable, _SERVER_SCRIPT],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    else:
        subprocess.Popen(
            [sys.executable, _SERVER_SCRIPT],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

def ensure_server(max_wait: int = 120):
    """サーバーが生きていなければ起動して待機"""
    if _is_server_alive():
        return  # 既に起動済み → 即リターン

    _start_server()
    print("[*] モデルロード完了を待機中", end="", flush=True)
    for _ in range(max_wait):
        time.sleep(1)
        print(".", end="", flush=True)
        if _is_server_alive():
            print(" 完了！")
            return
    print()
    raise RuntimeError(f"model_server が {max_wait}秒 以内に起動しませんでした。")

# ─── 公開API（既存コードの from clip_judge import judge_similarity を置換）──
def judge_similarity(ebay_img_url: str, scraped_items: list, base_thresholds: dict = None) -> (list, dict):
    """
    戻り値: (判定済みアイテムリスト, 算出した閾値辞書)
    """
    ensure_server()
    res = _call({
        "cmd": "judge_similarity",
        "ebay_img_url": ebay_img_url,
        "scraped_items": scraped_items,
        "base_thresholds": base_thresholds,
    }, timeout=300)
    if res.get("status") != "ok":
        raise RuntimeError(f"サーバーエラー: {res.get('msg')}")
    
    # 互換性のため、もし呼び出し元が単一の戻り値を期待している場合は
    # (result, thresholds) のタプルとして返る。
    return res["result"], res.get("thresholds", {})
