from mercari_scraper import search_mercari, create_browser, close_browser
import time

def verify_turbo():
    browser = create_browser()
    try:
        start_search = time.time()
        results = search_mercari('casio oceanus', browser, max_results=10)
        end_search = time.time()
        
        print(f"\n[*] Search & Parse Time: {end_search - start_search:.2f}s")
        print(f"[*] Results Found: {len(results)}")
        
        for i, r in enumerate(results[:5]):
            print(f"[{i}] Raw Label: {r.get('raw_label', 'N/A')}")
            print(f"    Title: {r['title'][:40]}...")
            print(f"    Price: {r['price']} | URL: {r['page_url'][:50]}...")
            if r['price'] == "0" or r['title'] == "不明":
                print(f"    [!] Error: Parsing failed for this item.")

    finally:
        close_browser(browser)

if __name__ == "__main__":
    verify_turbo()
