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

def diagnose(market_id, country_code):
    token = get_token()
    url = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": market_id,
        "X-EBAY-C-ENDUSERCTX": f"contextualLocation=country%3D{country_code}"
    }
    params = {"q": "CASIO Oceanus OCW-S6000", "limit": 10}
    
    print(f"\n=== Diagnostics for {market_id} (Location: {country_code}) ===")
    resp = requests.get(url, headers=headers, params=params)
    data = resp.json()
    items = data.get("itemSummaries", [])
    
    for i, itm in enumerate(items):
        print(f"\n--- Item {i+1}: {itm.get('title')[:50]} ---")
        price = itm.get("price", {})
        shipping = itm.get("shippingOptions", [])
        print(f"  Price: {price}")
        print(f"  ShippingOptions: {json.dumps(shipping, indent=2)}")
        
        # Check if shipping has cost
        if shipping:
            cost = shipping[0].get("shippingCost", {})
            print(f"  Detected Cost: {cost}")
        else:
            print("  [!] NO SHIPPING OPTIONS FOUND")

if __name__ == "__main__":
    diagnose("EBAY_US", "US")
    diagnose("EBAY_GB", "GB")
