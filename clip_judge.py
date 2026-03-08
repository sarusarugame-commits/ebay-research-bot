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

# 🏎️💨 GPU優先モード: 推奨モデル (isnet-general-use) を使用
try:
    print("[*] 背景除去モデル (isnet-general-use) をロードしています...")
    bg_session = new_session("isnet-general-use", providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
    print("[*] 背景除去モデル (isnet-general-use) ロード完了。")
except Exception as e:
    print(f"[Warn] bg_session init error: {e}")
    bg_session = None

# DINOv2用の前処理
dino_tensor_transform = T.Compose([
    T.ToTensor(),
    T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])

def letterbox_transform(img, target_size=224):
    """アスペクト比を維持してリサイズし、白背景でパディングする"""
    w, h = img.size
    scale = target_size / max(w, h)
    new_w, new_h = int(w * scale), int(h * scale)
    
    img_resized = img.resize((new_w, new_h), Image.LANCZOS)
    
    new_img = Image.new("RGB", (target_size, target_size), (255, 255, 255))
    offset_x = (target_size - new_w) // 2
    offset_y = (target_size - new_h) // 2
    new_img.paste(img_resized, (offset_x, offset_y))
    return new_img

# ============================================================
# 【設定】Color Gate 閾値
# ============================================================
COLOR_GATE_THRESHOLD = 50

def load_and_remove_bg(url):
    """画像を読み込み、背景を透過する（リサイズなし、isnet-general-useモデル使用）"""
    if not url:
        return None
        
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    try:
        response = requests.get(url, headers=headers, stream=True, timeout=10)
        response.raise_for_status()
        img = Image.open(BytesIO(response.content)).convert("RGB")
        
        # 司令官の指示によりリサイズ処理は削除
        # img.thumbnail((max_size, max_size)) 

        if bg_session:
            img_rgba = remove(img, session=bg_session)
        else:
            img_rgba = remove(img)

        if img_rgba.mode == "RGBA":
            return img_rgba
        
        print("    [Warn] rembg did not return RGBA. Using fallback.")
        return make_fallback_rgba(img)
    except Exception as e:
        url_snippet = str(url)[:50] if url else "None"
        print(f"    [!] 画像ロード/背景除去エラー ({url_snippet}...): {traceback.format_exc() if 'traceback' in globals() else e}")
        # 失敗時のみフォールバック
        if 'img' in locals():
            return make_fallback_rgba(img)
        return None

def make_fallback_rgba(img):
    """背景除去失敗時のフォールバック（HSV輝度による簡易抽出）"""
    arr = np.array(img)
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    # 白・明るい背景を除外
    bg_mask = (hsv[:, :, 2] > 240) & (hsv[:, :, 1] < 30)
    alpha = np.where(bg_mask, 0, 255).astype(np.uint8)
    rgba = np.dstack([arr, alpha])
    return Image.fromarray(rgba, "RGBA")

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
        # 歪みを防ぐため、Letterbox（白背景パディング）化してからTensor変換
        tensors = [dino_tensor_transform(letterbox_transform(img)) for img in images_rgb]
        batch_tensor = torch.stack(tensors).to(device)
        with torch.no_grad():
            embeddings = dino_model(batch_tensor)
        return embeddings
    except Exception as e:
        if "CUBLAS_STATUS_NOT_INITIALIZED" in str(e) and device.type == 'cuda':
            print(f"    [!] cuBLASエラーを検知しました。CPUで再試行します...")
            device = torch.device("cpu")
            dino_model = dino_model.to(device)
            tensors = [dino_tensor_transform(letterbox_transform(img)) for img in images_rgb]
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
    """無彩色判定"""
    s_channel = hsv_arr[:, :, 1]
    masked_s = s_channel[mask > 0]
    if len(masked_s) == 0: return True
    return np.mean(masked_s) < threshold

def get_masked_color_score(rgba1, rgba2):
    """背景除去画像（RGBA）を用いた精密なカラー判定"""
    try:
        # 中央重点で比較
        color_crop = T.CenterCrop(224)
        rgba1_c = color_crop(rgba1)
        rgba2_c = color_crop(rgba2)
        
        arr1, arr2 = np.array(rgba1_c), np.array(rgba2_c)
        mask1, mask2 = arr1[:, :, 3], arr2[:, :, 3]
        
        hsv1 = cv2.cvtColor(arr1[:, :, :3], cv2.COLOR_RGB2HSV)
        hsv2 = cv2.cvtColor(arr2[:, :, :3], cv2.COLOR_RGB2HSV)
        
        hist_hs1 = cv2.calcHist([hsv1], [0, 1], mask1, [18, 8], [0, 180, 0, 256])
        hist_hs2 = cv2.calcHist([hsv2], [0, 1], mask2, [18, 8], [0, 180, 0, 256])
        cv2.normalize(hist_hs1, hist_hs1, 0, 1, cv2.NORM_MINMAX)
        cv2.normalize(hist_hs2, hist_hs2, 0, 1, cv2.NORM_MINMAX)
        dist_hs = cv2.compareHist(hist_hs1, hist_hs2, cv2.HISTCMP_BHATTACHARYYA)

        hist_v1 = cv2.calcHist([hsv1], [2], mask1, [8], [0, 256])
        hist_v2 = cv2.calcHist([hsv2], [2], mask2, [8], [0, 256])
        cv2.normalize(hist_v1, hist_v1, 0, 1, cv2.NORM_MINMAX)
        cv2.normalize(hist_v2, hist_v2, 0, 1, cv2.NORM_MINMAX)
        dist_v = cv2.compareHist(hist_v1, hist_v2, cv2.HISTCMP_BHATTACHARYYA)
        
        if is_achromatic(hsv1, mask1) and is_achromatic(hsv2, mask2):
            hs_weight, v_weight = ACHROMATIC_HS_WEIGHT, ACHROMATIC_V_WEIGHT
        else:
            hs_weight, v_weight = DEFAULT_HS_WEIGHT, DEFAULT_V_WEIGHT
            
        final_dist = (dist_hs * hs_weight) + (dist_v * v_weight)
        return max(0, min(100, (1.0 - final_dist) * 100))
    except Exception as e:
        print(f"    [!] 色計算エラー: {e}")
        return 0

def judge_similarity(ebay_img_url, scraped_items):
    """高品質背景抽出 + 高画質比較判定"""
    try:
        print(f"    [*] eBay画像の背景除去中 (isnet-general-use)...")
        ebay_rgba = load_and_remove_bg(ebay_img_url)
        if ebay_rgba is None: return []
        
        ebay_rgb = rgba_to_rgb_white_bg(ebay_rgba)
        ebay_emb = get_dino_embeddings([ebay_rgb])[0].unsqueeze(0)
    except Exception as e:
        print(f"eBay画像の処理に失敗しました: {e}")
        return []

    results = []
    batch_size = 5
    print(f"    [*] {len(scraped_items)} 件の候補を精密判定中...")
    
    for i in range(0, len(scraped_items), batch_size):
        chunk = scraped_items[i : i + batch_size]
        valid_chunk_data = [] # (item, cand_rgba, cand_rgb)

        print(f"    [*] バッチ {i//batch_size + 1}: {len(chunk)} 枚を処理中...")
        
        urls_to_process = []
        for item in chunk:
            target_url = item.get("img_url") or (item.get("img_urls")[0] if item.get("img_urls") else None)
            urls_to_process.append(target_url)

        with ThreadPoolExecutor(max_workers=batch_size) as executor:
            rgba_results = list(executor.map(load_and_remove_bg, urls_to_process))

        for idx, item in enumerate(chunk):
            try:
                cand_rgba = rgba_results[idx]
                if cand_rgba is None:
                    item["score"] = 0
                    results.append(item)
                    continue
                
                # カラー判定
                color_score = get_masked_color_score(ebay_rgba, cand_rgba)
                item["color_score"] = color_score
                
                item_url = item.get("page_url") or item.get("item_url") or item.get("item_affiliate_web_url") or "No URL"
                if color_score < COLOR_GATE_THRESHOLD:
                    print(f"    [REJECT] Color Score too low ({color_score:.1f}) | URL: {item_url}")
                    item["score"] = 0
                    results.append(item)
                    continue
                
                cand_rgb = rgba_to_rgb_white_bg(cand_rgba)
                valid_chunk_data.append((item, cand_rgba, cand_rgb))
                
            except Exception as e:
                print(f"    [!] 判定エラー: {e}")
                item["score"] = 0
                results.append(item)

        # DINOv2 推論
        if valid_chunk_data:
            try:
                chunk_rgbs = [data[2] for data in valid_chunk_data]
                chunk_embs = get_dino_embeddings(chunk_rgbs)
                
                for idx, (item, rgba, rgb) in enumerate(valid_chunk_data):
                    cand_emb = chunk_embs[idx].unsqueeze(0)
                    sim = torch.nn.functional.cosine_similarity(ebay_emb, cand_emb).item()
                    # 修正案（少しマイルドにする： sim が 0.4 以上の部分を 0〜100 に割り当てる）
                    dino_score = max(0, min(100, (sim - 0.4) * 166.6))
                    
                    item["score"] = dino_score
                    item["dino_score"] = dino_score
                    
                    color_s = item.get("color_score", 0)
                    item_url = item.get("page_url") or item.get("item_url") or item.get("item_affiliate_web_url") or "No URL"
                    print(f"    [PASS] Color={color_s:.1f} | DINO={dino_score:.1f} => Final={dino_score:.1f}%")
                    print(f"    [+] URL: {item_url}")
                    results.append(item)
                    
            except Exception as e:
                print(f"    [!] バッチ推論エラー: {e}")
                for item, _, _ in valid_chunk_data:
                    item["score"] = 0
                    results.append(item)

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results
