import gpu_utils
import torch
from lightglue import LightGlue, SuperPoint
from lightglue.utils import load_image, rbd
from lightglue.viz2d import plot_matches, plot_keypoints
from PIL import Image
import requests
from io import BytesIO
import numpy as np

# LightGlueモデルのロード (SuperPoint + LightGlue)
print("[*] LightGlueモデル (SuperPoint) をロードしています...")
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
extractor = SuperPoint(max_num_keypoints=512).eval().to(device)  # CPU用にキーポイント数を制限
matcher = LightGlue(features='superpoint').eval().to(device)
print(f"[*] LightGlueモデルロード完了 (Device: {device})")

def fetch_image_as_tensor(url):
    """
    URLから画像をダウンロードし、LightGlue形式のテンソルとして返す。
    CPUメモリ節約のため最大512pxにリサイズする。
    """
    response = requests.get(url)
    img = Image.open(BytesIO(response.content)).convert('RGB')
    
    # リサイズ (アスペクト比維持)
    img.thumbnail((512, 512))
    
    # numpy -> torch
    img_np = np.array(img).astype(np.float32) / 255.0
    img_tensor = torch.from_numpy(img_np).permute(2, 0, 1).to(device)
    return img_tensor

def verify_with_lightglue(img1_url, img2_url):
    """
    2つの画像をLightGlueで比較し、(マッチ数, スコアの平均値) を返す。
    """
    try:
        image0 = fetch_image_as_tensor(img1_url)
        image1 = fetch_image_as_tensor(img2_url)

        # 特徴点抽出
        feats0 = extractor.extract(image0)
        feats1 = extractor.extract(image1)

        # マッチング
        matches01 = matcher({'image0': feats0, 'image1': feats1})
        feats0, feats1, matches01 = [rbd(x) for x in [feats0, feats1, matches01]]
        
        matches = matches01['matches']  # indices with shape (K, 2)
        scores = matches01['scores']    # confidence scores (K,)
        
        match_count = len(matches)
        avg_score = scores.mean().item() if match_count > 0 else 0.0
        
        return match_count, avg_score
    except Exception as e:
        print(f"LightGlue判定中にエラーが発生しました: {e}")
        return 0, 0.0

def calculate_lightglue_score(avg_confidence):
    """
    LightGlueのスコア平均値 (0.0 - 1.0) を 0-100 スコアに変換する。
    """
    return max(0, min(100, avg_confidence * 100))

def calculate_refined_score(base_score, match_data):
    """
    (互換性用) match_data には (count, avg_score) のタプルが入る想定。
    """
    match_count, avg_score = match_data
    lg_score = calculate_lightglue_score(avg_score)
    return lg_score
