import sys
import os

# Add current directory to path
sys.path.append(os.getcwd())

from shopping_api import search_rakuten, search_yahoo
from mercari_scraper import search_mercari, create_browser, close_browser

def test_shopping_api():
    print("--- Testing Shopping API ---")
    keyword = "nintendo switch"
    
    print("\n[Rakuten]")
    rakuten_results = search_rakuten(keyword)
    if rakuten_results:
        item = rakuten_results[0]
        print(f"Title: {item.get('title')}")
        print(f"Price (str): {item.get('price')} (Type: {type(item.get('price'))})")
        print(f"Price (int): {item.get('price_int')} (Type: {type(item.get('price_int'))})")
        assert "price" in item and isinstance(item["price"], str)
    else:
        print("No Rakuten results found (check API keys)")

    print("\n[Yahoo]")
    yahoo_results = search_yahoo(keyword)
    if yahoo_results:
        item = yahoo_results[0]
        print(f"Title: {item.get('title')}")
        print(f"Price (str): {item.get('price')} (Type: {type(item.get('price'))})")
        print(f"Price (int): {item.get('price_int')} (Type: {type(item.get('price_int'))})")
        assert "price" in item and isinstance(item["price"], str)
    else:
        print("No Yahoo results found (check API keys)")

def test_mercari_shops():
    print("\n--- Testing Mercari Shops / Stickers ---")
    browser = create_browser()
    try:
        # Search for something that often has Shops items
        keyword = "天白 時計" # User mentioned this
        results = search_mercari(keyword, browser, max_results=20)
        
        found_shops = False
        for item in results:
            print(f"Title: {item['title']} | URL: {item['page_url']}")
            if "/shops/product/" in item['page_url']:
                found_shops = True
        
        if found_shops:
            print("\nSuccessfully found Mercari Shops item!")
        else:
            print("\nNo Mercari Shops item found in results (check current listings)")
            
    finally:
        close_browser(browser)

if __name__ == "__main__":
    test_shopping_api()
    test_mercari_shops()
