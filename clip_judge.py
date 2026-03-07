import gpu_utils
import torch
import torchvision.transforms as T
from PIL import Image
import requests
from io import BytesIO
import cv2
import numpy as np
from rembg import remove, new_session

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
        return img_rgba
    except Exception as e:
        print(f"    [!] 画像ロード/背景除去エラー ({url[:50]}...): {e}")
        return None

def rgba_to_rgb_white_bg(rgba_img):
    """透過画像を白背景のRGB画像に変換（DINOv2用）"""
    background = Image.new("RGB", rgba_img.size, (255, 255, 255))
    background.paste(rgba_img, mask=rgba_img.split()[3]) 
    return background

def get_dino_embedding(image_rgb):
    """画像をDINOv2でエンコードしてベクトルを返す"""
    global device, dino_model
    try:
        img_tensor = transform(image_rgb).unsqueeze(0).to(device)
        with torch.no_grad():
            embedding = dino_model(img_tensor)
        return embedding
    except Exception as e:
        if "CUBLAS_STATUS_NOT_INITIALIZED" in str(e) and device.type == 'cuda':
            print(f"    [!] cuBLASエラーを検知しました。CPUで再試行します...")
            device = torch.device("cpu")
            dino_model = dino_model.to(device)
            img_tensor = transform(image_rgb).unsqueeze(0).to(device)
            with torch.no_grad():
                embedding = dino_model(img_tensor)
            return embedding
        raise e

def get_masked_color_score(rgba1, rgba2):
    """背景を完全に無視して、被写体の「色」だけで類似度を計算する！"""
    try:
        arr1 = np.array(rgba1)
        arr2 = np.array(rgba2)
        
        bgr1 = cv2.cvtColor(arr1[:, :, :3], cv2.COLOR_RGB2BGR)
        mask1 = arr1[:, :, 3]
        
        bgr2 = cv2.cvtColor(arr2[:, :, :3], cv2.COLOR_RGB2BGR)
        mask2 = arr2[:, :, 3]
        
        hsv1 = cv2.cvtColor(bgr1, cv2.COLOR_BGR2HSV)
        hsv2 = cv2.cvtColor(bgr2, cv2.COLOR_BGR2HSV)
        
        hist1 = cv2.calcHist([hsv1], [0, 1], mask1, [50, 60], [0, 180, 0, 256])
        hist2 = cv2.calcHist([hsv2], [0, 1], mask2, [50, 60], [0, 180, 0, 256])
        
        cv2.normalize(hist1, hist1, 0, 1, cv2.NORM_MINMAX)
        cv2.normalize(hist2, hist2, 0, 1, cv2.NORM_MINMAX)
        
        distance = cv2.compareHist(hist1, hist2, cv2.HISTCMP_BHATTACHARYYA)
        color_score = max(0, min(100, (1.0 - distance) * 100))
        return color_score
    except Exception as e:
        print(f"    [!] 色計算エラー: {e}")
        return 0

def judge_similarity(ebay_img_url, scraped_items):
    """
    【究極完全版】
    1. OpenCV色スコアで足切り (Color < 50 は即不採用)
    2. DINOv2 (形状) で全件精密判定 (LightGlueは一時停止中)
    """
    try:
        ebay_rgba = load_and_remove_bg(ebay_img_url)
        if ebay_rgba is None: return []
        
        ebay_rgb = rgba_to_rgb_white_bg(ebay_rgba)
        ebay_emb = get_dino_embedding(ebay_rgb)
    except Exception as e:
        print(f"eBay画像のエンコードに失敗しました: {e}")
        return []

    results = []
    print(f"    [*] {len(scraped_items)} 件の候補を精密判定中 (Color Gate: 50)...")
    
    for i, item in enumerate(scraped_items):
        try:
            target_url = item.get("img_url") or (item.get("img_urls")[0] if item.get("img_urls") else None)
            if not target_url:
                item["score"] = 0
                results.append(item)
                continue

            cand_rgba = load_and_remove_bg(target_url)
            if cand_rgba is None:
                item["score"] = 0
                results.append(item)
                continue
            
            # --- PHASE 1: COLOR GATE (OpenCV) ---
            color_score = get_masked_color_score(ebay_rgba, cand_rgba)
            if color_score < 50:
                print(f"    [REJECT] Candidate {i}: Color Score too low ({color_score:.1f})")
                item["score"] = 0
                item["color_score"] = color_score
                results.append(item)
                continue

            # --- PHASE 2: SHAPE (DINOv2) ---
            # 1. DINOv2
            cand_rgb = rgba_to_rgb_white_bg(cand_rgba)
            cand_emb = get_dino_embedding(cand_rgb)
            sim = torch.nn.functional.cosine_similarity(ebay_emb, cand_emb).item()
            dino_score = max(0, min(100, (sim - 0.5) * 200)) 
            
            # 2. LightGlue (精密判定) - 一旦コメントアウト
            # lg_count, lg_avg_conf = verify_with_lightglue(ebay_img_url, target_url)
            # lg_score = calculate_lightglue_score(lg_avg_conf)
            
            # 最終スコアブレンド: DINO 100%
            final_score = dino_score
            
            print(f"    [PASS] Candidate {i}: Color={color_score:.1f} | DINO={dino_score:.1f} => Final={final_score:.1f}%")
            
            item["score"] = final_score
            item["color_score"] = color_score
            item["dino_score"] = dino_score
            # item["lg_score"] = lg_score
            results.append(item)
            
        except Exception as e:
            print(f"    [!] 判定エラー (Candidate {i}): {e}")
            item["score"] = 0
            results.append(item)
            
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results

