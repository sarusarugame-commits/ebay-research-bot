import gpu_utils
import torch
import torchvision.transforms as T
from PIL import Image
import requests
from io import BytesIO
import cv2
import numpy as np
from rembg import remove, new_session
from concurrent.futures import ThreadPoolExecutor

# DINOv2モデルのロード
print("[*] DINOv2モデル (dinov2_vits14) をロードしています...")
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
dino_model = torch.hub.load('facebookresearch/dinov2', 'dinov2_vits14').to(device)
dino_model.eval()

# cuBLAS のハンドルを確実に初期化するためのダミー演算
if device.type == 'cuda':
    try:
        print("[*] CUDA (cuBLAS) を初期化しています...")
        dummy_input = torch.randn(1, 3, 224, 224).to(device)
        with torch.no_grad():
            _ = dino_model(dummy_input)
        print("[*] CUDA 初期化完了。")
    except Exception as e:
        print(f"[!] CUDA初期化中にエラーが発生しました。CPUモードへ移行します: {e}")
        device = torch.device("cpu")
        dino_model = dino_model.to(device)

print(f"[*] DINOv2モデルロード完了 (Device: {device})")

# 🏎️💨 GPU優先モード
bg_session = new_session("u2net", providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])

# from lightglue_judge import verify_with_lightglue, calculate_lightglue_score

# DINOv2用の前処理
transform = T.Compose([
    T.Resize(224),
    T.CenterCrop(224),
    T.ToTensor(),
    T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])

# ============================================================
# 【設定】Color Gate 閾値
# 前景抽出失敗時の精度低下を考慮しつつ、精度を維持するため 50 を堅持
# ============================================================
COLOR_GATE_THRESHOLD = 50

def make_fallback_rgba(img_rgb):
    """
    rembg失敗時のフォールバック。
    中央70%をクロップして周辺背景を減らし、
    HSV輝度閾値で簡易的な前景マスクを作ってRGBAを返す。
    完璧ではないが「背景込み全面」よりはるかにマシ。
    """
    w, h = img_rgb.size
    # 中央70%クロップ
    margin_x, margin_y = int(w * 0.15), int(h * 0.15)
    cropped = img_rgb.crop((margin_x, margin_y, w - margin_x, h - margin_y))

    arr = np.array(cropped)
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)

    # 白・明るいグレー背景を除外するマスク（V > 240 かつ S < 30 を背景とみなす）
    bg_mask = (hsv[:, :, 2] > 240) & (hsv[:, :, 1] < 30)
    alpha = np.where(bg_mask, 0, 255).astype(np.uint8)

    rgba = np.dstack([arr, alpha])
    result = Image.fromarray(rgba, "RGBA")
    print(f"    [WARN] rembg失敗 → 簡易マスクで代替処理しました")
    return result

def load_and_remove_bg(url, max_size=400):
    """画像をダウンロードし、リサイズ＆背景除去して RGBA画像 を返す"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    try:
        response = requests.get(url, headers=headers, stream=True, timeout=10)
        response.raise_for_status()
        img = Image.open(BytesIO(response.content)).convert("RGB")
        
        # 背景を抜く前に画像を小さくする
        img.thumbnail((max_size, max_size))
        
        img_rgba = remove(img, session=bg_session)

        # ============================================================
        # 【修正】背景除去の成否を検証する
        # rembgがRGBのまま返す・アルファが全不透明の場合は失敗とみなす
        # ============================================================
        if img_rgba.mode != "RGBA":
            print(f"    [WARN] rembgがRGBAを返しませんでした (mode={img_rgba.mode})。フォールバックします。")
            return make_fallback_rgba(img)

        alpha_arr = np.array(img_rgba)[:, :, 3]
        transparent_ratio = np.sum(alpha_arr == 0) / alpha_arr.size
        if transparent_ratio < 0.05:
            # 透過ピクセルが5%未満 = ほぼ除去できていない
            print(f"    [WARN] 背景除去がほぼ機能していません (透過率={transparent_ratio:.1%})。フォールバックします。")
            return make_fallback_rgba(img)

        return img_rgba
    except Exception as e:
        print(f"    [!] 画像ロード/背景除去エラー ({url[:50]}...): {e}")
        return None

def rgba_to_rgb_white_bg(rgba_img):
    """透過画像を白背景のRGB画像に変換（DINOv2用）"""
    background = Image.new("RGB", rgba_img.size, (255, 255, 255))
    background.paste(rgba_img, mask=rgba_img.split()[3]) 
    return background

def get_dino_embeddings(images_rgb):
    """複数画像をDINOv2でまとめてエンコードしてベクトル(batch)を返す"""
    global device, dino_model
    if not images_rgb: return None
    try:
        tensors = [transform(img) for img in images_rgb]
        batch_tensor = torch.stack(tensors).to(device)
        with torch.no_grad():
            embeddings = dino_model(batch_tensor)
        return embeddings
    except Exception as e:
        if "CUBLAS_STATUS_NOT_INITIALIZED" in str(e) and device.type == 'cuda':
            print(f"    [!] cuBLASエラーを検知しました。CPUで再試行します...")
            device = torch.device("cpu")
            dino_model = dino_model.to(device)
            tensors = [transform(img) for img in images_rgb]
            batch_tensor = torch.stack(tensors).to(device)
            with torch.no_grad():
                embeddings = dino_model(batch_tensor)
            return embeddings
        raise e

# カラー判定の重み付け定数
DEFAULT_HS_WEIGHT = 0.7
DEFAULT_V_WEIGHT = 0.3
ACHROMATIC_HS_WEIGHT = 0.3
ACHROMATIC_V_WEIGHT = 0.7

def is_achromatic(hsv_arr, mask, threshold=30):
    """
    無彩色判定（平均彩度が低い = 黒・白・シルバー系）
    """
    s_channel = hsv_arr[:, :, 1]
    masked_s = s_channel[mask > 0]
    if len(masked_s) == 0: return True
    return np.mean(masked_s) < threshold

def get_masked_color_score(rgba1, rgba2):
    """
    照明変動に強く、かつ黒い商品も判定できるハイブリッド式！
    通常は HS 70% + V 30% ですが、両者が無彩色の場合は HS 30% + V 70% に切り替えます。
    """
    try:
        arr1 = np.array(rgba1)
        arr2 = np.array(rgba2)
        
        bgr1 = cv2.cvtColor(arr1[:, :, :3], cv2.COLOR_RGB2BGR)
        mask1 = arr1[:, :, 3]
        
        bgr2 = cv2.cvtColor(arr2[:, :, :3], cv2.COLOR_RGB2BGR)
        mask2 = arr2[:, :, 3]
        
        # HSVに変換
        hsv1 = cv2.cvtColor(bgr1, cv2.COLOR_BGR2HSV)
        hsv2 = cv2.cvtColor(bgr2, cv2.COLOR_BGR2HSV)
        
        # 1. HSヒストグラム (色彩と彩度) - 照明に強い
        # H:18, S:8 ビンで色味をしっかり捉える
        hist_hs1 = cv2.calcHist([hsv1], [0, 1], mask1, [18, 8], [0, 180, 0, 256])
        hist_hs2 = cv2.calcHist([hsv2], [0, 1], mask2, [18, 8], [0, 180, 0, 256])
        cv2.normalize(hist_hs1, hist_hs1, 0, 1, cv2.NORM_MINMAX)
        cv2.normalize(hist_hs2, hist_hs2, 0, 1, cv2.NORM_MINMAX)
        dist_hs = cv2.compareHist(hist_hs1, hist_hs2, cv2.HISTCMP_BHATTACHARYYA)

        # 2. Vヒストグラム (明るさ) - 黒い商品の識別に必要
        # 明度は 8ビン (粗め) にして、影などの細かい変動を無視する
        hist_v1 = cv2.calcHist([hsv1], [2], mask1, [8], [0, 256])
        hist_v2 = cv2.calcHist([hsv2], [2], mask2, [8], [0, 256])
        cv2.normalize(hist_v1, hist_v1, 0, 1, cv2.NORM_MINMAX)
        cv2.normalize(hist_v2, hist_v2, 0, 1, cv2.NORM_MINMAX)
        dist_v = cv2.compareHist(hist_v1, hist_v2, cv2.HISTCMP_BHATTACHARYYA)
        
        
        # 3. 無彩色判定に基づく重み付けの決定
        achromatic1 = is_achromatic(hsv1, mask1)
        achromatic2 = is_achromatic(hsv2, mask2)
        
        if achromatic1 and achromatic2:
            # 両方が無彩色の場合はV（明るさ）を重視
            hs_weight, v_weight = ACHROMATIC_HS_WEIGHT, ACHROMATIC_V_WEIGHT
        else:
            # それ以外はHS（色味）を重視
            hs_weight, v_weight = DEFAULT_HS_WEIGHT, DEFAULT_V_WEIGHT
            
        final_dist = (dist_hs * hs_weight) + (dist_v * v_weight)
        
        # スコア化 (100点満点)
        color_score = max(0, min(100, (1.0 - final_dist) * 100))
        return color_score
    except Exception as e:
        print(f"    [!] 色計算エラー: {e}")
        return 0

def judge_similarity(ebay_img_url, scraped_items):
    """
    【究極完全版 2.0】バッチ処理 (Size 5) 対応
    1. HSV (H/S/V) ヒストグラムによるカラーゲート
    2. DINOv2 による形状判定 (バッチ推論)
    """
    try:
        ebay_rgba = load_and_remove_bg(ebay_img_url)
        if ebay_rgba is None: return []
        
        ebay_rgb = rgba_to_rgb_white_bg(ebay_rgba)
        # eBay画像も単体でエンコードして基準とする
        ebay_emb = get_dino_embeddings([ebay_rgb])[0].unsqueeze(0)
    except Exception as e:
        print(f"eBay画像のエンコードに失敗しました: {e}")
        return []

    results = []
    batch_size = 5
    print(f"    [*] {len(scraped_items)} 件の候補を精密判定中 (Color Gate: {COLOR_GATE_THRESHOLD})...")
    
    # 5件ずつのバッチで処理
    for i in range(0, len(scraped_items), batch_size):
        chunk = scraped_items[i : i + batch_size]
        valid_chunk_data = [] # (item, rgba, rgb)

        # --- STEP 1: ロード & 背景除去 (並列処理) ---
        print(f"    [*] バッチ {i//batch_size + 1}: {len(chunk)} 枚を並列処理中 (rembg)...")
        
        # 各アイテムの画像URLを抽出
        urls_to_process = []
        for item in chunk:
            target_url = item.get("img_url") or (item.get("img_urls")[0] if item.get("img_urls") else None)
            urls_to_process.append(target_url)

        # ThreadPoolExecutor で並列実行
        with ThreadPoolExecutor(max_workers=batch_size) as executor:
            # load_and_remove_bg に各URLを渡す
            rgba_results = list(executor.map(load_and_remove_bg, urls_to_process))

        # --- STEP 1.5: カラー判定 ---
        for idx, item in enumerate(chunk):
            try:
                cand_rgba = rgba_results[idx]
                if cand_rgba is None:
                    item["score"] = 0
                    results.append(item)
                    continue
                
                # カラーゲート
                color_score = get_masked_color_score(ebay_rgba, cand_rgba)
                item["color_score"] = color_score
                
                item_url = item.get("page_url") or item.get("item_url") or item.get("item_affiliate_web_url") or "No URL"
                if color_score < COLOR_GATE_THRESHOLD:
                    print(f"    [REJECT] Color Score too low ({color_score:.1f}) < {COLOR_GATE_THRESHOLD}")
                    print(f"    [-] URL: {item_url}")
                    item["score"] = 0
                    results.append(item)
                    continue
                
                cand_rgb = rgba_to_rgb_white_bg(cand_rgba)
                valid_chunk_data.append((item, cand_rgba, cand_rgb))
                
            except Exception as e:
                print(f"    [!] 判定エラー (個別処理): {e}")
                item["score"] = 0
                results.append(item)

        # --- STEP 2: DINOv2 バッチ推論 ---
        if valid_chunk_data:
            try:
                chunk_rgbs = [data[2] for data in valid_chunk_data]
                # バッチ5枚まとめて GPU へ！
                chunk_embs = get_dino_embeddings(chunk_rgbs)
                
                # コサイン類似度を一括計算
                for idx, (item, rgba, rgb) in enumerate(valid_chunk_data):
                    cand_emb = chunk_embs[idx].unsqueeze(0)
                    sim = torch.nn.functional.cosine_similarity(ebay_emb, cand_emb).item()
                    dino_score = max(0, min(100, (sim - 0.5) * 200)) 
                    
                    final_score = dino_score
                    item["score"] = final_score
                    item["dino_score"] = dino_score
                    
                    color_s = item.get("color_score", 0)
                    item_url = item.get("page_url") or item.get("item_url") or item.get("item_affiliate_web_url") or "No URL"
                    print(f"    [PASS] Color={color_s:.1f} | DINO={dino_score:.1f} => Final={final_score:.1f}%")
                    print(f"    [+] URL: {item_url}")
                    results.append(item)
                    
            except Exception as e:
                print(f"    [!] バッチ推論エラー: {e}")
                for item, _, _ in valid_chunk_data:
                    item["score"] = 0
                    results.append(item)

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results

