import gpu_utils
import sys
import io
import re
import datetime
import os as _os
import traceback
import functools
import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# WindowsでANSIエスケープを有効化
if sys.platform == "win32":
    _os.system("")
BLUE = "\033[94m"
RESET = "\033[0m"

def hyperlink(url, text=None):
    """OSC 8ハイパーリンク。Windows Terminal / VSCode対応。非対応端末はURLをそのまま表示。"""
    raw = text if text else url
    label = raw[:30] + "…" if len(raw) > 30 else raw
    return f"\033]8;;{url}\033\\{BLUE}{label}{RESET}\033]8;;\033\\"

def global_exception_handler(exctype, value, tb):
    print("\n" + "=" * 50)
    print("!!! UNCAUGHT EXCEPTION !!!")
    traceback.print_exception(exctype, value, tb)
    print("=" * 50 + "\n")

sys.excepthook = global_exception_handler

# Windows環境でのエンコードエラー（CP932）対策
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
print = functools.partial(print, flush=True)

from config import (
    EBAY_APP_ID,
    YAHOO_CLIENT_ID,
)
import database
from ebay_api import get_item_details, get_multiple_items_images_api
from mercari_scraper import search_mercari, search_rakuma, scrape_item_data
from surugaya_scraper import search_surugaya, scrape_surugaya_item
from llm_vision_judge import estimate_weight_with_llm, analyze_item_safety_and_tariff
from clip_judge_client import judge_similarity

from ebay_scraper import (
    scrape_ebay_item_specs,
    scrape_ebay_newest_items,
    ChromiumOptions,
    ChromiumPage,
)
from llm_namer import extract_product_name
from shopping_api import search_rakuten, search_yahoo, scrape_yahoo_item
from amazon_scraper import search_amazon, scrape_amazon_specs
from llm_vision_judge import verify_model_match
from sheets_writer import write_to_sheet

GREEN = "\033[92m"
RESET_GREEN = "\033[0m"

def get_fresh_browser():
    """並列処理用に新しいポートでブラウザを起動する"""
    co = ChromiumOptions()
    co.auto_port()
    co.headless(True)
    co.set_argument("--window-size=1280,720")
    return ChromiumPage(co)

def extract_specs_from_text(text):
    w, d = "不明", "不明"
    if not text: return w, d
    w_m = re.search(r"(\d+(\.\d+)?)\s?(kg|g|キロ|グラム)", text, re.I)
    if w_m: w = w_m.group(0)
    d_m = re.search(r"(\d+(\.\d+)?)\s?[x*×]\s?(\d+(\.\d+)?)\s?[x*×]\s?(\d+(\.\d+)?)\s?(cm|mm|センチ|インチ|in)?", text, re.I)
    if d_m: d = d_m.group(0)
    return w, d

def truncate_weight(w_str):
    if not w_str or w_str == "不明": return "不明"
    m = re.search(r"(\d+(\.\d+)?)", str(w_str))
    if m:
        val = float(m.group(1))
        unit = "kg" if "kg" in str(w_str).lower() or "キロ" in str(w_str) else "g"
        if unit == "g" and val > 2000: return f"{val/1000:.1f}kg"
        return f"{val}{unit}"
    return w_str

def adjust_dimensions(d_str):
    if not d_str or d_str == "不明": return "不明"
    return d_str.replace(" ", "")

def main():
    print("="*60)
    print(" eBay リサーチ部隊 - 国内最安値判定ツール")
    print("="*60)
    
    database.setup_db()
    last_url = ""
    
    while True:
        target_url = input(f"\n[?] eBay商品URLを入力 (Enterで前回と同じ: {last_url[:50]}...): ").strip()
        if not target_url:
            if last_url: target_url = last_url
            else:
                print("[!] URLを入力してください。")
                continue
        
        if target_url.lower() in ["exit", "quit", "q"]: break
        last_url = target_url

        # URL種別の判定
        item_ids = []
        if '/itm/' in target_url:
            # 単一商品
            item_id_match = re.search(r'/itm/(\d+)', target_url)
            if item_id_match:
                item_ids.append(item_id_match.group(1))
        elif '/sch/' in target_url or 'ebay.com/usr/' in target_url:
            # 検索結果またはセラーの商品一覧
            print("\n[*] 一覧URLを検知しました。商品リストを取得中...")
            browser = get_fresh_browser()
            try:
                list_items = scrape_ebay_newest_items(target_url, browser)
                item_ids = [it['id'] for it in list_items if it.get('id')]
            finally:
                browser.quit()
        
        if not item_ids:
            print("[!] 有効なeBay商品URL（個別または一覧）ではありません。")
            continue

        print(f"\n[*] 処理対象商品数: {len(item_ids)} 件")
        
        for ebay_item_id in item_ids:
            print(f"\n{'-'*40}")
            print(f"[*] ターゲットeBay商品: {ebay_item_id}")
            
            browser = get_fresh_browser()
            try:
                # 1. eBayスペック取得
                ebay_specs = scrape_ebay_item_specs(ebay_item_id, browser)
                if not ebay_specs or not ebay_specs.get('title'):
                    print(f"[!] eBay情報(ID:{ebay_item_id})の取得に失敗しました。")
                    continue
                
                ebay_title = ebay_specs['title']
                ebay_img_url = ebay_specs['img_urls'][0] if ebay_specs.get('img_urls') else ""
                print(f"    - Title: {ebay_title}")
                print(f"    - Price: ${ebay_specs.get('price_usd', 0)}")
                
                if not ebay_img_url:
                    print("[!] 画像URLが取得できなかったため、リサーチをスキップします。")
                    continue

                # 2. 日本語名の特定
                print("\n[*] LLMで日本語商品名を特定中...")
                name_data = extract_product_name(ebay_title)
                final_name = name_data.get("full_name", "特定不能")
                print(f" -> 最終確定した日本語名: {final_name}")

                # 3. 国内並列検索
                print(f"\n[*] 国内5大プラットフォームを並列調査中...")
                
                model_name = name_data.get("model", "").lower()
                series_name = name_data.get("series", "").lower()

                def normalize_text(text):
                    if not text: return ""
                    return text.lower().strip()

                def collect_candidates(candidates, platform):
                    if not candidates: return []
                    print(f"[*] {platform}: {len(candidates)} 件の候補を収集中...")
                    
                    _EXCLUDE_WORDS = ["レンタル", "rental", "パーツ", "修理", "ジャンク", "部品取り", "訳あり", "難あり"]
                    base_filtered = []
                    for item in candidates:
                        title_norm = normalize_text(item.get("title", ""))
                        if any(w in title_norm for w in _EXCLUDE_WORDS): continue
                        base_filtered.append(item)

                    keyword_filtered = []
                    for item in base_filtered:
                        title_norm = normalize_text(item.get("title", ""))
                        has_model = (model_name in title_norm) if len(model_name) >= 3 else False
                        series_parts = series_name.split()
                        series_head = normalize_text(series_parts[0]) if series_parts else ""
                        has_series = (series_name in title_norm or (len(series_head) >= 2 and series_head in title_norm)) if len(series_name) >= 2 else False

                        if has_model or has_series:
                            item["platform"] = platform
                            keyword_filtered.append(item)
                    
                    if not keyword_filtered:
                        keyword_filtered = base_filtered
                        for item in keyword_filtered: item["platform"] = platform

                    # 詳細情報の取得 (上位3件)
                    collected = []
                    to_scrape = keyword_filtered[:3]
                    for item in to_scrape:
                        try:
                            detail = None
                            if platform in ["メルカリ", "ラクマ"]: detail = scrape_item_data(item["page_url"], browser)
                            elif platform == "駿河屋": detail = scrape_surugaya_item(item["page_url"], browser)
                            elif platform == "Yahooショッピング": detail = scrape_yahoo_item(item["page_url"], browser)
                            
                            if detail: item.update(detail)
                            
                            # ジャンク再チェック
                            condition_text = normalize_text(item.get("condition", ""))
                            desc_text = normalize_text(item.get("description", ""))
                            if any(w in condition_text for w in ["全体的に状態が悪い", "ジャンク", "難あり", "訳あり"]): continue
                            if "ジャンク" in desc_text: continue
                            
                            if not item.get("_all_img_urls") and item.get("img_url"):
                                item["_all_img_urls"] = [item["img_url"]]
                            
                            if item.get("_all_img_urls"): collected.append(item)
                        except Exception as e:
                            print(f"    [!] {platform} 詳細取得エラー: {e}")
                    return collected

                all_candidates = []
                search_query = final_name
                
                tasks = [
                    ("メルカリ", lambda: search_mercari(search_query, browser)),
                    ("ラクマ", lambda: search_rakuma(search_query, browser)),
                    ("楽天市場", lambda: search_rakuten(search_query)),
                    ("Yahooショッピング", lambda: search_yahoo(search_query)),
                    ("駿河屋", lambda: search_surugaya(search_query, browser))
                ]

                # 各プラットフォームのタスク実行
                with ThreadPoolExecutor(max_workers=5) as executor:
                    futures = [executor.submit(lambda tk: (tk[0], tk[1]()), tk) for tk in tasks]
                    for f in as_completed(futures):
                        p_name, res = f.result()
                        all_candidates.extend(collect_candidates(res, p_name))

                if not all_candidates:
                    print("[!] 有効な国内候補が見つかりませんでした。")
                    continue

                # 4. LLM 画像判定 (並列)
                print(f"[*] AI判定開始 ({len(all_candidates)} 件)...")
                final_best = None
                
                def _judge(cand):
                    is_match, cond = verify_model_match(
                        ebay_img_url, 
                        cand["_all_img_urls"][0], 
                        model_name, 
                        cand.get("condition", "不明"),
                        ref_title=ebay_title,
                        cand_title=cand["title"]
                    )
                    return cand, is_match, cond

                with ThreadPoolExecutor(max_workers=5) as ex:
                    # 価格順（送料込み）にソートして判定
                    sorted_cands = sorted(all_candidates, key=lambda x: int(re.sub(r'\D', '', str(x.get('price', '999999')))))
                    judge_futures = [ex.submit(_judge, c) for c in sorted_cands]
                    for f in as_completed(judge_futures):
                        cand, ok, cond = f.result()
                        if ok:
                            final_best = cand
                            final_best["llm_condition"] = cond
                            break
                
                if final_best:
                    # 最終結果記録
                    res_data = {
                        "ebay_id": ebay_item_id,
                        "ebay_title": ebay_title,
                        "ebay_price_usd": ebay_specs.get('price_usd', 0),
                        "ebay_url": f"https://www.ebay.com/itm/{ebay_item_id}",
                        "domestic_best_price": final_best.get("price"),
                        "domestic_best_url": final_best.get("page_url"),
                        "domestic_platform": final_best.get("platform"),
                        "condition": final_best.get("llm_condition"),
                    }
                    write_to_sheet(res_data)
                    database.mark_as_researched(
                        ebay_item_id,
                        platform=final_best.get("platform"),
                        title=final_best.get("title"),
                        price=final_best.get("price"),
                        condition=final_best.get("llm_condition"),
                        url=final_best.get("page_url")
                    )
                    
                    print(f"\n{GREEN}✨ 最安値発見: {final_best['platform']} - ¥{final_best['price']:,}{RESET}")
                    print(f"URL: {hyperlink(final_best['page_url'])}")
                else:
                    print("[!] 一致する商品は見つかりませんでした。")

            except Exception as e:
                print(f"[!] 商品(ID:{ebay_item_id})の処理中にエラー: {e}")
                traceback.print_exc()
            finally:
                browser.quit()

if __name__ == "__main__":
    main()
