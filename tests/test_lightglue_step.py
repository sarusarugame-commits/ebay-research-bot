import sys
import os
import torch
import requests
from PIL import Image
from io import BytesIO

# Add current directory to path
sys.path.append(os.getcwd())

from lightglue_judge import verify_with_lightglue, calculate_lightglue_score, device, extractor

def debug_step_by_step():
    print("--- LightGlue Step-by-Step Debug (Final) ---")
    
    # Stable eBay images (using the one from previous test_judge.py)
    img_url = "https://i.ebayimg.com/images/g/Y8IAAOSwPzxh7X9I/s-l1600.jpg"
    
    # 1. Test Download
    print("[*] Testing Image Download...")
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(img_url, headers=headers, timeout=10)
        print(f"    Status: {response.status_code}")
        img = Image.open(BytesIO(response.content))
        print(f"    Image Size: {img.size}")
    except Exception as e:
        print(f"    [!] Download Failed: {e}")
        return

    # 2. Test Extractor
    print("\n[*] Testing Extractor...")
    try:
        img_rgb = img.convert('RGB')
        img_rgb.thumbnail((512, 512))
        import numpy as np
        img_np = np.array(img_rgb).astype(np.float32) / 255.0
        img_tensor = torch.from_numpy(img_np).permute(2, 0, 1).to(device)
        feats = extractor.extract(img_tensor)
        print(f"    Detected Keypoints: {len(feats['keypoints'][0])}")
    except Exception as e:
        print(f"    [!] Extraction Failed: {e}")
        return

    # 3. Test Full Verification
    print("\n[*] Testing Full Verification (Identical)...")
    try:
        count, avg_conf = verify_with_lightglue(img_url, img_url)
        score = calculate_lightglue_score(avg_conf)
        print(f"    Matches: {count}")
        print(f"    Avg Confidence: {avg_conf:.4f}")
        print(f"    Calculated Score: {score:.1f}%")
    except Exception as e:
        print(f"    [!] Verification Failed: {e}")

if __name__ == "__main__":
    debug_step_by_step()
