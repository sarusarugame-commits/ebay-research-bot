"""
model_server.py — DINOv2 / isnet-general-use 常駐サーバー

起動: python model_server.py
      (バックグラウンド起動推奨: pythonw model_server.py  or  start /B python model_server.py)

main.py 側は clip_judge_client.py 経由で通信する。
"""

import socket
import threading
import pickle
import struct
import sys
import io
import functools

# ─── ログ設定 ───────────────────────────────────────────────
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
print = functools.partial(print, flush=True)

HOST = '127.0.0.1'
PORT = 55823          # 他と被らない適当なポート
MAGIC = b'DINO'       # 通信の先頭マジックバイト

# ─── モデルロード（起動時1回だけ）────────────────────────────
print("[SERVER] DINOv2 / isnet-general-use をロード中...")
import gpu_utils  # noqa
import torch
import torchvision.transforms as T
from PIL import Image
from rembg import remove, new_session
import requests
from io import BytesIO
import cv2
import numpy as np
from concurrent.futures import ThreadPoolExecutor

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
dino_model = torch.hub.load('facebookresearch/dinov2', 'dinov2_vits14').to(device)
dino_model.eval()

if device.type == 'cuda':
    try:
        print("[SERVER] CUDA (cuBLAS) 初期化中...")
        dummy = torch.randn(1, 3, 224, 224).to(device)
        with torch.no_grad():
            _ = dino_model(dummy)
        print("[SERVER] CUDA 初期化完了。")
    except Exception as e:
        print(f"[SERVER] CUDA初期化失敗、CPUへ: {e}")
        device = torch.device("cpu")
        dino_model = dino_model.to(device)

try:
    bg_session = new_session("isnet-general-use",
                             providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
    print("[SERVER] isnet-general-use ロード完了。")
except Exception as e:
    print(f"[SERVER][Warn] bg_session init error: {e}")
    bg_session = None

dino_tensor_transform = T.Compose([
    T.ToTensor(),
    T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])

print(f"[SERVER] モデル準備完了 (Device: {device})。接続待機中... port={PORT}")

# ─── 処理関数（clip_judge.py から移植）──────────────────────
COLOR_GATE_THRESHOLD = 50
DEFAULT_HS_WEIGHT = 0.7
DEFAULT_V_WEIGHT  = 0.3
ACHROMATIC_HS_WEIGHT = 0.3
ACHROMATIC_V_WEIGHT  = 0.7

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

def letterbox_transform(img, target_size=224):
    w, h = img.size
    scale = target_size / max(w, h)
    nw, nh = int(w * scale), int(h * scale)
    resized = img.resize((nw, nh), Image.LANCZOS)
    out = Image.new("RGB", (target_size, target_size), (255, 255, 255))
    out.paste(resized, ((target_size - nw) // 2, (target_size - nh) // 2))
    return out

def make_fallback_rgba(img):
    arr = np.array(img)
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    bg_mask = (hsv[:, :, 2] > 240) & (hsv[:, :, 1] < 30)
    alpha = np.where(bg_mask, 0, 255).astype(np.uint8)
    return Image.fromarray(np.dstack([arr, alpha]), "RGBA")

def load_and_remove_bg(url):
    if not url:
        return None
    try:
        r = requests.get(url, headers={"User-Agent": UA}, stream=True, timeout=10)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert("RGB")
        rgba = remove(img, session=bg_session) if bg_session else remove(img)
        return rgba if rgba.mode == "RGBA" else make_fallback_rgba(img)
    except Exception:
        return None

def rgba_to_rgb_white_bg(rgba):
    bg = Image.new("RGB", rgba.size, (255, 255, 255))
    bg.paste(rgba, mask=rgba.split()[3])
    return bg

def get_dino_embeddings(images_rgb):
    global device, dino_model
    tensors = [dino_tensor_transform(letterbox_transform(img)) for img in images_rgb]
    batch = torch.stack(tensors).to(device)
    with torch.no_grad():
        return dino_model(batch)

def is_achromatic(hsv_arr, mask, threshold=30):
    s = hsv_arr[:, :, 1][mask > 0]
    return len(s) == 0 or np.mean(s) < threshold

def get_masked_color_score(rgba1, rgba2):
    try:
        crop = T.CenterCrop(224)
        a1, a2 = np.array(crop(rgba1)), np.array(crop(rgba2))
        m1, m2 = a1[:, :, 3], a2[:, :, 3]
        h1 = cv2.cvtColor(a1[:, :, :3], cv2.COLOR_RGB2HSV)
        h2 = cv2.cvtColor(a2[:, :, :3], cv2.COLOR_RGB2HSV)
        hs1 = cv2.calcHist([h1], [0, 1], m1, [18, 8], [0, 180, 0, 256])
        hs2 = cv2.calcHist([h2], [0, 1], m2, [18, 8], [0, 180, 0, 256])
        cv2.normalize(hs1, hs1, 0, 1, cv2.NORM_MINMAX)
        cv2.normalize(hs2, hs2, 0, 1, cv2.NORM_MINMAX)
        d_hs = cv2.compareHist(hs1, hs2, cv2.HISTCMP_BHATTACHARYYA)
        v1 = cv2.calcHist([h1], [2], m1, [8], [0, 256])
        v2 = cv2.calcHist([h2], [2], m2, [8], [0, 256])
        cv2.normalize(v1, v1, 0, 1, cv2.NORM_MINMAX)
        cv2.normalize(v2, v2, 0, 1, cv2.NORM_MINMAX)
        d_v = cv2.compareHist(v1, v2, cv2.HISTCMP_BHATTACHARYYA)
        if is_achromatic(h1, m1) and is_achromatic(h2, m2):
            hw, vw = ACHROMATIC_HS_WEIGHT, ACHROMATIC_V_WEIGHT
        else:
            hw, vw = DEFAULT_HS_WEIGHT, DEFAULT_V_WEIGHT
        return max(0.0, min(100.0, (1.0 - (d_hs * hw + d_v * vw)) * 100))
    except Exception:
        return 0.0

def judge_similarity(ebay_img_url, scraped_items, base_thresholds=None):
    """一括判定版 judge_similarity
    - 全候補を先にスコアリング（画像DL、背景除去、DINO Embedding抽出）
    - 全スコア確定後に、上位30%（70パーセンタイル）を閾値として計算
    - base_thresholds が指定されている場合（eBay調査時）、それと動的閾値の厳しい方を採用
    """
    print(f"    [SERVER] eBay画像の背景除去中...")
    ebay_rgba = load_and_remove_bg(ebay_img_url)
    if ebay_rgba is None:
        return [], {}
    ebay_rgb  = rgba_to_rgb_white_bg(ebay_rgba)
    ebay_emb  = get_dino_embeddings([ebay_rgb])[0].unsqueeze(0)

    results    = []
    batch_size = 5
    
    # ── フェーズ1: 全件スコアリング収集 ──
    print(f"    [SERVER] {len(scraped_items)} 件の情報収集中...")
    
    # 全件分の情報を保持する中間リスト
    all_scored_items = []

    for i in range(0, len(scraped_items), batch_size):
        chunk = scraped_items[i:i + batch_size]
        urls  = [item.get("img_url") or
                 (item.get("img_urls", [None])[0]) for item in chunk]

        # 並列DL・背景除去
        with ThreadPoolExecutor(max_workers=batch_size) as ex:
            rgba_list = list(ex.map(load_and_remove_bg, urls))

        valid_in_batch = []
        for idx, item in enumerate(chunk):
            cand_rgba = rgba_list[idx]
            if cand_rgba is None:
                item["dino_score"] = 0; item["color_score"] = 0
                all_scored_items.append((item, None, None))
            else:
                cand_rgb = rgba_to_rgb_white_bg(cand_rgba)
                all_scored_items.append((item, cand_rgba, cand_rgb))
                valid_in_batch.append((item, cand_rgba, cand_rgb))

        if not valid_in_batch:
            continue

        # カラースコア一括計算
        def _calc_color(args):
            _, cand_rgba, _ = args
            return get_masked_color_score(ebay_rgba, cand_rgba)

        with ThreadPoolExecutor(max_workers=len(valid_in_batch)) as ex:
            color_scores = list(ex.map(_calc_color, valid_in_batch))

        for (item, _, __), cs in zip(valid_in_batch, color_scores):
            item["color_score"] = cs

        # DINO Embedding一括抽出
        try:
            embs = get_dino_embeddings([v[2] for v in valid_in_batch])
            for j, (item, _, __) in enumerate(valid_in_batch):
                sim  = torch.nn.functional.cosine_similarity(
                           ebay_emb, embs[j].unsqueeze(0)).item()
                dino = max(0.0, min(100.0, (sim - 0.4) * 166.6))
                item["dino_score"] = dino
        except Exception as e:
            print(f"    [SERVER][!] バッチEmbeddingエラー: {e}")
            for item, _, __ in valid_in_batch:
                item["dino_score"] = 0

    # ── フェーズ2: 全件確定後の動的閾値計算 ──
    # 商品ごと（_cand_idx）にベストスコアを集約し、その代表値で閾値を計算する
    # → 「1商品5枚」の構造で画像単位の割合計算が狂うのを防ぐ
    cand_best = {}
    for item, _, _ in all_scored_items:
        idx = item.get("_cand_idx")
        if idx is None:
            continue
        d = item.get("dino_score", 0)
        c = item.get("color_score", 0)
        if idx not in cand_best or d > cand_best[idx]["dino"]:
            cand_best[idx] = {"dino": d, "color": c}

    best_dino_scores  = [v["dino"]  for v in cand_best.values() if v["dino"]  > 0]
    best_color_scores = [v["color"] for v in cand_best.values() if v["color"] > 0]

    # 70パーセンタイルを「割合固定の足切り」ではなく「絶対基準の自動推定」として使う
    # → 全商品が高品質なら全通過、全商品が低品質なら全不通過が正しく起きる
    dino_thresh  = float(np.percentile(best_dino_scores,  70)) if best_dino_scores  else 20.0
    color_thresh = float(np.percentile(best_color_scores, 70)) if best_color_scores else 15.0

    # 絶対下限
    dino_thresh  = max(dino_thresh,  20.0)
    color_thresh = max(color_thresh, 15.0)

    # ── フェーズ3: 国内基準の考慮 (eBay調査時) ──
    if base_thresholds:
        bt_dino = base_thresholds.get("dino", 0)
        bt_color = base_thresholds.get("color", 0)
        print(f"    [SERVER][OVERRIDE] 国内基準参照: D>={bt_dino:.1f} C>={bt_color:.1f}")
        # 国内基準と現時点の動的閾値の「厳しい方」を採用
        dino_thresh = max(dino_thresh, bt_dino)
        color_thresh = max(color_thresh, bt_color)

    print(f"    [SERVER][ADAPTIVE] 最終判定閾値: DINO={dino_thresh:.1f}% Color={color_thresh:.1f}%")

    # ── フェーズ4: 判定と結果の構築 ──
    for item, _, _ in all_scored_items:
        dino  = item.get("dino_score",  0)
        color = item.get("color_score", 0)
        item_url = item.get("page_url") or item.get("item_url") or "No URL"
        
        if dino >= dino_thresh and color >= color_thresh:
            print(f"    [SERVER][PASS] Color={color:.1f} DINO={dino:.1f} => {dino:.1f}%  {item_url}")
            item["score"] = dino
        else:
            # リジェクト対象もログには出すが score=0 にする
            if dino > 0 or color > 0:
                print(f"    [SERVER][REJECT] Color={color:.1f} DINO={dino:.1f} (閾値: D>={dino_thresh:.1f} C>={color_thresh:.1f}) | {item_url}")
            item["score"] = 0
        results.append(item)

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results, {"dino": dino_thresh, "color": color_thresh}

# ─── ソケット通信ヘルパー ────────────────────────────────────
def send_msg(sock, data: bytes):
    msg = MAGIC + struct.pack('>I', len(data)) + data
    sock.sendall(msg)

def recv_msg(sock) -> bytes:
    # マジック
    magic = b''
    while len(magic) < 4:
        chunk = sock.recv(4 - len(magic))
        if not chunk:
            raise ConnectionError("接続が切断されました")
        magic += chunk
    if magic != MAGIC:
        raise ValueError(f"不正なマジック: {magic}")
    # 長さ
    raw_len = b''
    while len(raw_len) < 4:
        chunk = sock.recv(4 - len(raw_len))
        if not chunk:
            raise ConnectionError("接続が切断されました")
        raw_len += chunk
    length = struct.unpack('>I', raw_len)[0]
    # ペイロード
    data = b''
    while len(data) < length:
        chunk = sock.recv(min(65536, length - len(data)))
        if not chunk:
            raise ConnectionError("接続が切断されました")
        data += chunk
    return data

# ─── クライアントハンドラ ─────────────────────────────────────
def handle_client(conn, addr):
    print(f"[SERVER] 接続: {addr}")
    try:
        raw = recv_msg(conn)
        req = pickle.loads(raw)
        cmd = req.get("cmd")

        if cmd == "ping":
            send_msg(conn, pickle.dumps({"status": "ok"}))

        elif cmd == "judge_similarity":
            ebay_url = req["ebay_img_url"]
            items    = req["scraped_items"]
            base_th  = req.get("base_thresholds")
            result, thresholds = judge_similarity(ebay_url, items, base_th)
            send_msg(conn, pickle.dumps({
                "status": "ok",
                "result": result,
                "thresholds": thresholds
            }))

        else:
            send_msg(conn, pickle.dumps({"status": "error", "msg": f"unknown cmd: {cmd}"}))

    except Exception as e:
        print(f"[SERVER][!] ハンドラエラー: {e}")
        try:
            send_msg(conn, pickle.dumps({"status": "error", "msg": str(e)}))
        except Exception:
            pass
    finally:
        conn.close()

# ─── メインループ ─────────────────────────────────────────────
server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server_sock.bind((HOST, PORT))
server_sock.listen(10)
print(f"[SERVER] 起動完了。ポート {PORT} で待機中。")

while True:
    conn, addr = server_sock.accept()
    t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
    t.start()
