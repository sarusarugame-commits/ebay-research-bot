import sys
import os

# Add current directory to path
sys.path.append(os.getcwd())

from lightglue_judge import verify_with_lightglue, calculate_lightglue_score

def test_lightglue_granular():
    print("--- Testing LightGlue Granular Scoring ---")
    
    # Use the same images from previous tests for consistency
    img1 = "https://m.media-amazon.com/images/I/71u9S+F-T+L._AC_SL1500_.jpg" # Steel watch
    img2 = "https://m.media-amazon.com/images/I/71u9S+F-T+L._AC_SL1500_.jpg" # Same watch
    
    print(f"[*] Comparing identical images...")
    count, avg_conf = verify_with_lightglue(img1, img2)
    score = calculate_lightglue_score(avg_conf)
    print(f"    Matches: {count}")
    print(f"    Avg Confidence: {avg_conf:.4f}")
    print(f"    Final Score: {score:.1f}%")

    img3 = "https://m.media-amazon.com/images/I/71-sN5k-m-L._AC_SL1500_.jpg" # Hibiki (Whisky)
    print(f"\n[*] Comparing different images...")
    count_diff, avg_conf_diff = verify_with_lightglue(img1, img3)
    score_diff = calculate_lightglue_score(avg_conf_diff)
    print(f"    Matches: {count_diff}")
    print(f"    Avg Confidence: {avg_conf_diff:.4f}")
    print(f"    Final Score: {score_diff:.1f}%")

if __name__ == "__main__":
    test_lightglue_granular()
