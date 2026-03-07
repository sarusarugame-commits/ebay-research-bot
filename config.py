import os
from dotenv import load_dotenv

# .envファイルを読み込む
load_dotenv()

# API設定
EBAY_APP_ID = os.getenv("EBAY_APP_ID")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# 楽天・Yahoo API
RAKUTEN_APPLICATION_ID = os.getenv("RAKUTEN_APPLICATION_ID")
RAKUTEN_ACCESS_KEY = os.getenv("RAKUTEN_ACCESS_KEY")
RAKUTEN_AFFILIATE_ID = os.getenv("RAKUTEN_AFFILIATE_ID")
YAHOO_CLIENT_ID = os.getenv("YAHOO_CLIENT_ID")

# 定数
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2

# パラメータ不足のチェック
if not EBAY_APP_ID or not EBAY_CLIENT_SECRET:
    print("WARNING: .env に EBAY_APP_ID または EBAY_CLIENT_SECRET が設定されていません。")
if not GOOGLE_APPLICATION_CREDENTIALS:
    print("WARNING: .env に GOOGLE_APPLICATION_CREDENTIALS が設定されていません。")
    print("※または環境変数として指定してください。")
if not OPENROUTER_API_KEY:
    print("WARNING: .env に OPENROUTER_API_KEY が設定されていません。LLM推論機能が利用できません。")
if not RAKUTEN_APPLICATION_ID or not RAKUTEN_ACCESS_KEY:
    print("WARNING: .env に RAKUTEN_APPLICATION_ID または RAKUTEN_ACCESS_KEY が設定されていません。楽天市場の結果が取得できません。")
if not YAHOO_CLIENT_ID:
    print("WARNING: .env に YAHOO_CLIENT_ID が設定されていません。Yahooショッピングの結果が取得できません。")
