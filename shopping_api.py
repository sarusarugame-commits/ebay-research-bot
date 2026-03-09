import requests
import time
from urllib.parse import urlparse, parse_qs, unquote
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
                    "page_url": unquote(parse_qs(urlparse(item["itemUrl"]).query).get("pc", [item["itemUrl"]])[0]),
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
                # Yahoo API V3 は結果によって code や condition が文字列("free", "used"等)で返ることがある
                ship_code = str(item.get("shipping", {}).get("code", ""))
                # "2" または "free" が送料無料（暫定）
                is_free_shipping = (ship_code == "2" or "free" in ship_code.lower())
                total_price = price if is_free_shipping else (price + 800)
                
                cond_val = str(item.get("condition", "")).lower()
                # "2" または "used" が中古
                condition = "中古" if (cond_val == "2" or "used" in cond_val) else "新品"
                
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
                    "page_url": unquote(parse_qs(urlparse(item["url"]).query).get("pc", [item["url"]])[0]),
                    "img_urls": img_urls,
                    "shipping_included": is_free_shipping
                })
    except Exception as e:
        print(f"[search_yahoo] Error: {e}")
    return results

def scrape_yahoo_item(url, browser_page):
    """Yahooショッピングの商品ページから詳細画像（最大5枚）を抽出する"""
    try:
        if not url.startswith("http"): return None
        print(f"    [SCRAPE] Yahooショッピング詳細抽出: {url}")
        browser_page.get(url)
        
        # 画像URL取得 (最大5枚)
        # Yahooは shp.c.yimg.jp などのドメインに商品画像がある
        img_urls = []
        # 少し待機して読み込みを待つ
        time.sleep(1) 
        
        for img in browser_page.eles('tag:img', timeout=5):
            src = img.attr('src')
            if src and src.startswith("http") and src not in img_urls:
                # 商品画像っぽいドメインを優先（shp.c.yimg.jp, item-shopping.c.yimg.jp 等）
                if "yimg.jp" in src:
                    # アイコンやバナー（ロゴ、カート等）を弾く
                    if any(x in src.lower() for x in ["icon", "logo", "banner", "common", "navigation", "stype"]):
                        continue
                    img_urls.append(src)
            if len(img_urls) >= 5: break
            
        print(f"    [*] Yahoo詳細: {len(img_urls)} 枚の画像を取得しました。")
        return {"img_urls": img_urls}
    except Exception as e:
        print(f"    [SCRAPE_ERROR] Yahoo詳細抽出失敗: {e}")
        return None
