import os
import re
import json
import requests
import urllib.parse
import time
from DrissionPage import ChromiumPage, ChromiumOptions
from ebay_api import retry

def create_browser():
    """main.py 互換: ChromiumPage を返す。"""
    co = ChromiumOptions()
    co.auto_port()
    co.set_argument('--window-size=1280,720')
    co.remove_argument('--start-maximized')
    co.headless(True)
    co.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    return ChromiumPage(co)

def close_browser(browser_page):
    if browser_page:
        try:
            browser_page.quit()
        except:
            pass

def _fetch_mercari_item_via_api(item_id, browser_page):
    """
    メルカリ商品詳細をXHR傍受で高速取得。
    取得できた場合は dict、失敗時は None を返す。
    """
    try:
        # フルURLではなくドメイン＋パスの部分文字列で傍受する
        browser_page.listen.start("api.mercari.jp/v2/items")
        browser_page.get(f"https://jp.mercari.com/item/{item_id}")
        packet = browser_page.listen.wait(timeout=8)
        browser_page.listen.stop()
        if not packet:
            return None
        body = packet.response.body
        print(f"    [API_DEBUG] packet URL: {packet.url}")
        print(f"    [API_DEBUG] body type: {type(body)}, preview: {str(body)[:300]}")
        data = json.loads(body) if isinstance(body, str) else body
        item = data.get("data") or data.get("item") or data
        if not item or not item.get("name"):
            print(f"    [API_DEBUG] item keys: {list(item.keys()) if isinstance(item, dict) else type(item)}")
            return None

        img_urls = []
        for photo in (item.get("photos") or item.get("thumbnails") or [])[:5]:
            url = (photo.get("image_url") or photo.get("url") or photo.get("thumbnail_url")) if isinstance(photo, dict) else photo
            if url:
                img_urls.append(url)

        cond_raw = (item.get("item_condition") or {}).get("name") or item.get("condition_name") or "不明"

        return {
            "title": item.get("name", "不明"),
            "price": str(item.get("price", "0")),
            "condition": cond_raw,
            "img_urls": img_urls,
            "platform": "メルカリ"
        }
    except Exception as e:
        print(f"    [API] メルカリXHR傍受失敗: {e}")
        try:
            browser_page.listen.stop()
        except:
            pass
        return None

def _fetch_rakuma_item_via_api(item_id, browser_page):
    """
    ラクマ商品詳細をXHR傍受で高速取得。
    取得できた場合は dict、失敗時は None を返す。
    """
    try:
        # fril.jp の商品詳細APIエンドポイントを傍受
        browser_page.listen.start("api.fril.jp")
        browser_page.get(f"https://fril.jp/items/{item_id}")
        packet = browser_page.listen.wait(timeout=8)
        browser_page.listen.stop()
        if not packet:
            return None
        body = packet.response.body
        print(f"    [API_DEBUG] packet URL: {packet.url}")
        print(f"    [API_DEBUG] body type: {type(body)}, preview: {str(body)[:300]}")
        data = json.loads(body) if isinstance(body, str) else body
        item = data.get("data") or data.get("item") or data
        if not item or not item.get("name"):
            print(f"    [API_DEBUG] item keys: {list(item.keys()) if isinstance(item, dict) else type(item)}")
            return None

        img_urls = []
        for photo in (item.get("photos") or [])[:5]:
            url = (photo.get("image_url") or photo.get("url")) if isinstance(photo, dict) else photo
            if url:
                img_urls.append(url)

        cond_raw = (item.get("item_condition") or {}).get("name") or "不明"

        return {
            "title": item.get("name", "不明"),
            "price": str(item.get("price", "0")),
            "condition": cond_raw,
            "img_urls": img_urls,
            "platform": "ラクマ"
        }
    except Exception as e:
        print(f"    [API] ラクマXHR傍受失敗: {e}")
        try:
            browser_page.listen.stop()
        except:
            pass
        return None

_SCRAPE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ja-JP,ja;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def _fetch_mercari_via_requests(url):
    """
    requests で __NEXT_DATA__ JSON を取得してメルカリ商品情報を返す。
    成功時は dict、失敗時は None。
    """
    try:
        r = requests.get(url, headers=_SCRAPE_HEADERS, timeout=8)
        if r.status_code != 200:
            return None
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
        if not m:
            return None
        data = json.loads(m.group(1))

        # Next.js のページデータ構造を辿る
        props = data.get("props", {}).get("pageProps", {})
        item = props.get("item") or props.get("data", {}).get("item")
        if not item:
            # Shops の場合は別キー
            item = props.get("shopItem") or props.get("product")
        if not item:
            return None

        img_urls = []
        for photo in (item.get("photos") or item.get("thumbnails") or [])[:5]:
            u = (photo.get("image_url") or photo.get("url") or photo.get("thumbnail_url")) if isinstance(photo, dict) else photo
            if u:
                img_urls.append(u)

        # mercdn.net の画像が取れなかった場合は og:image などを補完
        if not img_urls:
            og = re.findall(r'<meta property="og:image" content="([^"]+)"', r.text)
            img_urls = og[:5]

        cond = (item.get("item_condition") or {}).get("name") or item.get("condition_name") or "不明"
        price = str(item.get("price", "0"))
        title = item.get("name", "不明")

        print(f"    [requests] メルカリ高速取得成功")
        return {"title": title, "price": price, "condition": cond, "img_urls": img_urls, "platform": "メルカリ"}
    except Exception as e:
        print(f"    [requests] メルカリ取得失敗: {e}")
        return None

def _fetch_rakuma_via_requests(url):
    """
    requests で ラクマ商品ページHTMLをパースして情報を返す。
    成功時は dict、失敗時は None。
    """
    try:
        r = requests.get(url, headers=_SCRAPE_HEADERS, timeout=8)
        if r.status_code != 200:
            return None

        # __NEXT_DATA__ があれば使う（ラクマも Next.js ベースに移行済み）
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
        if m:
            data = json.loads(m.group(1))
            props = data.get("props", {}).get("pageProps", {})
            # ラクマのキー候補を網羅的に試す
            item = (props.get("item")
                    or props.get("itemDetail")
                    or props.get("data", {}).get("item")
                    or props.get("initialData", {}).get("item")
                    or props.get("dehydratedState", {}).get("queries", [{}])[0].get("state", {}).get("data", {}).get("item"))
            if item:
                img_urls = []
                for photo in (item.get("photos") or item.get("images") or item.get("imageUrls") or [])[:5]:
                    u = (photo.get("image_url") or photo.get("imageUrl") or photo.get("url")) if isinstance(photo, dict) else photo
                    if u:
                        img_urls.append(u)
                # __NEXT_DATA__ に画像がなければ直接HTMLからも補完
                if not img_urls:
                    img_urls = re.findall(r'https://[^\s"\'{}<>()]+(?:fril\.jp|r10s\.jp)[^\s"\'{}<>()]+\.(?:jpg|jpeg|png|webp)', r.text)[:5]
                raw_status = (item.get("status") or "").lower()
                if raw_status in ("sold_out", "trading", "stop", "suspended"):
                    print(f"    [SKIP] ラクマ売切れ商品をスキップ (status={raw_status})")
                    return None
                cond = (item.get("item_condition") or {}).get("name") or item.get("status") or "不明"
                print(f"    [requests] ラクマ高速取得成功 (Next.js, {len(img_urls)}枚)")
                return {
                    "title": item.get("name", "不明"),
                    "price": str(item.get("price", "0")),
                    "condition": cond,
                    "img_urls": img_urls,
                    "platform": "ラクマ"
                }

        # __NEXT_DATA__ でitemが取れなかった場合: HTMLから直接画像URLを抽出
        title_m = re.search(r'<meta property="og:title" content="([^"]+)"', r.text)

        # 価格抽出: 優先度順に試す
        price_str = "0"
        # ① og:description から「¥1,234」「1,234円」パターン
        desc_m = re.search(r'<meta property="og:description" content="([^"]+)"', r.text)
        if desc_m:
            p = re.search(r'[¥￥]?([\d,]+)\s*円', desc_m.group(1))
            if p:
                price_str = p.group(1).replace(",", "")
        # ② JSON-LD の price フィールド
        if price_str == "0":
            ld_m = re.search(r'"price"\s*:\s*"?([\d,]+)"?', r.text)
            if ld_m:
                price_str = ld_m.group(1).replace(",", "")
        # ③ HTML中の1000円以上の最大値（ゴミ値を避けるため下限設定）
        if price_str == "0":
            all_prices = [int(p.replace(",", "")) for p in re.findall(r'([\d,]+)\s*円', r.text)
                          if int(p.replace(",", "")) >= 100]
            if all_prices:
                price_str = str(max(all_prices))


        # CDN画像をHTMLから全件抽出（不正なURLが混ざらないよう引用符や括弧を排除した厳密なパターン）
        img_urls = list(dict.fromkeys(  # 重複排除・順序保持
            re.findall(r'https://[^\s"\'{}<>()]+(?:fril\.jp|r10s\.jp)[^\s"\'{}<>()]+\.(?:jpg|jpeg|png|webp)', r.text)
        ))[:5]

        if not (title_m and img_urls):
            return None

        # HTMLから売切れ判定（meta/og情報ベース）
        if re.search(r'sold.out|売り切れ|soldout|SOLD OUT', r.text, re.I):
            print(f"    [SKIP] ラクマ売切れ商品をスキップ (HTML判定)")
            return None

        print(f"    [requests] ラクマ高速取得成功 (meta, {len(img_urls)}枚) 価格:{price_str}円")
        return {
            "title": title_m.group(1) if title_m else "不明",
            "price": price_str,
            "condition": "不明",
            "img_urls": img_urls,
            "platform": "ラクマ"
        }

    except Exception as e:
        print(f"    [requests] ラクマ取得失敗: {e}")
        return None


@retry(max_retries=2)
def scrape_item_data(url, browser_page):
    """メルカリ・メルカリShops・ラクマ詳細抽出（requests高速取得優先・ブラウザfallback付き）"""
    try:
        if not url.startswith("http"):
            return None

        # ----------------------------------------
        # ラクマの処理
        # ----------------------------------------
        is_rakuma = "fril.jp" in url
        if is_rakuma:
            result = _fetch_rakuma_via_requests(url)
            if result:
                return result

            # fallback: ブラウザDOM解析
            print(f"    [SESSION] ラクマDOM fallback: {url}")
            browser_page.get(url)
            title_ele = browser_page.ele('css:.item__name', timeout=3) or browser_page.ele('css:.product_title')
            price_ele = browser_page.ele('css:.item__price') or browser_page.ele('css:.item-price')

            condition = "不明"
            for row in browser_page.eles('tag:tr', timeout=2):
                if "商品の状態" in row.text:
                    condition = row.text.replace("商品の状態", "").strip()

            img_urls = []
            rakuma_cdn_domains = ["fril.jp", "rakuten", "r10s.jp", "thumbnail"]
            for img in browser_page.eles('tag:img', timeout=2):
                src = img.attr('src')
                # HTMLエンティティ混入・異常に長いURLを除外
                if not src or '&quot;' in src or '&amp;' in src or len(src) > 500 or '"' in src or '{' in src or '}' in src or 'asset.fril.jp' in src:
                    continue
                if any(d in src for d in rakuma_cdn_domains) and src not in img_urls:
                    img_urls.append(src)
                if len(img_urls) >= 5:
                    break

            return {
                "title": title_ele.text if title_ele else "不明",
                "price": price_ele.text if price_ele else "0",
                "condition": condition,
                "img_urls": img_urls,
                "platform": "ラクマ"
            }

        # ----------------------------------------
        # メルカリ & メルカリShopsの処理
        # ----------------------------------------
        is_shops = "/shops/product/" in url

        # requests で高速取得を試みる
        result = _fetch_mercari_via_requests(url)
        if result:
            return result

        # fallback: ブラウザDOM解析
        print(f"    [SESSION] メルカリDOM fallback: {url}")
        browser_page.get(url)

        title_ele = browser_page.ele('tag:h1', timeout=4)
        title = title_ele.text if title_ele else "不明"

        price = "0"
        price_ele = (browser_page.ele('css:[data-testid="product-price"]')
                     or browser_page.ele('tag:mer-price', timeout=2)
                     or browser_page.ele('@data-testid=price'))
        if price_ele:
            price = price_ele.attr('value') or price_ele.text
            if not price and price_ele.shadow_root:
                span_ele = price_ele.shadow_root.ele('tag:span')
                if span_ele:
                    price = span_ele.text

        if not price or price == "0":
            body_text = browser_page.ele('tag:body').text
            match = re.search(r'[¥￥]\s*([\d,]+)', body_text)
            if match:
                price = match.group(1).replace(',', '')

        # 画像URL取得（timeout短縮: 5→2秒）
        img_urls = []
        for img in browser_page.eles('tag:img', timeout=2):
            src = img.attr('src')
            if src and src.startswith("http") and src not in img_urls:
                if "mercdn.net" in src or is_shops:
                    if "icon" not in src and "logo" not in src and '"' not in src and '{' not in src and '}' not in src:
                        img_urls.append(src)
            if len(img_urls) >= 5:
                break

        cond = "不明"
        if is_shops:
            for row in browser_page.eles('css:div[class*="merDisplayRow"]'):
                if "商品の状態" in row.text:
                    cond = row.text.replace("商品の状態", "").strip()
                    break
            if cond == "不明":
                body_text = browser_page.ele('tag:body').text
                if "未使用" in body_text or "新品" in body_text:
                    cond = "新品、未使用"
        else:
            cond_loc = (browser_page.ele('@data-testid=商品の状態')
                        or browser_page.ele('tag:mer-display-row@@label=商品の状態'))
            if cond_loc:
                cond = cond_loc.text.replace("商品の状態", "").strip()

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
        browser_page.wait.ele_displayed('css:[data-testid="item-cell"]', timeout=10)
        label_eles = browser_page.eles('css:[aria-label*="円"]', timeout=5)

        items_data = []
        for ele in label_eles:
            if len(items_data) >= max_results:
                break

            label = ele.attr('aria-label') or ""
            a_tag = ele if ele.tag == 'a' else ele.parent('tag:a')
            if not a_tag:
                continue

            i_url = a_tag.attr('href')
            if not i_url:
                continue
            if not i_url.startswith("http"):
                i_url = f"https://jp.mercari.com{i_url}"

            sticker = (ele.ele('css:[data-testid="thumbnail-sticker"]', timeout=0)
                       or a_tag.ele('css:[data-testid="thumbnail-sticker"]', timeout=0))
            sticker_label = sticker.attr('aria-label').lower() if sticker else ""

            if sticker_label and "shops" not in sticker_label:
                if "売り切れ" in sticker_label or "sold" in sticker_label:
                    continue

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
    safe_keyword = keyword.replace("-", " ")
    encoded_keyword = urllib.parse.quote(safe_keyword)
    url = f"https://fril.jp/search/{encoded_keyword}"
    print(f"[*] ラクマ検索開始: {keyword} -> (内部クエリ: {safe_keyword})")

    results = []
    try:
        browser_page.get(url)
        items = browser_page.eles('css:.item-box', timeout=8)
        if not items:
            items = browser_page.eles('css:.item', timeout=3)

        for item in items:
            if len(results) >= max_results:
                break

            # 売切れ商品をスキップ（CSSクラスまたはSOLDオーバーレイで判定）
            item_html = item.html if hasattr(item, 'html') else ""
            if re.search(r'sold.?out|item-box--sold|is-sold|SOLD', item_html, re.I):
                continue

            a_tag = item.ele('tag:a')
            if not a_tag:
                continue
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
