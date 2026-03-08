import sys
import os

# Add current directory to path
sys.path.append(r"g:\マイドライブ\Python_code\eBayリサーチ部隊")

# Mock dependencies
class MockResult:
    def __init__(self, items):
        self.items = items
    def get(self, key, default=None):
        return self.items.get(key, default)
    def __getitem__(self, key):
        return self.items[key]

def mock_judge_similarity(img_url, candidates):
    # Simulate returning the first item with a high score
    if candidates:
        res = candidates[0].copy()
        res["score"] = 90
        return [res]
    return []

# Test logic from main.py
def test_fix():
    amz_results = [
        {"title": "Test Item", "page_url": "https://amazon.co.jp/test", "img_url": "https://amazon.co.jp/img"}
    ]
    img_url = "https://ebay.com/ref_img"
    
    print(f"Original results: {amz_results}")
    
    # Logic from fixed main.py
    amz_for_judge = [{"img_url": r.get("img_url", ""), "page_url": r.get("page_url", ""), "_orig": r} 
                     for r in amz_results if r.get("img_url")]
    
    print(f"Prepared for judge: {amz_for_judge}")
    
    amz_judged = mock_judge_similarity(img_url, amz_for_judge)
    
    if amz_judged and amz_judged[0] is not None and amz_judged[0].get("score", 0) >= 70:
        best_amz = amz_judged[0]
        amz_url = best_amz.get("page_url") or best_amz.get("_orig", {}).get("page_url")
        
        print(f"Extracted amz_url: {amz_url}")
        assert amz_url == "https://amazon.co.jp/test"
        print("Verification SUCCESS: amz_url correctly extracted.")
    else:
        print("Verification FAILED: No match found.")

if __name__ == "__main__":
    test_fix()
