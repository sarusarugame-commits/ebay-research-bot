import os
import re
import urllib.parse
import time
from DrissionPage import ChromiumPage, ChromiumOptions
from ebay_api import retry

def create_browser():
    """main.py 互換: ChromiumPage を返す。"""
    co = ChromiumOptions()
    co.auto_port()
    co.headless(True)
    co.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    return ChromiumPage(co)

def close_browser(browser_page):
    if browser_page:
        try:
            browser_page.quit()
        except:
            pass

@retry(max_retries=2)
def scrape_item_data(url, browser_page):
    """メルカリ・メルカリShops・ラクマ詳細抽出 (画像5枚制限 ＆ 待機バッチリ版)"""
    try:
        if not url.startswith("http"): return None
        
        # ----------------------------------------
        # 🟢 ラクマの処理
        # ----------------------------------------
        is_rakuma = "fril.jp" in url
        if is_rakuma:
            browser_page.get(url)
            
            title_ele = browser_page.ele('css:.item__name', timeout=5) or browser_page.ele('css:.product_title')
            price_ele = browser_page.ele('css:.item__price') or browser_page.ele('css:.item-price')
            
            condition = "不明"
            for row in browser_page.eles('tag:tr', timeout=2):
                if "商品の状態" in row.text: 
                    condition = row.text.replace("商品の状態", "").strip()
            
            # 【修正】ラクマの画像取得ロジック（最大5枚）
            img_urls = []
            for img in browser_page.eles('tag:img', timeout=5):
                src = img.attr('src')
                # フリルの画像CDNかつ、アイコンなどを弾く
                if src and ("fril.jp" in src or "rakuten" in src) and src not in img_urls:
                    if "format=jpg" in src or "/img/" in src or "item" in src:
                        img_urls.append(src)
                if len(img_urls) >= 5: break
                
            return {
                "title": title_ele.text if title_ele else "不明",
                "price": price_ele.text if price_ele else "0",
                "condition": condition,
                "img_urls": img_urls,  # ← 【修正】ちゃんと返すようにしたよ！
                "platform": "ラクマ"
            }

        # ----------------------------------------
        # 🔴 メルカリ & メルカリShopsの処理
        # ----------------------------------------
        print(f"    [SESSION] DrissionPageでセッション確立: {url}")
        browser_page.get(url)
        is_shops = "/shops/product/" in url

        title_ele = browser_page.ele('tag:h1', timeout=5)
        title = title_ele.text if title_ele else "不明"

        price = "0"
        price_ele = browser_page.ele('css:[data-testid="product-price"]') or browser_page.ele('tag:mer-price', timeout=2) or browser_page.ele('@data-testid=price')
        if price_ele:
            price = price_ele.attr('value') or price_ele.text
            if not price and price_ele.shadow_root:
                span_ele = price_ele.shadow_root.ele('tag:span')
                if span_ele: price = span_ele.text

        if not price or price == "0":
            body_text = browser_page.ele('tag:body').text
            match = re.search(r'[¥￥]\s*([\d,]+)', body_text)
            if match: price = match.group(1).replace(',', '')

        # 【修正】画像URL取得 (最大5枚に制限＆Shopsの描画待機)
        img_urls = []
        # timeout=5 を入れて、Reactで画像が遅れて描画されるのをしっかり待つ！
        for img in browser_page.eles('tag:img', timeout=5):
            src = img.attr('src')
            if src and src.startswith("http") and src not in img_urls:
                # Shopsと通常ページで画像を拾う（アイコンは弾く）
                if "mercdn.net" in src or is_shops:
                    if "icon" not in src and "logo" not in src:
                        img_urls.append(src)
            if len(img_urls) >= 5: break  # ← 【修正】5枚でストップ！
        
        cond = "不明"
        if is_shops:
            # Shopsの詳細行から「商品の状態」を探す
            for row in browser_page.eles('css:div[class*="merDisplayRow"]'):
                if "商品の状態" in row.text:
                    cond = row.text.replace("商品の状態", "").strip()
                    break
            if cond == "不明":
                body_text = browser_page.ele('tag:body').text
                if "未使用" in body_text or "新品" in body_text:
                    cond = "新品、未使用"
        else:
            cond_loc = browser_page.ele('@data-testid=商品の状態') or browser_page.ele('tag:mer-display-row@@label=商品の状態')
            if cond_loc: cond = cond_loc.text.replace("商品の状態", "").strip()
        
        return {
            "title": title, "price": price, "condition": cond,
            "img_urls": img_urls, "platform": "メルカリ"
        }

    except Exception as e:
        print(f"    [SCRAPE_DEBUG] エラー: {e}")
        return None

def search_mercari(keyword, browser_page, max_results=20):
    """メルカリ検索 (DrissionPageネイティブ＆Shadow DOMぶち抜き版)"""
    encoded_keyword = urllib.parse.quote(keyword)
    url = f"https://jp.mercari.com/search?keyword={encoded_keyword}&status=on_sale"
    print(f"[*] メルカリ検索開始: {keyword}")
    
    try:
        browser_page.get(url)
        # 【爆速革命】「円」を含む aria-label を持つ要素を一気に取得！
        # これなら Shadow DOM に隠れていても、タグ名が変わっても捕まえられるよ！
        # 描画待ちとして 最初の item-cell が出るまで待機
        browser_page.wait.ele_displayed('css:[data-testid="item-cell"]', timeout=10)
        
        # ページ内の「〜円」というラベルを持つ要素を全取得
        label_eles = browser_page.eles('css:[aria-label*="円"]', timeout=5)
        
        items_data = []
        for ele in label_eles:
            if len(items_data) >= max_results: break
            
            label = ele.attr('aria-label') or ""
            # 親または自分自身を辿って a タグ（リンク）を探す
            # ele.closest('tag:a') が使えればベストだが、DrissionPageの仕様に合わせる
            a_tag = ele if ele.tag == 'a' else ele.parent('tag:a')
            if not a_tag: continue
            
            i_url = a_tag.attr('href')
            if not i_url: continue
            if not i_url.startswith("http"):
                i_url = f"https://jp.mercari.com{i_url}"

            # 売り切れ判定 (sticker は ele または a_tag の中にある)
            sticker = ele.ele('css:[data-testid="thumbnail-sticker"]', timeout=0) or a_tag.ele('css:[data-testid="thumbnail-sticker"]', timeout=0)
            sticker_label = sticker.attr('aria-label').lower() if sticker else ""
            
            # 1. 売り切れ判定 (Shopsバッジは除外)
            if sticker_label and "shops" not in sticker_label:
                if "売り切れ" in sticker_label or "sold" in sticker_label:
                    continue
            
            # 2. ラベルからタイトルと価格をパース
            # 形式例: "商品名... の画像 12,800円"
            title = "不明"
            price = "0"
            
            if " の画像 " in label:
                parts = label.split(" の画像 ")
                title = parts[0].strip()
                price_match = re.search(r'([\d,]+)円', parts[-1])
                if price_match:
                    price = price_match.group(1).replace(',', '')
            else:
                price_match = re.search(r'([\d,]+)円', label)
                if price_match:
                    price = price_match.group(1).replace(',', '')
                    title = label.replace(price_match.group(0), "").strip()

            if i_url:
                items_data.append({
                    "title": title,
                    "page_url": i_url, "price": price, "platform": "メルカリ"
                })
        # 重複除去 (同じ a タグを複数回拾う可能性があるため)
        seen_urls = set()
        unique_items = []
        for it in items_data:
            if it["page_url"] not in seen_urls:
                unique_items.append(it)
                seen_urls.add(it["page_url"])
        
        print(f"    [メルカリ] {len(unique_items)} 件の商品を取得しました。")
        return unique_items
    except Exception as e:
        print(f"    [Error] search_mercari: {e}")
        return []

def search_rakuma(keyword, browser_page, max_results=10):
    """ラクマ検索 (DrissionPage実装・本稼働版)"""
    encoded_keyword = urllib.parse.quote(keyword)
    url = f"https://fril.jp/search/{encoded_keyword}"
    print(f"[*] ラクマ検索開始: {keyword}")
    
    results = []
    try:
        browser_page.get(url)
        
        # ラクマの商品リストを待機
        items = browser_page.eles('css:.item-box', timeout=15)
        if not items:
            items = browser_page.eles('css:.item', timeout=5)
            
        for item in items:
            if len(results) >= max_results: break
            
            a_tag = item.ele('tag:a')
            if not a_tag: continue
            i_url = a_tag.attr('href')
            
            img_tag = item.ele('tag:img')
            title_ele = item.ele('css:.item-box__item-name') or item.ele('css:.link_search_title')
            title = (title_ele.text if title_ele else None) or (img_tag.attr('alt') if img_tag else "不明")
            
            price_ele = item.ele('css:.item-box__item-price') or item.ele('css:.price')
            price = price_ele.text if price_ele else "0"
            
            if i_url:
                results.append({
                    "title": title.strip(),
                    "page_url": i_url,
                    "price": price,
                    "platform": "ラクマ"
                })
        print(f"    [ラクマ] {len(results)} 件の商品を取得しました。")
    except Exception as e:
        print(f"    [Error] search_rakuma: {e}")
        
    return results
