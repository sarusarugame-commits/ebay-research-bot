import re
import time
from DrissionPage import ChromiumPage, ChromiumOptions
import logging

logger = logging.getLogger(__name__)

def search_amazon(keyword, browser_page, max_results=5):
    """Amazon.jp で商品を検索し、候補リストを返す（requests優先）"""
    import requests as req
    from html.parser import HTMLParser
    url = f"https://www.amazon.co.jp/s?k={re.sub(r'[\s　]+', '+', keyword)}"
    logger.info(f"    [*] Amazon検索中: {url}")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "ja-JP,ja;q=0.9",
    }
    try:
        r = req.get(url, headers=headers, timeout=8)
        html = r.text
        results = []
        # ASIN抽出
        asins = re.findall(r'data-asin="([A-Z0-9]{10})"', html)
        titles = re.findall(r'<h2[^>]*>.*?<a[^>]*href="(/dp/[^"]+)"[^>]*>.*?<span[^>]*>([^<]+)</span>', html, re.S)
        seen = set()
        for href, title in titles:
            base = "https://www.amazon.co.jp" + href.split('?')[0]
            if base in seen: continue
            seen.add(base)
            results.append({"platform": "Amazon", "title": title.strip(), "page_url": base, "img_url": ""})
            if len(results) >= max_results: break
        if results:
            return results
    except Exception as e:
        logger.debug(f"    [requests] Amazon検索失敗: {e}")

    # フォールバック: ブラウザ
    try:
        browser_page.get(url)
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
                img_url = img_ele.attr('src') if img_ele else ""
                results.append({"platform": "Amazon", "title": title, "page_url": page_url, "img_url": img_url})
            except:
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

def _get_amazon_html(url, browser_page):
    """requestsでAmazonのHTMLを取得、失敗時はブラウザにフォールバック"""
    import requests as req
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "ja-JP,ja;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        r = req.get(url, headers=headers, timeout=8)
        if r.status_code == 200 and "amazon" in r.url:
            return r.text
    except Exception as e:
        logger.debug(f"    [requests] Amazon取得失敗: {e}")
    # フォールバック: ブラウザ
    browser_page.get(url)
    body_ele = browser_page.ele('tag:body', timeout=8)
    return body_ele.text if body_ele else ""

def scrape_amazon_specs(url, browser_page):
    """Amazonの商品詳細ページからサイズと重量を抽出する"""
    logger.info(f"    [*] Amazon詳細ページ解析中: {url}")
    specs = {"weight": "不明", "dimensions": "不明"}
    
    try:
        full_text = _get_amazon_html(url, browser_page)
        # DOMが必要な場合のみブラウザ使用
        body_ele = None
        def _get_section_text(selector):
            nonlocal body_ele
            # まずfull_textから正規表現で探す（高速）
            return full_text
        
        # 1. 箇条書き詳細 (detailBullets_feature_div) を探す
        # full_textから該当セクションを切り出す
        bullets_match = re.search(r'((?:発送重量|商品の重量|本体重量|Item Weight|梱包サイズ|商品サイズ|商品の寸法).{0,500})', full_text, re.S)
        if True:  # 常にfull_textを使用
            text = full_text
            
            # 重量 (本体重量などを追加し、改行やスペースを許容)
            w_match = re.search(r"(?:発送重量|商品の重量|本体重量|Item Weight)\s*[:：]?\s*([\d.]+)\s*(g|kg|グラム|キロ)", text, re.I)
            if w_match:
                specs["weight"] = f"{w_match.group(1)}{w_match.group(2)}"
            
            # 梱包サイズ/商品の寸法 (表記揺れを追加、数字間の改行も許容)
            d_match = re.search(r"(?:梱包サイズ|商品サイズ|商品の寸法|製品サイズ|Product Dimensions|Package Dimensions)\s*[:：]?\s*([\d.x\s*×]+)\s*(cm|mm|センチ|インチ|in)", text, re.I)
            if d_match:
                clean_dim = re.sub(r'\s+', ' ', d_match.group(1)).strip()
                specs["dimensions"] = f"{clean_dim} {d_match.group(2)}"

        # 2. テーブル形式 (prodDetails) を探す（full_textから）
        if specs["weight"] == "不明" or specs["dimensions"] == "不明":
            if True:
                text = full_text
                if specs["weight"] == "不明":
                    w_match = re.search(r"(?:発送重量|商品の重量|本体重量|Item Weight)\s*[:：]?\s*([\d.]+)\s*(g|kg|グラム|キロ)", text, re.I)
                    if w_match: specs["weight"] = f"{w_match.group(1)}{w_match.group(2)}"
                
                if specs["dimensions"] == "不明":
                    d_match = re.search(r"(?:梱包サイズ|商品サイズ|商品の寸法|製品サイズ|Product Dimensions)\s*[:：]?\s*([\d.x\s*×]+)\s*(cm|mm)", text, re.I)
                    if d_match: 
                        clean_dim = re.sub(r'\s+', ' ', d_match.group(1)).strip()
                        specs["dimensions"] = f"{clean_dim} {d_match.group(2)}"

        # 3. テクニカルテーブル形式（full_textから）
        if specs["weight"] == "不明" or specs["dimensions"] == "不明":
            if True:
                text = full_text
                if specs["weight"] == "不明":
                    w_match = re.search(r"(?:重量|Weight)\s*[:：]?\s*([\d.]+)\s*(g|kg)", text, re.I)
                    if w_match: specs["weight"] = f"{w_match.group(1)}{w_match.group(2)}"
                
                if specs["dimensions"] == "不明":
                    d_match = re.search(r"(?:サイズ|Dimensions)\s*[:：]?\s*([\d.x\s*×]+)\s*(cm|mm)", text, re.I)
                    if d_match: 
                        clean_dim = re.sub(r'\s+', ' ', d_match.group(1)).strip()
                        specs["dimensions"] = f"{clean_dim} {d_match.group(2)}"

        # 4. 🌟追加：ページ全体からの強引な抽出 (上の3つの場所が見つからなかった場合の最終手段)
        if specs["weight"] == "不明" or specs["dimensions"] == "不明":
            if full_text:
                if specs["weight"] == "不明":
                    w_match = re.search(r"(?:発送重量|商品の重量|本体重量|Item Weight)\s*[:：]?\s*([\d.]+)\s*(g|kg|グラム|キロ)", full_text, re.I)
                    if w_match: specs["weight"] = f"{w_match.group(1)}{w_match.group(2)}"
                
                if specs["dimensions"] == "不明":
                    d_match = re.search(r"(?:梱包サイズ|商品サイズ|商品の寸法|製品サイズ|梱包サイズ\(LxWxH\))\s*[:：]?\s*([\d.x\s*×]+)\s*(cm|mm|センチ|インチ|in)", full_text, re.I)
                    if d_match:
                        clean_dim = re.sub(r'\s+', ' ', d_match.group(1)).strip()
                        specs["dimensions"] = f"{clean_dim} {d_match.group(2)}"

        return specs
    except Exception as e:
        logger.error(f"    [!] Amazonスペック抽出エラー: {url} -> {e}")
        return specs
