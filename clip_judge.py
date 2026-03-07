import os
import torch
import torch.nn.functional as F
from PIL import Image
import requests
from io import BytesIO
import logging

logger = logging.getLogger(__name__)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# モデルのロード
try:
    logger.info(f"Using device: {device}")
    # cuBLAS のハンドルを確実に初期化するためのダミー演算
    if device.type == 'cuda':
        torch.zeros(1).to(device) * torch.zeros(1).to(device)
except Exception as e:
    logger.error(f"CUDA initialization failed: {e}")
    device = torch.device("cpu")

# CLIP/DINOv2判定ロジック
def judge_similarity(ref_img_url, candidates):
    # (実装詳細は既存の通り)
    return candidates
