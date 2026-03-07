import sys
import os
import torch

# Add current directory to path
sys.path.append(os.getcwd())

from lightglue_judge import verify_with_lightglue, calculate_lightglue_score, device

def test_lightglue_debug():
    print("--- Debugging LightGlue Granular Scoring ---")
    print(f"[*] Torch CUDA available: {torch.cuda.is_available()}")
    print(f"[*] Current device: {device}")
    
    img1 = "https://m.media-amazon.com/images/I/71u9S+F-T+L._AC_SL1500_.jpg" # Steel watch
    img2 = "https://m.media-amazon.com/images/I/71u9S+F-T+L._AC_SL1500_.jpg" # Same watch
    
    print(f"\n[*] Comparing identical images...")
    try:
        count, avg_conf = verify_with_lightglue(img1, img2)
        score = calculate_lightglue_score(avg_conf)
        print(f"    Matches: {count}")
        print(f"    Avg Confidence: {avg_conf:.4f}")
        print(f"    Final Score: {score:.1f}%")
    except Exception as e:
        print(f"    [!] Error during comparison: {e}")

if __name__ == "__main__":
    test_lightglue_debug()
