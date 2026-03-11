import os
import requests
import time
import re
from config import GOOGLE_APPLICATION_CREDENTIALS

def search_by_google_lens(image_url, browser, max_results=5):
    """【国内検索用】メインから渡されたブラウザを使用して Google Lens を実行する"""
    print(f"[*] Google Lens (ブラウザ版) で検索を開始します...", flush=True)
    results = []
    try:
        # 日本語結果を優先させるため lr=lang_ja と hl=ja を付与
        tab = browser.new_tab(f"https://www.google.com/searchbyimage?image_url={image_url}&client=app&lr=lang_ja&hl=ja")
        tab.wait.load_start()
        tab.wait(2)
        tab.scroll.to_bottom()
        tab.wait(2)

        pref_domains = ["mercari.com", "rakuten.co.jp", "yahoo.co.jp", "shopping.yahoo.co.jp", "paypayfleamarket.yahoo.co.jp", "fril.jp", "amazon.co.jp"]

        # Googleは頻繁にクラス名を変えるため複数セレクタを試す
        candidate_selectors = [
            'css:a.LBcIee',
            'css:a.cz3goc',
            'css:div.g a',
            'css:div.tF2Cxc a',
            'css:div.yuRUbf a',
        ]
        items = []
        for sel in candidate_selectors:
            items = tab.eles(sel, timeout=2)
            if items:
                print(f"    [Lens] セレクタ '{sel}' で {len(items)} 件取得", flush=True)
                break
        if not items:
            print(f"    [Lens] セレクタ未ヒット。全aタグからフィルタリングします", flush=True)
            items = tab.eles('tag:a', timeout=2)

        for item in items:
            href = item.attr('href')
            if not href or not href.startswith('http'): continue
            if any(domain in href for domain in pref_domains):
                title_ele = item.ele('css:div[role="heading"]', timeout=1)
                text = title_ele.text.strip() if title_ele else item.text.strip()
                img_ele = item.ele('tag:img', timeout=1)
                img_url = img_ele.attr('src') if img_ele else ""
                if len(text) > 5:
                    results.append({"page_url": href, "title": text, "snippet": "", "img_url": img_url})
            if len(results) >= max_results: break
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
            items = tab.eles('css:a.LBcIee')
            
            existing_urls = {r["page_url"] for r in results}
            added_by_lens = 0
            for item in items:
                href = item.attr('href')
                if not href or href in existing_urls: continue
                if any(domain in href for domain in jp_domains): continue
                    
                title_ele = item.ele('css:div[role="heading"]', timeout=1)
                text = title_ele.text.strip() if title_ele else item.text.strip()
                img_ele = item.ele('tag:img', timeout=1)
                img_url_cand = img_ele.attr('src') if img_ele else ""
                
                if len(text) > 5 and img_url_cand:
                    results.append({"page_url": href, "title": text, "snippet": "", "img_url": img_url_cand})
                    existing_urls.add(href)
                    added_by_lens += 1
                if len(results) >= max_results: break
                
            tab.close()
            # 念のため最後に再度スライス
            results = results[:max_results]
            print(f" -> Lens補填完了。新たに {added_by_lens} 件を追加し、合計 {len(results)} 件の海外候補を確保しました！", flush=True)
        except Exception as e:
            print(f"[!] Lens補填検索 失敗: {e}", flush=True)
            
    return results[:max_results] # 念のためスライス
