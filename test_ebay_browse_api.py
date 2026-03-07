import requests
import base64
import json
from config import EBAY_APP_ID, EBAY_CLIENT_SECRET

def get_ebay_token():
    """
    Client Credentials Grant を使用して OAuth 2.0 アクセストークンを取得する
    """
    if not EBAY_APP_ID or not EBAY_CLIENT_SECRET:
        print("[!] EBAY_APP_ID または EBAY_CLIENT_SECRET が設定されていません。")
        return None

    url = "https://api.ebay.com/identity/v1/oauth2/token"
    
    # 資格情報を Base64 エンコード
    auth_str = f"{EBAY_APP_ID}:{EBAY_CLIENT_SECRET}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {b64_auth}"
    }
    
    payload = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope"
    }
    
    try:
        response = requests.post(url, headers=headers, data=payload)
        if response.status_code == 200:
            token_data = response.json()
            return token_data.get("access_token")
        else:
            print(f"[!] トンクン取得失敗: {response.status_code}")
            print(response.text)
            return None
    except Exception as e:
        print(f"[!] トークン取得中に例外が発生: {e}")
        return None

def test_ebay_browse_api(keywords):
    """
    eBay Browse API (Item Summary Search) を使用したテスト検索
    """
    token = get_ebay_token()
    if not token:
        return

    # エンドポイント
    url = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"
    }
    
    params = {
        "q": keywords,
        "limit": 5,
        "filter": "conditions:{NEW}"
    }

    print(f"[*] eBay Browse API (OAuth) 実行中: {keywords}")
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            items = data.get("itemSummaries", [])
            
            print(f" -> {len(items)} 件の商品が見つかりました。\n")
            
            for i, item in enumerate(items, 1):
                title = item.get("title", "Unknown")
                price_data = item.get("price", {})
                price = price_data.get("value", "0")
                currency = price_data.get("currency", "USD")
                item_url = item.get("itemWebUrl", "#")
                
                print(f"{i}. {title}")
                print(f"   価格: {price} {currency}")
                print(f"   URL:  {item_url}\n")
        else:
            print(f"[!] APIエラー: {response.status_code}")
            print(response.text)
            
    except Exception as e:
        print(f"[!] 例外が発生しました: {e}")

if __name__ == "__main__":
    # テスト用クエリ
    test_query = "CASIO Oceanus OCW-S6000PBS-7AJR White"
    test_ebay_browse_api(test_query)
