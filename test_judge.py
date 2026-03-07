import sys
import os

# Add current directory to path
sys.path.append(os.getcwd())

from clip_judge import judge_similarity

def test_hybrid_judge():
    print("--- Testing Ultimate Hybrid Judge ---")
    
    # Use a real eBay image URL that is likely to be stable
    ebay_img = "https://i.ebayimg.com/images/g/VlAAAOSwR3ll8v4m/s-l500.jpg"
    candidates = [
        {
            "title": "Same product (Similar)",
            "img_url": "https://i.ebayimg.com/images/g/VlAAAOSwR3ll8v4m/s-l500.jpg",
            "platform": "Test"
        },
        {
            "title": "Different product (Different Shape/Color)",
            "img_url": "https://i.ebayimg.com/images/g/vXgAAOSwR3ll8v4m/s-l500.jpg",
            "platform": "Test"
        }
    ]
    
    print(f"Base image: {ebay_img}")
    results = judge_similarity(ebay_img, candidates)
    
    for res in results:
        print(f"Title: {res['title']} | Score: {res['score']:.1f}%")

if __name__ == "__main__":
    test_hybrid_judge()
