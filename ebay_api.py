import os
import functools
import time
import base64
import requests
from urllib.parse import urlparse, parse_qs
from datetime import datetime
from config import EBAY_APP_ID, EBAY_CLIENT_SECRET, MAX_RETRIES, RETRY_BASE_DELAY

def retry(max_retries=MAX_RETRIES, base_delay=RETRY_BASE_DELAY):
    """汎用的なリトライデコレータ（指数バックオフ）"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries:
                        print(f"[{func.__name__}] 最大リトライ回数到達。エラー: {e}")
                        raise
                    delay = base_delay ** attempt
                    print(f"[{func.__name__} エラー] {e}")
                    print(f"[{func.__name__}] {delay}秒後に再試行します... ({attempt}/{max_retries})")
                    time.sleep(delay)
        return wrapper
    return decorator

def get_start_date():
    """日付ロジック (Browse APIでは直接のサポートがないため、参考値として保持)"""
    now = datetime.now()
    if now.day <= 15:
        start_date = datetime(now.year, now.month, 1)
    else:
        start_date = datetime(now.year, now.month, 16)
    return start_date.strftime("%Y-%m-%dT%H:%M:%S.000Z")

def parse_ebay_url(url):
    """eBayのURLから _ssn（セラー名）と _nkw（キーワード）を抽出する"""
    parsed_url = urlparse(url)
    qs = parse_qs(parsed_url.query)
    
    seller = qs.get('_ssn', [''])[0]
    keyword = qs.get('_nkw', [''])[0]
    
    return {
        "seller": seller,
        "keyword": keyword
    }

def get_ebay_token():
    """Client Credentials GrantフローでOAuth2トークンを取得する"""
    if not EBAY_APP_ID or not EBAY_CLIENT_SECRET:
        raise ValueError("eBayのApp IDまたはClient Secretが設定されていません(.envを確認してください)。")
        
    url = "https://api.ebay.com/identity/v1/oauth2/token"
    credentials = f"{EBAY_APP_ID}:{EBAY_CLIENT_SECRET}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode('utf-8')
    
    headers = {
        "Authorization": f"Basic {encoded_credentials}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope"
    }
    
    response = requests.post(url, headers=headers, data=data)
    if response.status_code != 200:
        raise Exception(f"eBay OAuth2トークン取得失敗: {response.text}")
        
    return response.json().get("access_token")

@retry()
def fetch_items(seller, keyword="", start_date_str=""):
    """
    最新のBrowse APIを使用して指定されたセラーから新着順で商品を取得する。
    Finding APIの制限上限エラー（10001等）を回避し、最新のRESTフローを使用。
    """
    if not seller:
        raise ValueError("セラー名(_ssn)がURLから抽出できませんでした。")

    print("[*] eBay APIの認証トークンを取得中...")
    token = get_ebay_token()
    
    url = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    
    q = keyword if keyword else "*"
    sort_query = "newlyListed"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
        "Content-Type": "application/json"
    }
    
    params = {
        "q": q,
        "filter": f"sellers:{{{seller}}}",
        "sort": sort_query,
        "limit": 10
    }
    
    print(f"[*] eBay Browse API 検索実行: セラー={seller}, キーワード={keyword}")
    response = requests.get(url, headers=headers, params=params)
    
    if response.status_code != 200:
        raise Exception(f"Browse API 検索エラー: {response.text}")
        
    data = response.json()
    item_summaries = data.get("itemSummaries", [])
    
    items = []
    for item in item_summaries:
        item_id = item.get("itemId")
        # Browse APIのitemIdは 'v1|23532...|0' のようなフォーマットのことがあるので後半を取り出す
        if "|" in item_id:
            raw_item_id = item_id.split("|")[1]
        else:
            raw_item_id = item_id

        title = item.get("title")
        image_url = item.get("image", {}).get("imageUrl")
        
        # MPN等は一覧取得では取れないが、LLMはタイトルから十分な推測が可能なためNoneで処理を継続
        items.append({
            "id": raw_item_id,
            "title": title,
            "image_url": image_url,
            "model_number": None
        })
            
    return items
