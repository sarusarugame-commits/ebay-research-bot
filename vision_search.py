import time
import re
import json
import base64
try:
    from scrapling import Adaptor as _ScraplingAdaptor
    _SCRAPLING_OK = True
except ImportError:
    _SCRAPLING_OK = False
from config import GOOGLE_APPLICATION_CREDENTIALS

def _parse_lens_html(html, pref_domains, max_results, exclude_domains=None):
    """ScraplingのAdaptorでHTMLを解析。CSSクラス不要でhrefドメイン一致で抽出。"""
    results = []
    exclude_domains = exclude_domains or []
    page = _ScraplingAdaptor(html)
    for a in page.css('a'):
        href = a.attrib.get('href', '')
        if not href.startswith('http'): continue
        if not any(d in href for d in pref_domains): continue
        if any(d in href for d in exclude_domains): continue
        # テキストは heading > h3 > span > fallback の優先順で取得
        text = ''
        for sel in ['div[role="heading"]', 'h3', 'span']:
            el = a.css_first(sel)
            if el and el.text.strip():
                text = el.text.strip()
                break
        if not text:
            text = a.text.strip()
        img_el = a.css_first('img')
        img_url = img_el.attrib.get('src', '') if img_el else ''
        if len(text) > 5:
            results.append({'page_url': href, 'title': text, 'snippet': '', 'img_url': img_url})
        if len(results) >= max_results:
            break
    return results

def _parse_lens_tab(tab, pref_domains, max_results, exclude_domains=None):
    """タブのHTMLをScrapling優先・フォールバックで解析する共通関数"""
    exclude_domains = exclude_domains or []
    if _SCRAPLING_OK:
        try:
            html = tab.html
            results = _parse_lens_html(html, pref_domains, max_results, exclude_domains)
            if results:
                return results
            print(f"    [Lens] Scrapling: 0件。DrissionPageフォールバックへ")
        except Exception as e:
            print(f"    [Lens] Scrapling失敗({e})。DrissionPageフォールバックへ")

    # フォールバック: DrissionPage（複数セレクタ試行 + 全aタグ）
    results = []
    candidate_selectors = ['css:a.LBcIee', 'css:a.cz3goc', 'css:div.g a', 'css:div.tF2Cxc a', 'css:div.yuRUbf a']
    items = []
    for sel in candidate_selectors:
        items = tab.eles(sel, timeout=2)
        if items: break
    if not items:
        items = tab.eles('tag:a', timeout=2)

    for item in items:
        try:
            href = item.attr('href') or ''
            if not href.startswith('http'): continue
            if not any(d in href for d in pref_domains): continue
            if any(d in href for d in exclude_domains): continue
            text = ''
            for sel in ['css:div[role="heading"]', 'css:h3', 'css:span']:
                try:
                    el = item.ele(sel, timeout=0.5)
                    if el and el.text.strip():
                        text = el.text.strip(); break
                except Exception: pass
            if not text: text = item.text.strip()
            img_ele = item.ele('tag:img', timeout=0.5)
            img_url = img_ele.attr('src') if img_ele else ''
            if len(text) > 5:
                results.append({'page_url': href, 'title': text, 'snippet': '', 'img_url': img_url})
        except Exception: continue
        if len(results) >= max_results: break
    return results

def search_by_google_lens(image_url, browser, max_results=5):
    """【国内検索用】メインから渡されたブラウザを使用して Google Lens を実行する"""
    print(f"[*] Google Lens (ブラウザ版) で検索を開始します...", flush=True)
    results = []
    try:
        tab = browser.new_tab(f"https://www.google.com/searchbyimage?image_url={image_url}&client=app&lr=lang_ja&hl=ja")
        tab.wait.load_start()
        tab.wait(2)
        tab.scroll.to_bottom()
        tab.wait(2)

        pref_domains = ["mercari.com", "rakuten.co.jp", "yahoo.co.jp", "shopping.yahoo.co.jp", "paypayfleamarket.yahoo.co.jp", "fril.jp", "amazon.co.jp"]
        results = _parse_lens_tab(tab, pref_domains, max_results)
        
        tab.close()
        print(f" -> Google Lens で国内サイトを {len(results)} 件抽出しました。", flush=True)
    except Exception as e:
        print(f"[!] Google Lens 失敗: {e}", flush=True)
    return results

def find_similar_images_on_web(image_uri, browser, max_results=5, force_lens=False):
    """【国内用】APIをメインに使い、足りない分をLensで補填する"""
    results = []
    
    # 0. 強制 Lens モード
    if force_lens:
        print("[*] Google Lens を強制実行します...", flush=True)
        return search_by_google_lens(image_uri, browser, max_results=max_results)

    # 1. Vision API 試行
    if GOOGLE_APPLICATION_CREDENTIALS and os.path.exists(GOOGLE_APPLICATION_CREDENTIALS):
        print(f"[*] Vision API (Web Detection) で国内の類似ページを検索中...", flush=True)
        try:
            from google.cloud import vision
            client = vision.ImageAnnotatorClient()
            image = vision.Image()
            image.source.image_uri = image_uri
            
            feature = vision.Feature(type_=vision.Feature.Type.WEB_DETECTION, max_results=400)
            request = vision.AnnotateImageRequest(image=image, features=[feature])
            response = client.batch_annotate_images(requests=[request]).responses[0]
            
            if response.web_detection:
                pref_domains = [
                    "mercari.com", "rakuten.co.jp", "yahoo.co.jp", 
                    "shopping.yahoo.co.jp", "paypayfleamarket.yahoo.co.jp", 
                    "fril.jp", "borderless.store", "amazon.co.jp",
                    "shopping.geocities.jp", "paypaymall.yahoo.co.jp",
                    "buyee.jp", "item.rakuten.co.jp", "store.shopping.yahoo.co.jp",
                    "wowma.jp", "ponparemall.com", "yodobashi.com", "biccamera.com",
                    "kakaku.com", "suruga-ya.jp", "mandarake.co.jp", "mbok.jp",
                    "bookoffonline.co.jp", "netoff.co.jp", "hardoff.co.jp",
                    "lashinbang.com", "animate-onlineshop.jp", "amiami.jp",
                    "ec.line.me", "d-shopping.docomo.ne.jp", "qoo10.jp", "zara.com/jp"
                ]
                for page in response.web_detection.pages_with_matching_images:
                    url = page.url
                    title = page.page_title
                    is_pref_domain = any(domain in url for domain in pref_domains)
                    if is_pref_domain:
                        results.append({"page_url": url, "title": title, "snippet": "", "img_url": ""})
                    
                    if len(results) >= max_results:
                        break
            
            print(f" -> Vision API で {len(results)} 件の国内候補を抽出しました。", flush=True)
        except Exception as e:
            print(f"[!] Vision API 実行エラー: {e}", flush=True)
    else:
        print("[*] Vision APIの認証情報がないため、Lensでの検索を実行します...", flush=True)

    # 2. Vision APIの取得件数が max_results に満たない場合、Lensで補填する
    if len(results) < max_results:
        needed = max_results - len(results)
        print(f"[*] 国内候補が {max_results} 件に満たないため、Google Lens (ブラウザ版) で残り {needed} 件を補填検索します...", flush=True)
        
        try:
            # ブラウザ側でも max_results をきっちり守らせる
            lens_res = search_by_google_lens(image_uri, browser, max_results=max_results)
            existing_urls = {r["page_url"] for r in results}
            
            added = 0
            for lr in lens_res:
                if lr["page_url"] not in existing_urls:
                    results.append(lr)
                    existing_urls.add(lr["page_url"])
                    added += 1
                if len(results) >= max_results: break
            
            # 念のため最後に再度スライスして、不整合（6件など）を防ぐ
            results = results[:max_results]
            print(f" -> Lens補填完了。新たに {added} 件を追加し、合計 {len(results)} 件の国内候補を確保しました！", flush=True)
        except Exception as e:
            print(f"[!] Lens補填検索 失敗: {e}", flush=True)

    return results[:max_results] # 念のためスライス

def search_global_images_by_lens(image_uri, browser, max_results=5):
    """【海外用】Vision APIをメインに使い、足りない分をLensで補填する"""
    results = []
    
    # 1. Vision API 試行
    if GOOGLE_APPLICATION_CREDENTIALS and os.path.exists(GOOGLE_APPLICATION_CREDENTIALS):
        print(f"[*] Vision API (Web Detection) で海外の類似ページを検索中...", flush=True)
        try:
            from google.cloud import vision
            client = vision.ImageAnnotatorClient()
            image = vision.Image()
            image.source.image_uri = image_uri
            
            feature = vision.Feature(type_=vision.Feature.Type.WEB_DETECTION, max_results=400)
            request = vision.AnnotateImageRequest(image=image, features=[feature])
            response = client.batch_annotate_images(requests=[request]).responses[0]
            
            if response.web_detection and response.web_detection.pages_with_matching_images:
                # 弾きたい日本のドメイン
                jp_domains = ["mercari.com", "rakuten.co.jp", "yahoo.co.jp", "fril.jp", "amazon.co.jp", ".co.jp", ".jp"]
                
                for page in response.web_detection.pages_with_matching_images:
                    url = page.url
                    title = page.page_title
                    if any(domain in url for domain in jp_domains):
                        continue
                        
                    if len(title) > 5:
                        results.append({"page_url": url, "title": title, "snippet": "", "img_url": ""})
                    
                    if len(results) >= max_results:
                        break
                        
            print(f" -> Vision API で {len(results)} 件の海外サイト候補を抽出しました。", flush=True)
        except Exception as e:
            print(f"[!] Vision API 実行エラー: {e}", flush=True)
    else:
        print("[*] Vision APIの認証情報がないためAPI検索をスキップします...", flush=True)

    # 2. 補填 (Google Lens Global版) - Vision APIで5件取れたらスキップ
    if len(results) < max_results and len(results) == 0:  # Vision API完全失敗時のみ補填
        needed = max_results - len(results)
        print(f"[*] 海外候補が {max_results} 件に満たないため、Google Lens (Global版) で残り {needed} 件を補填検索します...", flush=True)
        
        try:
            tab = browser.new_tab(f"https://www.google.com/searchbyimage?image_url={image_uri}&client=app&hl=en")
            tab.wait.load_start()
            tab.wait(1)
            tab.scroll.to_bottom()
            tab.wait(1)
            
            jp_domains = ["mercari.com", "rakuten.co.jp", "yahoo.co.jp", "fril.jp", "amazon.co.jp", ".jp/"]
            results = _parse_lens_tab(tab, pref_domains=[], max_results=max_results, exclude_domains=jp_domains)
            
            tab.close()
            print(f" -> Lens補填完了。合計 {len(results)} 件の海外候補を確保しました！", flush=True)
        except Exception as e:
            print(f"[!] Lens補填検索 失敗: {e}", flush=True)
            
    return results[:max_results] # 念のためスライス
