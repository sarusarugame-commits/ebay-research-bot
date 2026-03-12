import gpu_utils
import sys
import io
import re
import datetime
import os as _os
# WindowsでANSIエスケープを有効化
if sys.platform == "win32":
    _os.system("")
BLUE  = "\033[94m"
RESET = "\033[0m"

def hyperlink(url, text=None):
    \"\"\"OSC 8ハイパーリンク。Windows Terminal / VSCode対応。非対応端末はURLをそのまま表示。\"\"\"
    raw = text if text else url
    label = raw[:30] + "…" if len(raw) > 30 else raw
    return f"\033]8;;{url}\033\\{BLUE}{label}{RESET}\033]8;;\033\\"

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

from config import EBAY_APP_ID, GOOGLE_APPLICATION_CREDENTIALS, RAKUTEN_APPLICATION_ID, RAKUTEN_ACCESS_KEY, RAKUTEN_AFFILIATE_ID, YAHOO_CLIENT_ID
import database
from ebay_api import get_item_details
from mercari_scraper import search_mercari, search_rakuma, scrape_item_data
from surugaya_scraper import search_surugaya, scrape_surugaya_item
from llm_vision_judge import estimate_weight_with_llm, analyze_item_safety_and_tariff
from clip_judge_client import judge_similarity
# verify_with_lightglue は clip_judge 内で使われるようになったよ！
from ebay_scraper import scrape_ebay_newest_items, scrape_ebay_item_specs, get_browser_page
from vision_search import find_similar_images_on_web
from llm_namer import extract_product_name
from shopping_api import search_rakuten, search_yahoo, scrape_yahoo_item
from amazon_scraper import search_amazon, search_amazon_via_google, scrape_amazon_specs
from llm_vision_judge import verify_model_match
from sheets_writer import write_to_sheet
GREEN = "\033[92m"
RESET_GREEN = "\033[0m"

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
    is_mm = "mm" in dims_str.lower()
    nums = re.findall(r"(\d+(\.\d+)?)", dims_str)
    if len(nums) >= 3:
        d1, d2, d3 = float(nums[0][0]), float(nums[1][0]), float(nums[2][0])
        # mmに統一
        if not is_mm:
            d1 *= 10; d2 *= 10; d3 *= 10
        # バッファ追加 (+20, +20, +10)
        d1, d2, d3 = int(d1 + 20), int(d2 + 20), int(d3 + 10)
        return f"{d1}x{d2}x{d3}mm"
    return dims_str

def truncate_weight(weight_str):
    if weight_str == "不明": return "不明"
    is_kg = "kg" in weight_str.lower() or "キロ" in weight_str
    nums = re.findall(r"(\d+(\.\d+)?)", weight_str)
    if nums:
        val = float(nums[0][0])
        # gに統一
        if is_kg:
            val *= 1000
        # バッファ追加 (+100g)
        val = int(val + 100)
        return f"{val}g"
    return weight_str

def save_debug_image(url, folder, filename):
    \"\"\"画像をダウンロードして保存する（デバッグ用）\"\"\"
    try:
        if not os.path.exists(folder):
            os.makedirs(folder)
        # ファイル名のサニタイズ (改行やWindows禁止文字を除去)
        clean_filename = re.sub(r'[\r\n\t]', '', filename)
        clean_filename = re.sub(r'[\\/:*?\"<>|]', '_', clean_filename)
        
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            with open(os.path.join(folder, clean_filename), 'wb') as f:
                f.write(r.content)
    except Exception as e:
        print(f"    [DEBUG_IMG_ERROR] {e}")

def print_token_stats():
    \"\"\"本日・月間・累計のトークン使用量と円換算コストを表示する\"\"\"
    stats = database.get_token_usage_stats()
    
    # 料金設定 ($/1M tokens)
    IN_PRICE = 0.25
    OUT_PRICE = 1.50
    EXCHANGE_RATE = 150.0 # 円/USD
    
    def calc_yen(it, ot, tt):
        # 思考トークン(tt)は出力(ot)の一部として課金
        total_out = ot + tt
        usd = (it / 1000000 * IN_PRICE) + (total_out / 1000000 * OUT_PRICE)
        return usd * EXCHANGE_RATE

    print(f\"\n{BLUE}{'='*60}{RESET}\")
    print(f\"{BLUE}            📊 LLM トークン使用統計 (推計コスト){RESET}\")
    print(f\"{BLUE}{'='*60}{RESET}\")
    
    labels = [
        (\"今回 (Session)\", 'session'),
        (\"今日 (Today)\", 'today'),
        (\"今月 (M-T-D)\", 'month'),
        (\"全期間 (Total)\", 'total')
    ]
    
    for label, key in labels:
        it, ot, tt = stats[key]
        it = it or 0
        ot = ot or 0
        tt = tt or 0
        cost = calc_yen(it, ot, tt)
        color = GREEN if key in ('session', 'today') else BLUE
        think_str = f\" (うち思考:{tt:,})\" if tt > 0 else \"\"
        print(f\"{BLUE}■ {label:14}:{RESET} {color}¥{cost:,.1f}{RESET} (In:{it:,} / Out:{ot:,}{think_str})\")
    
    print(f\"{BLUE}{'='*60}{RESET}\n\")
    
def show_notification(title, body):
    \"\"\"Windows トースト通知を表示する\"\"\"
    try:
        import subprocess
        subprocess.Popen([
            \"powershell\", \"-WindowStyle\", \"Hidden\", \"-Command\",
            f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;'
            f'$t = [Windows.UI.Notifications.ToastTemplateType]::ToastText02;'
            f'$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($t);'
            f'$x.GetElementsByTagName(\"text\")[0].AppendChild($x.CreateTextNode(\"{title}\")) | Out-Null;'
            f'$x.GetElementsByTagName(\"text\")[1].AppendChild($x.CreateTextNode(\"{body}\")) | Out-Null;'
            f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier(\"eBay Research\").Show('
            f'[Windows.UI.Notifications.ToastNotification]::new($x));'
        ], creationflags=0x08000000)
    except Exception:
        pass

def execute_research_session(url, browser):
    \"\"\"1回分のリサーチ処理を実行する。正常終了またはスキップ時にTrue、エラー時にFalse（の一部）を返す設計。\"\"\"
    _start_time = datetime.datetime.now()
    try:
        # 1. eBayスクレイピング
        print(\"\n[*] eBayから新着商品をスクレイピング中...\")
        items = scrape_ebay_newest_items(url, browser)
        if not items: 
            print(\"[!] eBay商品が見つかりませんでした。\")
            return

        items.sort(key=lambda x: x.get('timestamp', datetime.datetime.min), reverse=False)
        target_item = None
        for item in items:
            item_id_check = item.get('id')
            print(f\"    [DB_CHECK] ID={item_id_check} -> is_researched={database.is_researched(item_id_check)}\", flush=True)
            if database.is_researched(item_id_check):
                print(f\"    [SKIP] 調査済み: {item_id_check} {item.get('title','')[:40]}\")
            else:
                target_item = item
                break
        
        if not target_item:
            print(\"\n[OK] 指定されたURL内の全商品はすでにリサーチ済みです。\")
            return
            
        item_id = target_item.get('id')
        print(f\"\n\" + \"-\"*50)
        print(f\" 【リサーチ開始】 eBay商品 ID: {item_id}\")
        print(f\" タイトル: {target_item.get('title')}\")
        print(f\" 画像URL: {target_item.get('image_url')}\")
        print(\"-\" * 50)
        
        # 1.5 安全性・関税チェック (Gemma 3 Vision) - 複数画像対応
        ebay_specs_pre = scrape_ebay_item_specs(item_id, browser)
        all_img_urls = ebay_specs_pre.get(\"img_urls\") or []
        if not all_img_urls:
            all_img_urls = [target_item.get('image_url')]
        safety_data = analyze_item_safety_and_tariff(all_img_urls[0], all_img_urls)
        
        # 判定結果の表示
        print(f\"    - アルコール判定: {'あり (⚠️ SKIP)' if safety_data.get('is_alcohol') else 'なし'}\")
        print(f\"    - 高関税素材判定: {'あり (⚠️ ATTENTION)' if safety_data.get('is_high_tariff') else 'なし'}\")
        if safety_data.get('label'):
            print(f\"    - 検出された素材: {safety_data.get('label')}\")

        if safety_data.get(\"is_alcohol\"):
            print(f\"\n[⚠️ SKIP] アルコール飲料が検出されました。ガイドラインによりこの商品はスキップします。\")
            database.mark_as_researched(item_id, weight=\"SKIPPED\", dimensions=\"ALCOHOL\")
            return

        high_tariff_flag = safety_data.get(\"is_high_tariff\", False)
        material_label = safety_data.get(\"label\", \"なし\")
        if high_tariff_flag:
            print(f\"\n[⚠️ ATTENTION] 高関税対象素材（{material_label}）の可能性があります。\")
        
        # スペック収集（安全性チェック時に取得済みのものを再利用）
        ebay_specs = ebay_specs_pre
        
        raw_w, raw_d = ebay_specs.get(\"weight\", \"不明\"), ebay_specs.get(\"dimensions\", \"不明\")
        # Geminiが選んだ最良画像を使用（なければ先頭画像）
        img_url = safety_data.get(\"best_img_url\") or all_img_urls[0] if all_img_urls else target_item.get(\"image_url\")

        # 2. 画像検索
        print(\"\n[*] Google Vision API / Lens を使用して類似画像を検索中...\")
        candidate_pages = find_similar_images_on_web(img_url, browser, max_results=5)
        
        scored_candidates = []
        if candidate_pages:
            # Vision API / Lens でドメインフィルター済みなので、そのまま使う
            scored_candidates = candidate_pages[:5]

            if not scored_candidates:
                print(\"    [!] 国内の候補が全く見つかりませんでした。\")

            # 寸法（dimensions）が不明な場合はテキストから推測する処理
            if raw_d == \"不明\":
                for p in scored_candidates:
                    _, d = extract_specs_from_text(p.get(\"title\", \"\") + \" \" + p.get(\"snippet\", \"\"))
                    if d != \"不明\": raw_d = d; break



        # 3. 商品名特定
        print(\"\n[*] AI を使用して日本語正式商品名を特定中...\")
        name_data = extract_product_name(target_item.get('title'), scored_candidates, img_url=img_url)

        final_name = name_data.get(\"full_name\", \"特定不能\")
        print(f\" -> 最終確定した日本語名: {final_name}\")

        # ==========================================
        # 英語商品名・eBay検索クエリの特定
        print(\"\n[*] LLMでeBay検索クエリ（型番）を生成中...\")
        from llm_namer import extract_ebay_search_query
        en_query_data = extract_ebay_search_query(target_item.get('title'))
        final_en_name = en_query_data.get(\"full_name\", target_item.get('title'))
        print(f\" -> 最終確定した英語名: {final_en_name}\")

        # デバッグ画像用フォルダの作成
        debug_folder = f\"debug_images/{item_id}_{datetime.datetime.now().strftime('%H%M%S')}\"
        save_debug_image(img_url, debug_folder, \"00_ebay_reference.jpg\")

        # 4. 国内横断検索 (2段階フェーズ)
        all_domestic_candidates = []
        weight_final = \"不明\"
        dims_final = \"不明\"
        origin_final = \"不明\"
        tentative_best_item = None
        tentative_best_price = float('inf')
        domestic_thresholds = {}

        if final_name and final_name != \"特定不能\":
            # 検索クエリ = LLMが確定した日本語名をそのまま使う
            search_query = final_name

            # フィルタリング用キーワード
            import unicodedata
            def normalize_text(text):
                return unicodedata.normalize('NFKC', text).lower()

            model_name = normalize_text(name_data.get('model', ''))
            series_name = normalize_text(name_data.get('series', ''))

            def collect_candidates(candidates, platform):
                if not candidates:
                    print(f\"[*] {platform}: 検索結果 0 件でした。\")
                    return []

                print(f\"[*] {platform}: {len(candidates)} 件の候補を収集中...\")
                
                _EXCLUDE_WORDS = [\"レンタル\", \"rental\", \"パーツ\", \"修理\", \"ジャンク\", \"部品取り\"]
                keyword_filtered = []
                for item in candidates:
                    title_norm = normalize_text(item.get(\"title\", \"\"))
                    if any(w in title_norm for w in _EXCLUDE_WORDS):
                        continue
                    
                    has_model = (model_name in title_norm) if len(model_name) >= 3 else False
                    series_head = normalize_text(series_name.split()[0]) if series_name.split() else \"\"
                    has_series = (series_name in title_norm or (len(series_head) >= 2 and series_head in title_norm)) if len(series_name) >= 2 else False

                    if has_model or has_series:
                        item[\"platform\"] = platform
                        keyword_filtered.append(item)
                
                if not keyword_filtered:
                    print(f\"    [!] キーワード一致なし、全件を対象にします。\")
                    for item in candidates: item[\"platform\"] = platform
                    keyword_filtered = candidates

                # browser_page = get_browser_page() # Removed this line as browser is passed in
                collected = []
                for item in keyword_filtered:
                    page_url = item.get('page_url', '')
                    if not page_url or not page_url.startswith(\"http\"): continue

                    # 詳細情報の取得
                    detail = None
                    if platform in [\"メルカリ\", \"ラクマ\"]:
                        detail = scrape_item_data(page_url, browser)
                    elif platform == \"駿河屋\":
                        detail = scrape_surugaya_item(page_url, browser)
                    elif platform == \"Yahooショッピング\":
                        detail = scrape_yahoo_item(page_url, browser)
                    
                    if detail: item.update(detail)
                    
                    current_img_urls = item.get(\"img_urls\", [])
                    if not current_img_urls and item.get(\"img_url\"):
                        current_img_urls = [item[\"img_url\"]]
                    
                    if current_img_urls:
                        item[\"_all_img_urls\"] = current_img_urls
                        collected.append(item)
                return collected

            print(f\"\n[*] 国内5大プラットフォームを順次調査中（収集フェーズ）...\")
            
            # 各プラットフォームから収集
            m_res = search_mercari(search_query, browser, max_results=15)
            all_domestic_candidates.extend(collect_candidates(m_res, \"メルカリ\"))

            r_res = search_rakuma(search_query, browser, max_results=10)
            all_domestic_candidates.extend(collect_candidates(r_res, \"ラクマ\"))

            s_res = search_surugaya(search_query, browser, max_results=10)
            all_domestic_candidates.extend(collect_candidates(s_res, \"駿河屋\"))

            _series = name_data.get('series', '')
            _model = name_data.get('model', '')
            _jp_name = final_name.replace(_series, '').replace(_model, '').strip(' ,、')
            api_query = f\"{_series} {_model} {_jp_name}\".strip() or search_query
            
            all_domestic_candidates.extend(collect_candidates(search_rakuten(api_query), \"楽天市場\"))
            all_domestic_candidates.extend(collect_candidates(search_yahoo(api_query), \"Yahooショッピング\"))

            # ── 国内一括判定フェーズ ──
            if all_domestic_candidates:
                print(f\"\n[*] 国内全候補 ({len(all_domestic_candidates)}件) を一括画像判定中...\")
                # サーバーに送る形式に変換 (1商品複数画像対応のため、フラットに展開)
                server_payload = []
                for idx, cand in enumerate(all_domestic_candidates):
                    for img_url_target in cand[\"_all_img_urls\"]:
                        server_payload.append({
                            \"img_url\": img_url_target,
                            \"page_url\": cand.get(\"page_url\"),
                            \"_cand_idx\": idx
                        })
                
                judged_list, thresholds = judge_similarity(img_url, server_payload)
                domestic_thresholds = thresholds
                
                # スコアを商品ごとに集約（複数画像のうち最高スコアを採用）
                final_results = []
                for idx, cand in enumerate(all_domestic_candidates):
                    cand_scores = [float(item.get(\"score\", 0)) for item in judged_list if item.get(\"_cand_idx\") == idx]
                    best_score = max(cand_scores) if cand_scores else 0
                    
                    if best_score > 0:
                        cand[\"score\"] = best_score
                        # 最高スコアの画像URLを特定
                        best_item_ji = next(item for item in judged_list if item.get(\"_cand_idx\") == idx and float(item.get(\"score\", 0)) == best_score)
                        cand[\"best_img_url\"] = best_item_ji.get(\"img_url\")
                        
                        # 価格・送料計算
                        p_str = re.sub(r'[^\d]', '', str(cand.get('price', '0')))
                        price_val = int(p_str) if p_str else 0
                        cand[\"price_int\" ] = price_val
                        ship_fee = cand.get(\"actual_shipping_fee\", 0)
                        total_price = price_val + ship_fee
                        cand[\"total_price\"] = total_price
                        
                        # 暫定最安値更新
                        if total_price < tentative_best_price:
                            tentative_best_price = total_price
                            tentative_best_item = cand
                        
                        # スペック補完
                        if weight_final == \"不明\" or dims_final == \"不明\":
                            desc_text = cand.get(\"description\", \"\") + \" \" + cand.get(\"title\", \"\")
                            ext_w, ext_d = extract_specs_from_text(desc_text)
                            if weight_final == \"不明\" and ext_w != \"不明\": weight_final = ext_w
                            if dims_final == \"不明\" and ext_d != \"不明\": dims_final = ext_d
                        
                        final_results.append(cand)
                
                final_candidates = final_results
                print(f\"[*] 国内判定完了: {len(final_candidates)} 件合格 (D閾値:{thresholds.get('dino'):.1f} C閾値:{thresholds.get('color'):.1f})\")

        if weight_final == \"不明\" and raw_w != \"不明\": weight_final = truncate_weight(raw_w)
        if dims_final == \"不明\" and raw_d != \"不明\": dims_final = adjust_dimensions(raw_d)

        # 5. 【最終補完】スペック情報（重量/サイズ）の補完
        amz_urls_checked = []  # 参照したAmazon URLを収集
        amz_search_url = None   # Amazon検索URL（取得できなくても表示用に保持）
        if weight_final == \"不明\" or dims_final == \"不明\":
            # --- STEP 5.1: Amazon からの補完 ---
            print(\"\n[*] 重量またはサイズが不明なため、Amazonからスペック情報を補完中...\")
            try:
                browser = get_fresh_browser()
                amz_search_query = final_name if final_name != \"特定不能\" else target_item.get('title')
                import re as _re
                amz_search_url = f\"https://www.amazon.co.jp/s?k={_re.sub(r'[\\s　]+', '+', amz_search_query)}\"
                amz_results = search_amazon(amz_search_query, browser, max_results=5)
                
                # 1. Amazon内検索でヒットした場合（画像あり）
                amz_for_judge = [{\"img_url\": r.get(\"img_url\", \"\"), \"page_url\": r.get(\"page_url\", \"\"), \"_orig\": r}
                                 for r in amz_results if r.get(\"img_url\")]

                if amz_for_judge:
                    amz_judged_list, _ = judge_similarity(img_url, amz_for_judge)
                    
                    if amz_judged_list and amz_judged_list[0].get(\"score\", 0) >= 70:
                        best_amz = amz_judged_list[0]
                        amz_url = best_amz.get(\"page_url\") or best_amz.get(\"_orig\", {}).get(\"page_url\")
                        if amz_url:
                            amz_urls_checked.append(amz_url)
                            print(f\"    [MATCH] Amazonで一致商品を発見 (Score: {best_amz['score']:.1f}%).\")
                            amz_specs = scrape_amazon_specs(amz_url, browser)
                            if weight_final == \"不明\" and amz_specs.get(\"weight\") != \"不明\":
                                weight_final = truncate_weight(amz_specs[\"weight\"])
                            if dims_final == \"不明\" and amz_specs.get(\"dimensions\") != \"不明\":
                                dims_final = adjust_dimensions(amz_specs[\"dimensions\"])
                            if amz_specs.get(\"origin\", \"不明\") != \"不明\":
                                origin_final = amz_specs[\"origin\"]
                                if re.search(r\"中国|china\", origin_final, re.I):
                                    high_tariff_flag = True
                                    print(f\"    [⚠️ 原産地] {origin_final} → 高関税フラグを有効化\")
                    else:
                        print(\"    [!] Amazon候補が類似度不足。Google経由で再検索します...\", flush=True)
                        amz_results = []  # Google経由フォールバックへ

                # 2. 画像なし or 類似度不足 → Google経由
                if not amz_for_judge or not amz_results:
                    print(\"    [!] Amazon内検索でヒットしないため、Google経由でAmazonを検索します...\")
                    amz_google_results = search_amazon_via_google(amz_search_query, browser, max_results=3)
                    
                    if amz_google_results:
                        for r in amz_google_results:
                            amz_url = r.get(\"page_url\")
                            if amz_url:
                                amz_urls_checked.append(amz_url)
                            print(f\"    [*] Google経由で発見したページを解析中: {amz_url}\")
                            
                            # --- 🌟 追加: Amazon詳細ページを開いて画像を直接取得し、判定する ---
                            tab = browser.latest_tab
                            tab.get(amz_url)
                            tab.wait(2)
                            
                            # 画像要素の取得 (Amazonの商品画像は主に #landingImage か #imgBlkFront)
                            img_ele = tab.ele('#landingImage') or tab.ele('#imgBlkFront')
                            if img_ele:
                                amz_img_src = img_ele.attr('src') or img_ele.attr('data-old-hires')
                                if amz_img_src:
                                    print(\"    [*] 詳細ページから画像を取得. 類似度判定を実行します...\")
                                    amz_judged_list, _ = judge_similarity(img_url, [{\"img_url\": amz_img_src, \"page_url\": amz_url}])
                                    if not amz_judged_list or amz_judged_list[0].get(\"score\", 0) < 70:
                                        score = amz_judged_list[0].get(\"score\", 0) if amz_judged_list else 0
                                        print(f\"    [SKIP] 画像判定不合格 (Score: {score:.1f}%)\")
                                        continue
                                    print(f\"    [MATCH] 画像判定合格！スペックを抽出します...\")
                            # --------------------------------------------------------
                            
                            # 判定に合格した場合（または画像が取れなかった場合）にスペック抽出
                            amz_specs = scrape_amazon_specs(amz_url, browser)
                            
                            found_any = False
                            if weight_final == \"不明\" and amz_specs.get(\"weight\") != \"不明\":
                                weight_final = truncate_weight(amz_specs[\"weight\"])
                                found_any = True
                            if dims_final == \"不明\" and amz_specs.get(\"dimensions\") != \"不明\":
                                dims_final = adjust_dimensions(amz_specs[\"dimensions\"])
                                found_any = True
                            if amz_specs.get(\"origin\", \"不明\") != \"不明\":
                                origin_final = amz_specs[\"origin\"]
                                if re.search(r\"中国|china\", origin_final, re.I):
                                    high_tariff_flag = True
                                    print(f\"    [⚠️ 原産地] {origin_final} → 高関税フラグを有効化\")
                                
                            if found_any:
                                print(\"    [MATCH] Google経由のAmazonページからスペックの補完に成功しました。\")
                                break
                                
            except Exception as e:
                print(f\"    [!] Amazonスペック補完中にエラー: {e}\")

            # --- STEP 5.2: Amazonでも不明な場合の LLM(Gemma) 推定 ---
            if weight_final == \"不明\" or dims_final == \"不明\":
                print(\"\n[*] Amazonでも判明しなかったため、LLM(画像)による重量・サイズ推定を実行します...\")
                try:
                    llm_estimated = estimate_weight_with_llm(img_url, final_name)
                    if llm_estimated:
                        if weight_final == \"不明\": weight_final = llm_estimated.get(\"weight\", \"不明\")
                        if dims_final == \"不明\": dims_final = llm_estimated.get(\"dimensions\", \"不明\")
                except Exception as e:
                    print(f\"    [!] LLM推定処理でエラー: {e}\")

        best_item = tentative_best_item

        # ===== 国内最安値のLLM型番検証（完全並列版） =====
        ebay_condition = \"Good\"
        if best_item and name_data.get(\"model\"):
            from concurrent.futures import ThreadPoolExecutor, as_completed
            model_number = name_data.get(\"model\", \"\")
            print(f\"\n[*] 国内最安値商品をLLMで詳細検証中（型番: {model_number}）...\")

            sorted_candidates = sorted(final_candidates, key=lambda x: x.get(\"total_price\", float('inf')))

            # 画像URLを事前解決
            _logo_patterns = [\"static\", \"logo\", \"banner\", \"icon\", \"badge\", \"avatar\", \"profile\"]
            def _resolve_img(cand):
                _raw_imgs = cand.get(\"img_urls\") or ([cand.get(\"img_url\")] if cand.get(\"img_url\") else [])
                _filtered = [u for u in _raw_imgs if u and not any(p in u.lower() for p in _logo_patterns)]
                return cand.get(\"best_img_url\") or (_filtered or _raw_imgs or [None])[0]

            # 画像なし候補は即通過（最安値優先）
            no_img_cand = next((c for c in sorted_candidates if not _resolve_img(c)), None)

            # 全件並列判定
            def _judge(cand):
                cand_img = _resolve_img(cand)
                if not cand_img:
                    return cand, True, \"Good\"
                domestic_desc = cand.get(\"condition\", \"\") + \" \" + cand.get(\"description\", \"\")
                is_match, cond = verify_model_match(
                    img_url, cand_img, model_number, domestic_desc,
                    ref_title=name_data.get(\"title_en\", \"\"),
                    cand_title=cand.get(\"title\", \"\")
                )
                # タイトル型番クロスチェック
                ref_model  = model_number.upper()
                cand_title_u = cand.get(\"title\", \"\").upper()
                ref_prefix_m = re.match(r'^([A-Z]+)', ref_model)
                ref_digits_m = re.search(r'(\d{3,})', ref_model)
                ref_prefix = ref_prefix_m.group(1) if ref_prefix_m else \"\"
                ref_digits = ref_digits_m.group(1) if ref_digits_m else \"\"
                if is_match and ref_digits:
                    for tok in re.findall(r'[A-Z]{1,4}[\d]{3,}[\w\-]*', cand_title_u):
                        tok_prefix_m = re.match(r'^([A-Z]+)', tok)
                        tok_digits_m = re.search(r'(\d{3,})', tok)
                        tok_prefix = tok_prefix_m.group(1) if tok_prefix_m else \"\"
                        tok_digits = tok_digits_m.group(1) if tok_digits_m else \"\"
                        if tok_digits == ref_digits and tok_prefix and tok_prefix != ref_prefix:
                            print(f\"    [TITLE CONFLICT] 型番不一致: ref={ref_model} vs {tok} → スキップ\")
                            is_match = False
                            break
                return cand, is_match, cond

            best_item = None
            with ThreadPoolExecutor(max_workers=3) as ex:
                futures = {ex.submit(_judge, c): c for c in sorted_candidates}
                results = {}
                for future in as_completed(futures):
                    cand, is_match, cond = future.result()
                    results[id(cand)] = (is_match, cond)

            # 結果を価格順に走査して最初の一致を採用
            for cand in sorted_candidates:
                is_match, cond = results.get(id(cand), (False, \"Good\"))
                if is_match:
                    best_item = cand
                    ebay_condition = cond
                    best_item['ebay_condition'] = ebay_condition
                    break

            if not best_item:
                print(\"    [!] LLM検証で全候補が除外されました。最安値なしとして処理します。\")
        # ====================================================================
        if best_item:
            print(\"\n[*] 国内最安値が判明したため、eBay全体での競合最安値（US/UK）をチェックします...\")
            from validate_ebay_search_v3 import process_market, get_ebay_token
            
            token = get_ebay_token()
            if token:
                print(f\"[*] eBay競合検索キーワード: {final_en_name}\")
                ebay_model_number = name_data.get(\"model\", \"\")
                
                # 日本で見つかった最安値商品のコンディションを判別
                best_cond_str = str(best_item.get(\"condition\", \"\")).lower()
                # 1. 「新品」「new」が含まれているか判定
                contains_new = \"新品\" in best_cond_str or \"new\" in best_cond_str
                # 2. 中古を示唆するキーワードが含まれているか判定（「同様」「展示」などは新古品＝USED扱い）
                is_used_keyword = any(k in best_cond_str for k in [\"中古\", \"used\", \"2\", \"傷\", \"汚れ\", \"近い\", \"同様\", \"訳あり\", \"展示\", \"ランク\"])
                # 3. 「未開封」があれば新品扱いを優先
                is_unopened = \"未開封\" in best_cond_str
                
                if is_unopened:
                    target_cond = \"NEW\"
                elif contains_new and not is_used_keyword:
                    target_cond = \"NEW\"
                else:
                    target_cond = \"USED\"
                
                print(f\"    [*] 国内最安値のコンディション: {best_item.get('condition')} -> eBay調査条件: {target_cond}\")
                
                # US/UK 並列検索
                from concurrent.futures import ThreadPoolExecutor

                def _search_market(market_id):
                    label = \"US\" if market_id == \"EBAY_US\" else \"UK\"
                    top3 = process_market(token, market_id, final_en_name, img_url, target_cond, model_number=ebay_model_number, exclude_id=item_id, ebay_title=target_item.get('title', ''), base_thresholds=domestic_thresholds)
                    if target_cond == \"NEW\" and not top3:
                        print(f\"    [!] {label}で新品が見つからないため、中古で再検索します...\")
                        top3 = process_market(token, market_id, final_en_name, img_url, \"USED\", model_number=ebay_model_number, exclude_id=item_id, ebay_title=target_item.get('title', ''), base_thresholds=domestic_thresholds)
                    
                    # 結果が0件のとき、ターゲット自身を価格参照としてフォールバック
                    if not top3:
                        ebay_price = target_item.get(\"price\")
                        ebay_url   = f\"https://www.ebay.com/itm/{item_id}\"
                        if ebay_price:
                            try:
                                # 文字列として受け取る可能性があるため変換
                                price_val = float(str(ebay_price).replace(\",\", \"\").replace(\"$\", \"\").strip())
                                print(f\"    [!] {label}: 競合0件のため、ターゲット自身の価格をフォールバックとして使用 (${price_val:.2f})\")
                                top3 = [{
                                    \"itemId\":    item_id,
                                    \"title\":     target_item.get(\"title\", \"\"),
                                    \"price\":     price_val,
                                    \"currency\":  \"USD\",
                                    \"shipping\":  0.0,
                                    \"total_usd\": price_val,
                                    \"score\":     100.0,
                                    \"item_url\":  ebay_url,
                                    \"condition\": target_cond,
                                }]
                            except Exception:
                                pass
                    return top3

                with ThreadPoolExecutor(max_workers=2) as ex:
                    fut_us = ex.submit(_search_market, \"EBAY_US\")
                    fut_uk = ex.submit(_search_market, \"EBAY_GB\")
                    us_top3 = fut_us.result()
                    uk_top3 = fut_uk.result()

                # ミラーリングロジック
                if us_top3 and not uk_top3:
                    print(\"[*] UK の結果が空のため、US の結果を UK にミラーリングします。\")
                    uk_top3 = us_top3.copy()
                elif uk_top3 and not us_top3:
                    print(\"[*] US の結果が空のため、UK の結果を US にミラーリングします。\")
                    us_top3 = uk_top3.copy()

                print(\"\n\" + \"=\"*50)
                print(\"   🏆 eBay Global 競合最安値 (Top 3)\")
                print(\"=\"*50)
                results = {\"US\": us_top3, \"UK\": uk_top3}
                for m_id in [\"US\", \"UK\"]:
                    print(f\"\n[Market: eBay {m_id}]\")
                    if not results[m_id]:
                        print(\"  一致する商品は見つかりませんでした。\")
                    else:
                        for i, res in enumerate(results[m_id], 1):
                            print(f\"  Rank {i}: {res['title'][:60]}...\")
                            print(f\"    - 合計価格: ${res['total_usd']:,.2f} USD (本体:{res['price']} {res['currency']} + 送料:{res['shipping']})\")
                            print(f\"    - 適合率:   {res['score']:.1f}%\")
                            print(f\"    - URL:      {hyperlink(res['item_url'])}\")
            else:
                print(\"[!] eBay APIトークンが取得できなかったため、競合チェックをスキップします。\")
        # 6. Excelへの自動書き込み
        if best_item:
            # 書き込み用データの整形
            def ext_dim(d_str, idx):
                nums = re.findall(r\"\d+\", d_str)
                return nums[idx] if len(nums) > idx else \"\"

            def ext_weight(w_str):
                match = re.search(r\"\d+\", w_str.replace(\",\", \"\"))
                return match.group() if match else \"\"

            # 保存された ebay_condition を取得
            final_ebay_condition = best_item.get('ebay_condition', ebay_condition)

            item_data = {
                \"product_name\": final_name,
                \"length\": ext_dim(dims_final, 0),
                \"width\": ext_dim(dims_final, 1),
                \"height\": ext_dim(dims_final, 2),
                \"weight\": ext_weight(weight_final),
                \"domestic_price\": best_item.get(\"total_price\", 0),
                \"us_top3_prices\": [item[\"total_usd\"] for item in us_top3] if 'us_top3' in locals() else [],
                \"uk_top3_prices\": [item[\"total_usd\"] for item in uk_top3] if 'uk_top3' in locals() else [],
                \"us_shipping_jpy\": locals().get('calculated_us_shipping_jpy', 0),
                \"uk_shipping_jpy\": locals().get('calculated_uk_shipping_jpy', 0),
                \"ebay_url\": f\"https://www.ebay.com/itm/{item_id}\",
                \"source_url\": best_item.get(\"page_url\", \"\"),
                \"is_high_tariff\": high_tariff_flag,
                \"condition\": final_ebay_condition,
                \"origin\": origin_final
            }

            write_to_sheet(item_data)
            database.mark_as_researched(
                item_id,
                platform=best_item.get(\"platform\"),
                title=best_item.get(\"title\"),
                price=best_item.get(\"total_price\"),
                condition=best_item.get(\"condition\"),
                url=best_item.get(\"page_url\"),
                weight=weight_final,
                dimensions=dims_final
            )
        else:
            print(\"\n[!] 国内最安値が見つからなかったため、Excelへの書き込みをスキップします。\")


        print(f\"{BLUE}\n{'='*60}{RESET}\")
        print(f\"{BLUE}                 ✨ リサーチ完了 ✨{RESET}\")
        print(f\"{BLUE}{'='*60}{RESET}\")
        print(f\"{BLUE}■ eBay商品 ID  : {item_id}{RESET}\")
        print(f\"{BLUE}■ 判定した商品名: {final_name}{RESET}\")
        print(f\"{BLUE}{'-' * 60}{RESET}\")
        
        if best_item:
            print(f\"{BLUE}【！国内最安値確定商品！】{RESET}\")
            print(f\"{BLUE}■ プラットフォーム: {best_item.get('platform')}{RESET}\")
            print(f\"{BLUE}■ 商品タイトル    : {best_item.get('title')}{RESET}\")
            print(f\"{BLUE}■ 送料込み価格    : ¥{best_item.get('total_price', 0):,}{RESET}\")
            print(f\"{BLUE}■ 商品URL         : {hyperlink(best_item.get('page_url'))}{RESET}\")
            print(f\"{BLUE}■ 加重平均スコア  : {best_item.get('score', 0):.1f}%{RESET}\")
            print(f\"{BLUE}■ 商品の状態      : {best_item.get('condition')}{RESET}\")
        else:
            print(f\"{BLUE} domestic lowest price NOT found (類似の商品が見つかりませんでした)。{RESET}\")
        
        if high_tariff_flag:
            print(f\"{BLUE}\n【⚠️ 重要：高関税注意】{RESET}\")
            print(f\"{BLUE}この商品は AI により「{material_label}」素材である可能性が高いと判定されました。{RESET}\")
            print(f\"{BLUE}鉄・鋼鉄製品や本革製品は関税が高くなる可能性があるため、仕入れ前に再確認してください。{RESET}\")
            
        print(f\"{BLUE}{'-' * 60}{RESET}\")
        print(f\"{BLUE}■ 原産地          : {origin_final}{RESET}\")
        print(f\"{BLUE}■ 推定重量        : {weight_final}{RESET}\")
        print(f\"{BLUE}■ 推定サイズ      : {dims_final}{RESET}\")
        if amz_urls_checked:
            print(f\"{BLUE}■ Amazon参照URL   :{RESET}\")
            for _au in amz_urls_checked:
                print(f\"{BLUE}  - {hyperlink(_au)}{RESET}\")
        elif amz_search_url:
            print(f\"{BLUE}■ Amazon参照URL   : {hyperlink(amz_search_url, '検索結果を開く')} （商品詳細は未取得）{RESET}\")
        else:
            print(f\"{BLUE}■ Amazon参照URL   : （参照なし）{RESET}\")
        print(f\"{BLUE}{'='*60}{RESET}\")
        print(f\"{BLUE}[*] データベースへの記録が完了しました。\n{RESET}\")
        _elapsed = datetime.datetime.now() - _start_time
        _m, _s = divmod(int(_elapsed.total_seconds()), 60)
        print(f\"{BLUE}[*] 処理時間: {_m}分{_s}秒{RESET}\")
        
        # トークン統計の表示
        print_token_stats()
        
        # Windows トースト通知
        try:
            import subprocess
            _title = \"eBayリサーチ完了\"
            _body  = f\"{final_name} | {_m}分{_s}秒\"
            subprocess.Popen([
                \"powershell\", \"-WindowStyle\", \"Hidden\", \"-Command\",
                f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;'
                f'$t = [Windows.UI.Notifications.ToastTemplateType]::ToastText02;'
                f'$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($t);'
                f'$x.GetElementsByTagName(\"text\")[0].AppendChild($x.CreateTextNode(\"{_title}\")) | Out-Null;'
                f'$x.GetElementsByTagName(\"text\")[1].AppendChild($x.CreateTextNode(\"{_body}")) | Out-Null;'
                f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier(\"eBay Research\").Show('
                f'[Windows.UI.Notifications.ToastNotification]::new($x));'
            ], creationflags=0x08000000)
        except Exception:
            pass

    except Exception as e:
        print(f\"\n[！重大なエラーが発生しました！]\")
        print(f\"エラー内容: {e}\")
        traceback.print_exc()

def main():
    print(\"\n\" + \"=\"*50)
    print(\"   eBay/国内5大ECプラットフォーム 横断リサーチツール\")
    print(\"=\"*50)
    
    url = input(\"eBayの検索結果URLを入力してください:\n> \").strip()
    if not url: return

    print(\"\n[*] ブラウザの起動を確認中...\")
    browser = get_browser_page()
    if not browser:
        print(\"[!] ブラウザの取得に失敗したため終了します。\")
        return
    print(\"[*] ブラウザ準備完了。\")

    while True:
        execute_research_session(url, browser)
        print(\"\n\" + \"=\"*50)
        next_input = input(f'[Enter] 同じURLで再処理  /  新しいURL入力  /  [q] 終了\n> ').strip()
        if next_input.lower() == 'q':
            print(\"[*] 終了します。\")
            break
        elif next_input.startswith('http'):
            url = next_input

if __name__ == \"__main__\":
    main()
