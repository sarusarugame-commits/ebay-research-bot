import sys
import os

# Add current directory to path
sys.path.append(os.getcwd())

from llm_vision_judge import analyze_item_safety_and_tariff

def test_vision_safety():
    print("--- Testing Vision Safety & Tariff Check ---")
    
    test_cases = [
        {
            "name": "Alcohol (Whisky)",
            "url": "https://m.media-amazon.com/images/I/71-sN5k-m-L._AC_SL1500_.jpg", # Hibiki
            "expected_alcohol": True
        },
        {
            "name": "Leather Bag",
            "url": "https://m.media-amazon.com/images/I/81xU+cOqWDL._AC_SL1500_.jpg", # Leather tote
            "expected_high_tariff": True
        },
        {
            "name": "Steel Watch",
            "url": "https://m.media-amazon.com/images/I/71u9S+F-T+L._AC_SL1500_.jpg", # Steel watch
            "expected_high_tariff": True
        },
        {
            "name": "Plastic Toy",
            "url": "https://m.media-amazon.com/images/I/71R2H0L-R0L._AC_SL1500_.jpg", # Lego
            "expected_alcohol": False,
            "expected_high_tariff": False
        }
    ]
    
    for case in test_cases:
        print(f"\n[*] Testing: {case['name']}")
        result = analyze_item_safety_and_tariff(case['url'])
        print(f"    Result: {result}")
        
        if "expected_alcohol" in case:
            if result['is_alcohol'] == case['expected_alcohol']:
                print("    [PASS] Alcohol check match")
            else:
                print(f"    [FAIL] Alcohol check mismatch (Expected {case['expected_alcohol']})")
                
        if "expected_high_tariff" in case:
            if result['is_high_tariff'] == case['expected_high_tariff']:
                print("    [PASS] Tariff check match")
            else:
                print(f"    [FAIL] Tariff check mismatch (Expected {case['expected_high_tariff']})")

if __name__ == "__main__":
    test_vision_safety()
