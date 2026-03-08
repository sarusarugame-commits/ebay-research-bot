import sys
import os
# カレントディレクトリをパスに追加
sys.path.append(os.getcwd())

from ebay_api import get_item_details, fetch_items

def test_details():
    print("[*] テスト用アイテム取得中...")
    try:
        # Adorama 等の有名セラーからアイテムを取得
        items = fetch_items("adoramacamera")
        if not items:
            print("[!] アイテムが見つかりませんでした。別のセラーを試します...")
            items = fetch_items("world_wide_stereo")
        
        if not items:
            print("[!] テスト用アイテムの取得に失敗しました。")
            return
        
        target_item = items[0]
        aid = target_item['id']
        print(f"=== Testing Item ID: {aid} ({target_item['title']}) ===")
        
        # US Context
        print("\n--- US Context (NY 10001) ---")
        res_us = get_item_details(aid, marketplace_id='EBAY_US', country='US', zip_code='10001')
        if res_us:
            print(f"Price: {res_us['price']} {res_us['currency']}")
            print(f"Shipping Cost: ${res_us['shipping_cost']}")
            print(f"Shippable: {res_us['is_shippable']}")
        
        # UK Context
        print("\n--- UK Context (London E1 6AN) ---")
        res_uk = get_item_details(aid, marketplace_id='EBAY_GB', country='GB', zip_code='E1 6AN')
        if res_uk:
            print(f"Price: {res_uk['price']} {res_uk['currency']}")
            print(f"Shipping Cost: £{res_uk['shipping_cost']}")
            print(f"Shippable: {res_uk['is_shippable']}")
            
    except Exception as e:
        print(f"[ERROR] テスト実行中にエラー: {e}")

if __name__ == "__main__":
    test_details()
