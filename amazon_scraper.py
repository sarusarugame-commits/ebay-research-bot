import re
import time
from DrissionPage import ChromiumPage, ChromiumOptions
import logging

logger = logging.getLogger(__name__)

def search_amazon(keyword, browser_page, max_results=5):
    """Amazon.jp で商品を検索し、候補リストを返す"""
    url = f"https://www.amazon.co.jp/s?k={re.sub(r'[\s　]+', '+', keyword)}"
    logger.info(f"    [*] Amazon検索中: {url}")
    
    try:
        browser_page.get(url)
        # 検索結果が出るまで少し待機
        browser_page.wait.ele_displayed('css:[data-component-type="s-search-result"]', timeout=10)
        
        results = []
        items = browser_page.eles('css:[data-component-type="s-search-result"]')
        
        for item in items[:max_results]:
            try:
                title_ele = item.ele('css:h2 a')
                title = title_ele.text
                page_url = title_ele.attr('href')
                if page_url.startswith('/'):
                    page_url = "https://www.amazon.co.jp" + page_url
                
                img_ele = item.ele('css:img.s-image')
                img_url = img_ele.attr('src')
                
                results.append({
                    "platform": "Amazon",
                    "title": title,
                    "page_url": page_url,
                    "img_url": img_url
                })
            except Exception as e:
                logger.debug(f"      [SKIP] Amazonアイテムパースエラー: {e}")
                continue
                
        return results
    except Exception as e:
        logger.error(f"    [!] Amazon検索エラー: {e}")
        return []

def search_amazon_via_google(keyword, browser_page, max_results=3):
    """Google検索経由で Amazon.jp の商品ページを探す"""
    query = f"{keyword} amazon.co.jp"
    url = f"https://www.google.com/search?q={re.sub(r'[\s　]+', '+', query)}"
    logger.info(f"    [*] Google経由でAmazonを検索中: {url}")
    
    try:
        browser_page.get(url)
        # 検索結果が出るまで少し待機
        browser_page.wait.ele_displayed('css:#search', timeout=10)
        
        results = []
        # Googleの検索結果（リンク）を抽出
        links = browser_page.eles('css:#search a')
        
        # Amazon.co.jp の商品ページ（dp/ や /gp/product/ を含む）を探す
        seen_urls = set()
        for link in links:
            href = link.attr('href')
            if not href: continue
            
            # Amazon.co.jp の商品詳細ページっぽいURLか判定
            if "amazon.co.jp" in href and ("/dp/" in href or "/gp/product/" in href):
                # クエリパラメータを除去して正規化
                base_url = href.split('?')[0].split('#')[0]
                if base_url in seen_urls: continue
                seen_urls.add(base_url)
                
                # タイトルを取得（もしあれば）
                title = link.text or "Amazon商品ページ"
                
                results.append({
                    "platform": "Amazon(Google経由)",
                    "title": title,
                    "page_url": base_url,
                    "img_url": "" # Google上では画像URL特定が難しいため空にする（画像判定が必要なら詳細ページから取る必要あり）
                })
                
                if len(results) >= max_results:
                    break
                    
        return results
    except Exception as e:
        logger.error(f"    [!] Google経由のAmazon検索エラー: {e}")
        return []

def scrape_amazon_specs(url, browser_page):
    """Amazonの商品詳細ページからサイズと重量を抽出する"""
    logger.info(f"    [*] Amazon詳細ページ解析中: {url}")
    specs = {"weight": "不明", "dimensions": "不明"}
    
    try:
        browser_page.get(url)
        # ページ読み込み待機
        time.sleep(2)
        
        # 1. 箇条書き詳細 (detailBullets_feature_div) を探す
        bullets_div = browser_page.ele('#detailBullets_feature_div')
        if bullets_div:
            text = bullets_div.text
            # 重量
            w_match = re.search(r"(?:発送重量|商品の重量|Item Weight)\s*[:：]\s*([\d.]+)\s*(g|kg|グラム|キロ)", text, re.I)
            if w_match:
                specs["weight"] = f"{w_match.group(1)}{w_match.group(2)}"
            
            # 梱包サイズ/商品サイズ
            d_match = re.search(r"(?:梱包サイズ|商品サイズ|Product Dimensions|Package Dimensions)\s*[:：]\s*([\d.x *×]+)\s*(cm|mm|センチ|インチ|in)", text, re.I)
            if d_match:
                specs["dimensions"] = f"{d_match.group(1).strip()} {d_match.group(2)}"

        # 2. テーブル形式 (prodDetails) を探す (1で見つからなかった場合)
        if specs["weight"] == "不明" or specs["dimensions"] == "不明":
            table = browser_page.ele('#prodDetails')
            if table:
                text = table.text
                if specs["weight"] == "不明":
                    w_match = re.search(r"(?:発送重量|商品の重量|Item Weight)\s+([\d.]+)\s*(g|kg|グラム|キロ)", text, re.I)
                    if w_match: specs["weight"] = f"{w_match.group(1)}{w_match.group(2)}"
                
                if specs["dimensions"] == "不明":
                    d_match = re.search(r"(?:梱包サイズ|商品サイズ|Product Dimensions)\s+([\d.x *×]+)\s*(cm|mm)", text, re.I)
                    if d_match: specs["dimensions"] = f"{d_match.group(1).strip()} {d_match.group(2)}"

        # 3. テクニカルテーブル形式 (common for some categories)
        if specs["weight"] == "不明" or specs["dimensions"] == "不明":
            tech_div = browser_page.ele('#productDetails_techSpec_section_1')
            if tech_div:
                text = tech_div.text
                if specs["weight"] == "不明":
                    w_match = re.search(r"(?:重量|Weight)\s+([\d.]+)\s*(g|kg)", text, re.I)
                    if w_match: specs["weight"] = f"{w_match.group(1)}{w_match.group(2)}"
                
                if specs["dimensions"] == "不明":
                    d_match = re.search(r"(?:サイズ|Dimensions)\s+([\d.x *×]+)\s*(cm|mm)", text, re.I)
                    if d_match: specs["dimensions"] = f"{d_match.group(1).strip()} {d_match.group(2)}"

        return specs
    except Exception as e:
        logger.error(f"    [!] Amazonスペック抽出エラー: {url} -> {e}")
        return specs
