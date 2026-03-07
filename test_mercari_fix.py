import os
import sys

# プロジェクトルートをパスに追加
sys.path.append(os.getcwd())

import gpu_utils
from mercari_scraper import search_mercari, create_browser
from clip_judge import judge_similarity

def test_mercari_shops_inclusion():
    print("[*] メルカリShops除外回避テスト開始...")
    browser = create_browser()
    # ユーザーが指定した商品が含まれる商品名で検索
    search_query = "OCW-S6000BVS-1AJR"
    
    try:
        results = search_mercari(search_query, browser, max_results=10)
        
        target_url = "https://jp.mercari.com/shops/product/dMnchBroPkahsQp7fqBxca"
        found = False
        for item in results:
            print(f"  - 見つかった商品: {item['title']} ({item['page_url']})")
            if target_url in item['page_url']:
                found = True
                break
        
        if found:
            print("[SUCCESS] 該当のメルカリShops商品が正常に検出されました！")
        else:
            print("[FAILURE] 該当の商品が見つかりませんでした。ロジックを再確認してください。")
            
        # ついでに判定ロジックも1件だけ回してみる
        if results:
            ebay_img = "https://i.ebayimg.com/images/g/V4IAAOSwV-5mYIAn/s-l1600.jpg" # 適当なオシアナスの画像URL
            # 最初の一報だけ画像URLをセット（search_mercariはimg_urlsを返さないので少し手動補完）
            # search_mercari は title, page_url, price を返す。
            # 実際は scrape_item_data で詳細を取るので、ここでは簡易的に check
            print("\n[*] 判定ロジックの簡易チェック開始...")
            # 本番同様 detail を取る必要があるので、簡略化
            print("  (本番の main.py ではこの後詳細をスクレイピングして画像を取得します)")

    finally:
        browser.quit()

if __name__ == "__main__":
    test_mercari_shops_inclusion()
