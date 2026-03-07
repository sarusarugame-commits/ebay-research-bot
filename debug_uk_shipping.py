import requests
import base64
import json
import os
from config import EBAY_APP_ID, EBAY_CLIENT_SECRET

def get_token():
    auth_str = f"{EBAY_APP_ID}:{EBAY_CLIENT_SECRET}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    url = "https://api.ebay.com/identity/v1/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Authorization": f"Basic {b64_auth}"}
    payload = {"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"}
    return requests.post(url, headers=headers, data=payload).json().get("access_token")

def debug_uk():
    token = get_token()
    url = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
        "X-EBAY-C-ENDUSERCTX": "contextualLocation=country%3DGB"
    }
    # "does not ship" が多そうな US のセラーの商品を UK で検索してみる
    params = {"q": "watch", "limit": 40}
    
    resp = requests.get(url, headers=headers, params=params)
    items = resp.json().get("itemSummaries", [])
    
    for itm in items:
        # もし Seller が US なのに UK で出ているものを探す
        location = itm.get("itemLocation", {}).get("country", "")
        if location != "GB":
            print(f"Title: {itm.get('title')[:50]}")
            print(f"  Item Location: {location}")
            print(f"  ShippingOptions: {json.dumps(itm.get('shippingOptions'), indent=2)}")
            print("-" * 20)

if __name__ == "__main__":
    debug_uk()
