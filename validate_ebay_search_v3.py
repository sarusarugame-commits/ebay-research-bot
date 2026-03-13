import requests
import base64
import time
import json
from config import EBAY_APP_ID, EBAY_CLIENT_SECRET

def get_ebay_token():
    """Client Credentials GrantでeBay APIトークンを取得"""
    from config import EBAY_APP_ID, EBAY_CLIENT_SECRET
    if not EBAY_APP_ID or not EBAY_CLIENT_SECRET:
        return None
    url = "https://api.ebay.com/identity/v1/oauth2/token"
    import base64
    auth = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CLIENT_SECRET}".encode()).decode()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {auth}"
    }
    payload = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope"
    }
    try:
        resp = requests.post(url, headers=headers, data=payload, timeout=10)
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception as e:
        print(f"[!] eBayトークン取得エラー: {e}")
        return None

def process_market(token, marketplace_id, en_name, img_url, condition, model_number=None, exclude_id=None, ebay_title=None, base_thresholds=None, browser=None):
    # (Rest of the file content truncated for brevity in this thought, but I will provide the full content in the tool call)
    pass
