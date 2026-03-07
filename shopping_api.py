import requests
from config import RAKUTEN_APPLICATION_ID, RAKUTEN_ACCESS_KEY, RAKUTEN_AFFILIATE_ID, YAHOO_CLIENT_ID

def search_rakuten(keyword):
    """楽天で上位件数を取得し、画像付きのリストを返す。新形式(Access Key方式)に対応。"""
    if not RAKUTEN_APPLICATION_ID or not RAKUTEN_ACCESS_KEY:
        return []
    
    # 新形式のエンドポイント (openapi.rakuten.co.jp)
    url = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20220601"
    params = {
        "applicationId": RAKUTEN_APPLICATION_ID,
        "accessKey": RAKUTEN_ACCESS_KEY,
        "affiliateId": RAKUTEN_AFFILIATE_ID,
        "keyword": keyword,
        "sort": "+itemPrice",
        "hits": 5,
        "imageFlag": 1,
        "availability": 1
    }
    
    results = []
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            print(f"[search_rakuten] API Error {r.status_code}: {r.text}")
        data = r.json()
        if "Items" in data:
            for item_wrap in data["Items"]:
                item = item_wrap["Item"]
                price = item["itemPrice"]
                ship_flag = item.get("postageFlag", 0)
                total_price = price if ship_flag == 1 else (price + 800)
                
                is_used = item.get("usedFlag", 0) == 1
                item_name = item.get("itemName", "")
                # タイトルに中古を示唆する言葉があれば中古とする (usedFlagが0でもタイトル優先)
                used_keywords = ["中古", "USED", "ランク", "展示品", "訳あり"]
                if not is_used:
                    if any(k in item_name.upper() for k in used_keywords):
                        # 「新品」という言葉が含まれていないか、あるいは「新品同様」のような文脈かを確認
                        if "新品" not in item_name or "新品同様" in item_name or "中古" in item_name:
                            is_used = True
                
                condition = "中古" if is_used else "新品"
                
                img_urls = []
                if item.get("mediumImageUrls"):
                    for img_obj in item["mediumImageUrls"]:
                        img_urls.append(img_obj["imageUrl"])
                        if len(img_urls) >= 5: break
                
                results.append({
                    "platform": "楽天市場",
                    "title": item["itemName"],
                    "price": str(price),  # main.py用に文字列のpriceを追加
                    "price_int": price,
                    "total_price": total_price,
                    "condition": condition,
                    "page_url": item["itemUrl"],
                    "img_urls": img_urls,
                    "shipping_included": True if ship_flag == 1 else False
                })
    except Exception as e:
        print(f"[search_rakuten] Error: {e}")
    return results

def search_yahoo(keyword):
    """Yahooショッピングで上位件数を取得し、画像付きのリストを返す。"""
    if not YAHOO_CLIENT_ID:
        return []
    
    url = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"
    params = {
        "appid": YAHOO_CLIENT_ID,
        "query": keyword,
        "sort": "+price",
        "results": 5,
        "in_stock": "true"
    }
    
    results = []
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        if "hits" in data:
            for item in data["hits"]:
                price = int(item["price"])
                ship_fee = int(item.get("shipping", {}).get("code", 1))
                total_price = price if ship_fee == 2 else (price + 800)
                
                cond_code = int(item.get("condition", 1))
                condition = "中古" if cond_code == 2 else "新品"
                
                # Yahoo API V3 は検索結果ではメイン画像1枚のみ
                img_urls = []
                if item.get("image", {}).get("medium"):
                    img_urls.append(item["image"]["medium"])
                
                results.append({
                    "platform": "Yahooショッピング",
                    "title": item["name"],
                    "price": str(price),  # main.py用に文字列のpriceを追加
                    "price_int": price,
                    "total_price": total_price,
                    "condition": condition,
                    "page_url": item["url"],
                    "img_urls": img_urls,
                    "shipping_included": True if ship_fee == 2 else False
                })
    except Exception as e:
        print(f"[search_yahoo] Error: {e}")
    return results
