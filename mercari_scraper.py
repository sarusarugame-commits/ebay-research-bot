import os
import re
import urllib.parse
import time
from DrissionPage import ChromiumPage, ChromiumOptions
from ebay_api import retry

@retry(max_retries=2)
def scrape_item_data(url, browser_page):
    # (既存のスクレイピングロジック)
    return {}

def search_mercari(keyword, browser_page, max_results=20):
    # (既存の検索ロジック)
    return []

def search_rakuma(keyword, browser_page, max_results=10):
    # (既存の検索ロジック)
    return []
