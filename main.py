import gpu_utils
import sys
import io
import re
import datetime
import traceback
import functools
import os
import requests

def global_exception_handler(exctype, value, tb):
    print("\n" + "="*50)
    print("!!! UNCAUGHT EXCEPTION !!!")
    traceback.print_exception(exctype, value, tb)
    print("="*50 + "\n")

sys.excepthook = global_exception_handler

# Windows環境でのエンコードエラー（CP932）対策
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
print = functools.partial(print, flush=True)

from config import EBAY_APP_ID, GOOGLE_APPLICATION_CREDENTIALS
import database
from mercari_scraper import search_mercari, search_rakuma, scrape_item_data
from surugaya_scraper import search_surugaya, scrape_surugaya_item
from amazon_scraper import search_amazon, scrape_amazon_specs
from llm_vision_judge import estimate_weight_with_llm, analyze_item_safety_and_tariff, verify_specs_with_llm
from clip_judge import judge_similarity
# verify_with_lightglue は clip_judge 内で使われるようになったよ！
from ebay_scraper import scrape_ebay_newest_items, scrape_ebay_item_specs, get_browser_page
from vision_search import find_similar_images_on_web
from llm_namer import extract_product_name

def extract_specs_from_text(text):
    w, d = "不明", "不明"
    if not text: return w, d
    w_m = re.search(r"(\d+(\.\d+)?)\s?(kg|g|キロ|グラム)", text, re.I)
    if w_m: w = w_m.group(0)
    d_m = re.search(r"(\d+(\.\d+)?)\s?[x*×]\s?(\d+(\.\d+)?)\s?[x*×]\s?(\d+(\.\d+)?)\s?(cm|mm|センチ|インチ|in)?", text, re.I)
    if d_m: d = d_m.group(0)
    return w, d

def adjust_dimensions(dims_str):
    if dims_str == "不明": return "不明"
    # 梱包バッファ: 縦+20mm, 横+20mm, 高+10mm
    # 一桁目は切り捨て
    nums = re.findall(r"(\d+(\.\d+)?)", dims_str)
    if len(nums) >= 3:
        d_vals = [float(nums[i][0]) for i in range(3)]
        unit = "mm" if "mm" in dims_str.lower() else "cm"
        
        # 単位を統一して計算 (基本はcmで来ることを想定しつつ)
        if unit == "cm":
            # cm -> mm, add buffer, round down last digit, mm -> cm
            d1 = (d_vals[0] * 10) + 20
            d2 = (d_vals[1] * 10) + 20
            d3 = (d_vals[2] * 10) + 10
            d1, d2, d3 = [int(d // 10 * 10) for d in [d1, d2, d3]]
            return f"{d1/10}x{d2/10}x{d3/10} cm"
        else:
            # mm
            d1 = d_vals[0] + 20
            d2 = d_vals[1] + 20
            d3 = d_vals[2] + 10
            d1, d2, d3 = [int(d // 10 * 10) for d in [d1, d2, d3]]
            return f"{d1}x{d2}x{d3} mm"
    return dims_str

def truncate_weight(weight_str):
    if weight_str == "不明": return "不明"
    # 梱包バッファ: +100g
    # 一桁目は切り捨て
    nums = re.findall(r"(\d+(\.\d+)?)", weight_str)
    if nums:
        val = float(nums[0][0])
        unit = "kg" if "kg" in weight_str.lower() or "キロ" in weight_str else "g"
        
        if unit == "kg":
            gram_val = (val * 1000) + 100
            gram_val = int(gram_val // 10 * 10)
            return f"{gram_val/1000}kg"
        else:
            gram_val = val + 100
            gram_val = int(gram_val // 10 * 10)
            return f"{gram_val}g"
    return weight_str

def save_debug_image(url, folder, filename):
    """画像をダウンロードして保存する（デバッグ用）"""
    try:
        if not os.path.exists(folder):
            os.makedirs(folder)
        # ファイル名のサニタイズ (改行やWindows禁止文字を除去)
        clean_filename = re.sub(r'[\r\n\t]', '', filename)
        clean_filename = re.sub(r'[\\/:*?"<>|]', '_', clean_filename)
        
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            with open(os.path.join(folder, clean_filename), 'wb') as f:
                f.write(r.content)
    except Exception as e:
        print(f"    [DEBUG_IMG_ERROR] {e}")

def main():
    print("\n" + "="*50)
    print("   eBay/国内5大ECプラットフォーム 横断リサーチツール")
    print("="*50)
    
    url = input("eBayの検索結果URLを入力してください:\n> ").strip()
    if not url: return

    print("\n[*] ブラウザの起動を確認中...")
    browser = get_browser_page()
    if not browser:
        print("[!] ブラウザの取得に失敗したため終了します。")
        return
    print("[*] ブラウザ準備完了。")

    try:
        # 1. eBayスクレイピング
        print("\n[*] eBayから新着商品をスクレイピング中...")
        items = scrape_ebay_newest_items(url, browser)
        if not items: 
            print("[!] eBay商品が見つかりませんでした。")
            return
            
        items.sort(key=lambda x: x.get('timestamp', datetime.datetime.min), reverse=False)
        target_item = items[0] if items else None
        
        if not target_item:
            print("\n[OK] 指定されたURL内の全商品はすでにリサーチ済みです。")
            return
            
        item_id = target_item.get('id')
        print(f"\n" + "-"*50)
        print(f" 【リサーチ開始】 eBay商品 ID: {item_id}")
        print(f" タイトル: {target_item.get('title')}")
        print(f" 画像URL: {target_item.get('image_url')}")
        print("-" * 50)
        
        # 1.5 安全性・関税チェック (Gemma 3 Vision)
        safety_data = analyze_item_safety_and_tariff(target_item.get('image_url'))
        if safety_data.get("is_alcohol"):
            print(f"\n[⚠️ SKIP] アルコール飲料が検出されました。ガイドラインによりこの商品はスキップします。")
            database.mark_as_researched(item_id, weight="SKIPPED", dimensions="ALCOHOL")
            return

        high_tariff_flag = safety_data.get("is_high_tariff", False)
        material_label = safety_data.get("label", "なし")
        if high_tariff_flag:
            print(f"\n[⚠️ ATTENTION] 高関税対象素材（{material_label}）の可能性があります。")
        
        # スペック収集
        ebay_specs = scrape_ebay_item_specs(item_id, browser)
        raw_w, raw_d = ebay_specs.get("weight", "不明"), ebay_specs.get("dimensions", "不明")
        img_url = target_item.get("image_url")

        # 2. 画像検索
        print("\n[*] Google Vision API / Lens を使用して類似画像を検索中...")
        candidate_pages = find_similar_images_on_web(img_url, browser, max_results=5)
        
        scored_candidates = []
        if candidate_pages:
            jp_pattern = re.compile(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]')
            jp_candidates = []
            for p in candidate_pages:
                text = (p.get('title', '') + ' ' + p.get('snippet', '')).strip()
                if jp_pattern.search(text):
                    jp_candidates.append(p)
            
            if jp_candidates:
                with_img = [c for c in jp_candidates if c.get('img_url')]
                without_img = [c for c in jp_candidates if not c.get('img_url')]
                scored_candidates = []
                if with_img:
                    scored_candidates = judge_similarity(img_url, with_img)
                scored_candidates.extend(without_img)
            else:
                print("[!] 国内ドメインが見つかりませんでした。Google Lens で再試行...")
                lens_pages = find_similar_images_on_web(img_url, browser, max_results=5, force_lens=True)
                for p in lens_pages:
                    text = (p.get('title', '') + ' ' + p.get('snippet', '')).strip()
                    if jp_pattern.search(text):
                        jp_candidates.append(p)
                if jp_candidates:
                    with_img = [c for c in jp_candidates if c.get('img_url')]
                    without_img = [c for c in jp_candidates if not c.get('img_url')]
                    judged_lens = []
                    if with_img:
                        judged_lens = judge_similarity(img_url, with_img)
                    scored_candidates = judged_lens + without_img

            if raw_d == "不明":
                for p in jp_candidates:
                    _, d = extract_specs_from_text(p.get("title", "") + " " + p.get("snippet", ""))
                    if d != "不明": raw_d = d; break

        # 3. 商品名特定
        print("\n[*] AI を使用して日本語正式商品名を特定中...")
        name_data = extract_product_name(target_item.get('title'), scored_candidates)
        final_name = name_data.get("full_name", "特定不能")
        print(f" -> 最終確定した日本語名: {final_name}")

        # デバッグ画像用フォルダの作成
        debug_folder = f"debug_images/{item_id}_{datetime.datetime.now().strftime('%H%M%S')}"
        save_debug_image(img_url, debug_folder, "00_ebay_reference.jpg")

        # 4. 国内横断検索 (2段階フェーズ)
        final_candidates = []
        weight_final = "不明"
        dims_final = "不明"
        tentative_best_item = None
        tentative_best_price = float('inf')

        if final_name and final_name != "特定不能":
            brand_model = f"{name_data.get('brand', '')} {name_data.get('model', '')}".strip()
            search_query = brand_model if len(brand_model) > 5 else final_name
            import unicodedata
            def normalize_text(text):
                return unicodedata.normalize('NFKC', text).lower()
            filter_text = f"{name_data.get('series', '')} {name_data.get('model', '')} {name_data.get('keywords', '')}".strip()
            search_keywords = [normalize_text(k) for k in re.split(r'[\s　]+', filter_text) if len(k) > 1]
            model_name = normalize_text(name_data.get('model', ''))

            def process_candidates(candidates, platform):
                nonlocal tentative_best_price, tentative_best_item, weight_final, dims_final
                if not candidates: return
                threshold = max(1, len(search_keywords) // 2)
                keyword_filtered = []
                for item in candidates:
                    title_norm = normalize_text(item.get("title", ""))
                    num_matches = sum(1 for k in search_keywords if k in title_norm)
                    has_model = model_name in title_norm if len(model_name) > 3 else False
                    if has_model or num_matches >= threshold:
                        keyword_filtered.append(item)
                if not keyword_filtered: keyword_filtered = candidates
                browser_page = get_browser_page()
                for item in keyword_filtered:
                    item_title = item.get('title', '不明')
                    page_url = item.get('page_url', '')
                    if not page_url or not page_url.startswith("http"): continue
                    detail = None
                    if platform in ["メルカリ", "ラクマ"]:
                        detail = scrape_item_data(page_url, browser_page)
                    elif platform == "駿河屋":
                        detail = scrape_surugaya_item(page_url, browser_page)
                    if detail: item.update(detail)
                    current_img_urls = item.get("img_urls", []) or ([item["img_url"]] if item.get("img_url") else [])
                    if not current_img_urls: continue
                    img_candidates = [{"img_url": u} for u in current_img_urls]
                    judged_imgs = judge_similarity(img_url, img_candidates)
                    if not judged_imgs: continue
                    best_ji = judged_imgs[0]
                    final_score = float(best_ji.get("score", 0))
                    if final_score < 70: continue
                    detail_price_str = str(item.get('price', '0'))
                    p_str = re.sub(r'[^\d]', '', detail_price_str)
                    price_val = int(p_str) if p_str else 0
                    if price_val <= 0: continue
                    item["price_int"] = price_val
                    ship_fee = item.get("actual_shipping_fee", 0)
                    total_price = item["price_int"] + ship_fee
                    item["total_price"] = total_price
                    if total_price < tentative_best_price:
                        tentative_best_price = total_price
                        tentative_best_item = item
                    if weight_final == "不明" or dims_final == "不明":
                        desc_text = item.get("description", "") + " " + item.get("title", "")
                        ext_w, ext_d = extract_specs_from_text(desc_text)
                        if weight_final == "不明" and ext_w != "不明": weight_final = ext_w
                        if dims_final == "不明" and ext_d != "不明": dims_final = ext_d
                    final_candidates.append(item)

            def get_fresh_browser():
                nonlocal browser
                try:
                    if not browser: browser = get_browser_page()
                    else: _ = browser.latest_tab
                except Exception: browser = get_browser_page()
                return browser

            browser = get_fresh_browser()
            m_res = search_mercari(search_query, browser, max_results=15)
            process_candidates(m_res, "メルカリ")
            browser = get_fresh_browser()
            r_res = search_rakuma(search_query, browser, max_results=10)
            process_candidates(r_res, "ラクマ")
            browser = get_fresh_browser()
            s_res = search_surugaya(search_query, browser, max_results=10)
            process_candidates(s_res, "駿河屋")
            from shopping_api import search_rakuten
            process_candidates(search_rakuten(search_query), "楽天市場")
            from shopping_api import search_yahoo
            process_candidates(search_yahoo(search_query), "Yahooショッピング")

            # --- Amazon Spec Extraction Phase ---
            print(f"\n[*] Amazon.jp から詳細スペック（サイズ・重量）の取得を試行中...")
            amazon_candidates = search_amazon(search_query, browser, max_results=5)
            if amazon_candidates:
                amazon_judged = judge_similarity(img_url, amazon_candidates)
                for a_item in amazon_judged:
                    a_score = float(a_item.get('score', 0))
                    if a_score >= 70:
                        a_specs = scrape_amazon_specs(a_item['page_url'], browser)
                        if a_specs["weight"] != "不明":
                            weight_final = a_specs["weight"]
                        if a_specs["dimensions"] != "不明":
                            dims_final = a_specs["dimensions"]
                        if weight_final != "不明" and dims_final != "不明": break
            else:
                print("    [!] Amazonで商品が見つかりませんでした。スペックは現状のまま（不明）維持します。")

        if weight_final == "不明" and raw_w != "不明": weight_final = raw_w
        if dims_final == "不明" and raw_d != "不明": dims_final = raw_d
        
        # 数値の調整（バッファ加算と切り捨て）
        weight_final = truncate_weight(weight_final)
        dims_final = adjust_dimensions(dims_final)

        # --- Final Spec Verification Phase (LLM) ---
        print("\n[*] Gemma 3 が画像から最終的なスペック（梱包込）を検証・調整中...")
        is_weight_unknown = (weight_final == "不明")
        is_dims_unknown = (dims_final == "不明")
        
        weight_v, dims_v = verify_specs_with_llm(img_url, weight_final, dims_final)
        
        # 不明フラグの処理
        if is_weight_unknown: weight_v += " (不明)"
        if is_dims_unknown: dims_v += " (不明)"
        
        weight_final = weight_v
        dims_final = dims_v

        best_item = tentative_best_item
        if best_item:
            database.mark_as_researched(
                item_id, 
                platform=best_item.get("platform"), 
                title=best_item.get("title"), 
                price=best_item.get("total_price"), 
                condition=best_item.get("condition"), 
                url=best_item.get("page_url"), 
                weight=weight_final, 
                dimensions=dims_final
            )
        else:
            database.mark_as_researched(item_id, weight=weight_final, dimensions=dims_final)

        print("\n" + "="*60)
        print("                 ✨ リサーチ完了 ✨")
        print("="*60)
        print(f"■ eBay商品 ID  : {item_id}")
        print(f"■ 判定した商品名: {final_name}")
        print("-" * 60)
        if best_item:
            print(f"【！国内最安値確定商品！】")
            print(f"■ プラットフォーム: {best_item.get('platform')}")
            print(f"■ 送料込み価格    : ¥{best_item.get('total_price', 0):,}")
            print(f"■ 商品URL         : {best_item.get('page_url')}")
        else:
            print(" domestic lowest price NOT found.")
        print("-" * 60)
        print(f"■ 推定重量        : {weight_final}")
        print(f"■ 推定サイズ      : {dims_final}")
        print("="*60)

    except Exception as e:
        print(f"\n[！重大なエラーが発生しました！]")
        print(f"エラー内容: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
