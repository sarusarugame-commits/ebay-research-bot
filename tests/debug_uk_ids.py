import requests, base64, json
from config import EBAY_APP_ID, EBAY_CLIENT_SECRET

def get_token():
    url = "https://api.ebay.com/identity/v1/oauth2/token"
    auth_str = f"{EBAY_APP_ID}:{EBAY_CLIENT_SECRET}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Authorization": f"Basic {b64_auth}"}
    payload = {"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"}
    return requests.post(url, headers=headers, data=payload).json().get("access_token")

token = get_token()
search_url = "https://api.ebay.com/buy/browse/v1/item_summary/search"
headers = {
    "Authorization": f"Bearer {token}", 
    "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
    "X-EBAY-C-ENDUSERCTX": "contextualLocation=country%3DGB"
}
params = {"q": "CASIO Oceanus OCW-S6000", "limit": 20, "filter": "conditions:{NEW}"}
r = requests.get(search_url, headers=headers, params=params).json()

for it in r.get("itemSummaries", []):
    print(f"ID: {it.get('itemId')} | Title: {it.get('title')[:30]}")
    print(f"  Price: {it.get('price').get('value')} {it.get('price').get('currency')}")
    print(f"  Shipping Options: {it.get('shippingOptions')}")
    print("-" * 50)
