import requests
import base64
import time
import json
from config import EBAY_APP_ID, EBAY_CERT_ID, EBAY_DEV_ID, EBAY_RU_NAME

# キャッシュ用変数をグローバルに保持
_EBAY_TOKEN = None
_EBAY_TOKEN_EXPIRY = 0

def get_ebay_token():
    """
    eBay APIトークン（Client Credentials Grant）を取得またはキャッシュから返す。
    """
    global _EBAY_TOKEN, _EBAY_TOKEN_EXPIRY
    
    now = time.time()
    if _EBAY_TOKEN and now < _EBAY_TOKEN_EXPIRY:
        return _EBAY_TOKEN

    print("[*] eBay APIアクセストークンを新規取得中...")
    auth_str = f"{EBAY_APP_ID}:{EBAY_CERT_ID}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    
    url = "https://api.ebay.com/identity/v1/oauth2/token"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {b64_auth}"
    }
    payload = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope"
    }
    
    try:
        resp = requests.post(url, headers=headers, data=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        _EBAY_TOKEN = data["access_token"]
        # 有効期限の5分前に更新するように設定
        _EBAY_TOKEN_EXPIRY = now + data["expires_in"] - 300
        return _EBAY_TOKEN
    except Exception as e:
        print(f"[!] eBayトークン取得エラー: {e}")
        return None

def search_ebay(query, limit=10, marketplace_id='EBAY_US'):
    """
    eBay Browse API (search) を使用して商品を検索する。
    """
    token = get_ebay_token()
    if not token:
        return []

    url = f"https://api.ebay.com/buy/browse/v1/item_summary/search"
    params = {
        "q": query,
        "limit": limit
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": marketplace_id
    }
    
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        items = []
        for item in data.get("itemSummaries", []):
            items.append({
                "item_id": item["itemId"],
                "title": item["title"],
                "price": item.get("price", {}).get("value"),
                "currency": item.get("price", {}).get("currency"),
                "page_url": item.get("itemWebUrl"),
                "img_url": item.get("image", {}).get("imageUrl"),
                "delivery_options": item.get("deliveryOptions", [])
            })
        return items
    except Exception as e:
        print(f"[!] eBay検索エラー: {e}")
        return []

def get_item_details(item_id, marketplace_id='EBAY_US'):
    """
    eBay Browse API (getItem) を使用して商品の詳細情報を取得する。
    """
    token = get_ebay_token()
    if not token:
        return None

    # IDが v1|...|0 の形式でない場合は補完する
    full_id = item_id if "|" in item_id else f"v1|{item_id}|0"
    url = f"https://api.ebay.com/buy/browse/v1/item/{full_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": marketplace_id
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        # 送料情報の簡易抽出
        shipping_cost = 0
        is_shippable = False
        shipping_options = data.get("estimatedAvailabilities", [])
        # Browse APIの getItem では詳細な送料計算には別途リクエストが必要な場合が多いが、
        # ここでは基本的な配送可否のみチェック
        if data.get("shipToLocations"):
            is_shippable = True
            
        return {
            "item_id": item_id,
            "price": data.get("price", {}).get("value"),
            "currency": data.get("price", {}).get("currency"),
            "shipping_cost": shipping_cost,
            "is_shippable": is_shippable,
            "raw_data": data
        }
    except Exception as e:
        print(f"[!] eBay詳細取得エラー (ID:{item_id}): {e}")
        return None

def get_multiple_items_images_api(item_ids, marketplace_id='EBAY_US', max_workers=10):
    """
    複数のeBay商品IDから、Browse APIを使用して並列で複数画像URLを高速取得する。
    ブラウザでの個別スクレイピングを置き換えることで処理速度を劇的に向上させます。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    token = get_ebay_token()
    results = {}
    
    def fetch_single(i_id):
        full_id = i_id if "|" in i_id else f"v1|{i_id}|0"
        url = f"https://api.ebay.com/buy/browse/v1/item/{full_id}"
        headers = {
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": marketplace_id,
            "Content-Type": "application/json"
        }
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                urls = []
                main_img = data.get("image", {}).get("imageUrl")
                if main_img:
                    urls.append(main_img)
                for add_img in data.get("additionalImages", []):
                    u = add_img.get("imageUrl")
                    if u and u not in urls:
                        urls.append(u)
                return i_id, urls
        except Exception as e:
            print(f"    [!] API画像取得エラー(ID:{i_id}): {e}")
        return i_id, []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_single, i_id): i_id for i_id in item_ids}
        for future in as_completed(futures):
            i_id, urls = future.result()
            results[i_id] = urls
            
    return results
