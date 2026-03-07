import requests
import json
from config import EBAY_APP_ID

def test_ebay_finding_api(keywords):
    """
    eBay Finding API の findItemsByKeywords を使用したテスト検索
    """
    if not EBAY_APP_ID:
        print("[!] EBAY_APP_ID が設定されていません。")
        return

    # エンドポイント
    url = "https://svcs.ebay.com/services/search/FindingService/v1"
    
    # パラメータ
    params = {
        "OPERATION-NAME": "findItemsByKeywords",
        "SERVICE-VERSION": "1.0.0",
        "SECURITY-APPNAME": EBAY_APP_ID,
        "RESPONSE-DATA-FORMAT": "JSON",
        "REST-PAYLOAD": "",
        "keywords": keywords,
        "paginationInput.entriesPerPage": 5,
        "sortOrder": "BestMatch",
        "itemFilter(0).name": "Condition",
        "itemFilter(0).value": "New",
        "itemFilter(1).name": "ListingType",
        "itemFilter(1).value": "FixedPrice"
    }

    print(f"[*] eBay Finding API 実行中: {keywords}")
    
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            # レスポンス構造の確認とパース
            search_response = data.get("findItemsByKeywordsResponse", [{}])[0]
            ack = search_response.get("ack", ["Failure"])[0]
            
            if ack == "Success":
                search_result = search_response.get("searchResult", [{}])[0]
                items = search_result.get("item", [])
                
                print(f" -> {len(items)} 件の商品が見つかりました。\n")
                
                for i, item in enumerate(items, 1):
                    title = item.get("title", ["Unknown"])[0]
                    price_data = item.get("sellingStatus", [{}])[0].get("currentPrice", [{}])[0]
                    price = price_data.get("__value__", "0")
                    currency = price_data.get("@currencyId", "USD")
                    view_url = item.get("viewItemURL", ["#"])[0]
                    
                    print(f"{i}. {title}")
                    print(f"   価格: {price} {currency}")
                    print(f"   URL:  {view_url}\n")
            else:
                error_msg = search_response.get("errorMessage", [{}])[0]
                print(f"[!] APIエラー: {error_msg}")
        else:
            print(f"[!] HTTPエラー: {response.status_code}")
            print(response.text)
            
    except Exception as e:
        print(f"[!] 例外が発生しました: {e}")

if __name__ == "__main__":
    # テスト用クエリ
    test_query = "CASIO Oceanus OCW-S6000PBS-7AJR White"
    test_ebay_finding_api(test_query)
