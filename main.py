import os
import sys
import time
import requests
import json
import sqlite3
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# 自作モジュール
from ebay_api import search_ebay
from shopping_api import search_rakuten, search_yahoo, search_surugaya, scrape_surugaya_item, scrape_yahoo_item
from mercari_scraper import search_mercari, search_rakuma, scrape_item_data
from llm_namer import extract_product_name, extract_ebay_search_query
from llm_vision_judge import (
    estimate_weight_with_llm, 
    analyze_item_safety_and_tariff, 
    judge_similarity_with_llm,
    verify_model_match
)
from sheets_writer import write_to_google_sheets
from config import GEMINI_API_KEY, SHIPPING_COST_PER_KG
import database
from browser_utils import get_fresh_browser

def normalize_text(text):
    if not text: return ""
    return text.lower().strip()

def calculate_shipping_and_profit(ebay_price_usd, weight_g, domestic_price_jpy, exchange_rate):
    """
    利益計算ロジック
    """
    # 概算送料 (1kg = 3000円換算など)
    weight_kg = float(re.sub(r'[^0-9.]', '', str(weight_g))) / 1000 if weight_g != "不明" else 0.5
    shipping_jpy = weight_kg * SHIPPING_COST_PER_KG
    
    ebay_price_jpy = ebay_price_usd * exchange_rate
    # eBay手数料 (約15%) + 海外送料 + 国内仕入れ
    fees_jpy = ebay_price_jpy * 0.15
    profit_jpy = ebay_price_jpy - (fees_jpy + shipping_jpy + domestic_price_jpy)
    
    return int(shipping_jpy), int(profit_jpy)

def main():
    print("="*60)
    print(" eBay リサーチ部隊 - 国内最安値判定モード (並列版)")
    print("="*60)

    # 履歴管理
    last_url = ""
    
    while True:
        target_url = input(f"\n[?] eBay商品URLを入力 (Enterで前回と同じ: {last_url[:50]}...): ").strip()
        if not target_url:
            if last_url:
                target_url = last_url
            else:
                print("[!] URLを入力してください。")
                continue
        
        if target_url.lower() in ["exit", "quit", "q"]:
            break
        
        last_url = target_url

        item_id_match = re.search(r'/itm/(\d+)', target_url)
        if not item_id_match:
            print("[!] 有効なeBay商品URLではありません。")
            continue
        ebay_item_id = item_id_match.group(1)

        print(f"\n[*] ターゲットeBay商品: {ebay_item_id}")
        
        # ブラウザ生成
        browser = get_fresh_browser()
        
        try:
            # 1. eBay詳細情報取得 (キャッシュ確認含む)
            from ebay_scraper import scrape_ebay_item_specs
            ebay_specs = scrape_ebay_item_specs(ebay_item_id, browser)
            if not ebay_specs:
                print("[!] eBay情報の取得に失敗しました。")
                continue
            
            ebay_title = ebay_specs['title']
            ebay_price_usd = ebay_specs['price_usd']
            ebay_img_url = ebay_specs['main_img_url']
            
            print(f"    - Title: {ebay_title}")
            print(f"    - Price: ${ebay_price_usd}")

            # 2. 型番・クエリ抽出 (LLM)
            # まずeBayタイトルとキャッシュから国内検索用のヒントを得る
            # ここではまずeBay検索クエリを抽出
            query_info = extract_ebay_search_query(ebay_title)
            model_name = query_info.get("model", "")
            series_name = query_info.get("series", "")
            search_query = query_info.get("full_name", ebay_title)

            # 3. 国内プラットフォーム並列検索
            print(f"[*] 国内サイト並列検索中: {search_query} ...")
            
            platforms = [
                ("メルカリ", lambda q, b: search_mercari(q, b)),
                ("ラクマ", lambda q, b: search_rakuma(q, b)),
                ("楽天市場", lambda q, b: search_rakuten(q)),
                ("Yahooショッピング", lambda q, b: search_yahoo(q)),
                # ("駿河屋", lambda q, b: search_surugaya(q))
            ]

            all_candidates = []
            
            def _search_task(p_name, func):
                try:
                    # メルカリ・ラクマはブラウザが必要
                    # APIベース（楽天等）は第2引数無視でOK
                    res = func(search_query, browser)
                    for r in res: r["platform"] = p_name
                    return res
                except Exception as e:
                    print(f"    [!] {p_name} 検索エラー: {e}")
                    return []

            with ThreadPoolExecutor(max_workers=len(platforms)) as executor:
                futures = [executor.submit(_search_task, name, fn) for name, fn in platforms]
                for f in as_completed(futures):
                    all_candidates.extend(f.result())

            if not all_candidates:
                print("[!] 国内サイトに一致する商品が見つかりませんでした。")
                continue

            # 4. 候補の絞り込み (キーワード一致優先)
            def collect_candidates(candidates, platform):
                if not candidates:
                    print(f"[*] {platform}: 検索結果 0 件でした。")
                    return []

                print(f"[*] {platform}: {len(candidates)} 件の候補を収集中...")
                
                _EXCLUDE_WORDS = ["レンタル", "rental", "パーツ", "修理", "ジャンク", "部品取り", "訳あり", "難あり"]
                base_filtered = []
                for item in candidates:
                    title_norm = normalize_text(item.get("title", ""))
                    if any(w in title_norm for w in _EXCLUDE_WORDS):
                        continue
                    base_filtered.append(item)

                keyword_filtered = []
                for item in base_filtered:
                    title_norm = normalize_text(item.get("title", ""))
                    has_model = (model_name in title_norm) if len(model_name) >= 3 else False
                    series_head = normalize_text(series_name.split()[0]) if series_name.split() else ""
                    has_series = (series_name in title_norm or (len(series_head) >= 2 and series_head in title_norm)) if len(series_name) >= 2 else False

                    if has_model or has_series:
                        item["platform"] = platform
                        keyword_filtered.append(item)
                
                if not keyword_filtered:
                    print(f"    [!] キーワード一致なし、除外ワード適用済みの全件を対象にします。")
                    for item in base_filtered: item["platform"] = platform
                    keyword_filtered = base_filtered
                
                # 詳細情報の並列取得
                collected = []
                # ここも並列化 (各サイト2〜3件程度)
                to_scrape = keyword_filtered[:3]
                
                for item in to_scrape:
                    page_url = item["page_url"]
                    platform = item["platform"]
                    detail = None
                    if platform in ["メルカリ", "ラクマ"]:
                        detail = scrape_item_data(page_url, browser)
                    elif platform == "駿河屋":
                        detail = scrape_surugaya_item(page_url, browser)
                    elif platform == "Yahooショッピング":
                        detail = scrape_yahoo_item(page_url, browser)
                    
                    if detail: item.update(detail)
                    
                    # 詳細取得後の追加除外チェック (コンディション・説明文にジャンク等が含まれる場合は除外)
                    condition_text = normalize_text(item.get("condition", ""))
                    desc_text = normalize_text(item.get("description", ""))
                    if any(w in condition_text for w in ["全体的に状態が悪い", "ジャンク", "難あり", "訳あり"]):
                        continue
                    if "ジャンク" in desc_text:
                        continue
                    
                    current_img_urls = item.get("img_urls", [])
                    if not current_img_urls and item.get("img_url"):
                        current_img_urls = [item["img_url"]]
                    
                    if current_img_urls:
                        item["_all_img_urls"] = current_img_urls
                        collected.append(item)
                return collected

            # プラットフォームごとにトップ候補を取得
            platform_groups = {}
            for c in all_candidates:
                p = c["platform"]
                if p not in platform_groups: platform_groups[p] = []
                platform_groups[p].append(c)
            
            final_collect_list = []
            for p, cand_list in platform_groups.items():
                final_collect_list.extend(collect_candidates(cand_list, p))

            if not final_collect_list:
                print("[!] 有効な国内候補が見つかりませんでした。")
                continue

            # 5. LLM による画像・詳細比較 (5件並列)
            print(f"[*] AI判定開始 ({len(final_collect_list)} 件)...")
            
            def _judge(cand):
                # 型番クロスチェック
                c_title = normalize_text(cand.get("title", ""))
                # 型番がタイトルのどこかに含まれているか? (3文字以上の場合)
                if len(model_name) >= 4 and model_name not in c_title:
                     return cand, False, "Mismatch"

                img_url = (cand.get("_all_img_urls") or [None])[0]
                is_match, cond = verify_model_match(
                    ebay_img_url, 
                    img_url, 
                    model_name, 
                    cand.get("condition", "不明"),
                    ref_title=ebay_title,
                    cand_title=cand.get("title", "")
                )
                return cand, is_match, cond

            sorted_candidates = sorted(final_collect_list, key=lambda x: int(x.get("price", "9999999")))
            
            best_item = None
            with ThreadPoolExecutor(max_workers=5) as ex:
                futures = {ex.submit(_judge, c): c for c in sorted_candidates}
                results = {}
                for future in as_completed(futures):
                    cand, is_match, cond = future.result()
                    results[id(cand)] = (is_match, cond)

            # 判定結果の整理
            for cand in sorted_candidates:
                is_match, cond = results[id(cand)]
                if is_match:
                    best_item = cand
                    best_item["llm_condition"] = cond
                    break
            
            if not best_item:
                print("[!] AI判定により、一致する商品は見つかりませんでした。")
                continue

            # 6. 重量推論 & 最終計算
            weight_info = estimate_weight_with_llm(ebay_img_url, search_query)
            safety_info = analyze_item_safety_and_tariff(ebay_img_url)
            
            # 為替 (固定または取得)
            exchange_rate = 150.0 
            
            price_jpy = int(best_item["price"])
            ship_jpy, profit_jpy = calculate_shipping_and_profit(
                ebay_price_usd, weight_info["weight"], price_jpy, exchange_rate
            )
            
            # 結果表示
            print("\n" + "="*40)
            print(" 【リサーチ結果: 国内最安値】")
            print(f"  - 判定: 一致 ({best_item['platform']})")
            print(f"  - 商品: {best_item['title'][:50]}...")
            print(f"  - URL: {best_item['page_url']}")
            print(f"  - 仕入価格: ¥{price_jpy:,}")
            print(f"  - 推定送料: ¥{ship_jpy:,} (重量:{weight_info['weight']})")
            print(f"  - 見込利益: ¥{profit_jpy:,} (手数料・送料込)")
            print(f"  - コンディション: {best_item['llm_condition']}")
            print("="*40)

            # 7. スプレッドシート書込み
            write_to_google_sheets({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "ebay_id": ebay_item_id,
                "ebay_url": target_url,
                "ebay_price": ebay_price_usd,
                "domestic_platform": best_item["platform"],
                "domestic_price": price_jpy,
                "domestic_url": best_item["page_url"],
                "profit": profit_jpy,
                "shipping": ship_jpy,
                "weight": weight_info["weight"],
                "condition": best_item["llm_condition"],
                "memo": f"Safety: {safety_info['label']}"
            })

        except Exception as e:
            print(f"[!] エラー発生: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if 'browser' in locals():
                browser.quit()

if __name__ == "__main__":
    main()
