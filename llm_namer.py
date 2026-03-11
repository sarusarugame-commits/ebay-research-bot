import requests
import json
import time
import re
from collections import Counter
from config import OPENROUTER_API_KEY

JP_PATTERN = re.compile(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uFF00-\uFFEF]')

def get_word_frequencies(titles):
    """タイトル群から日本語単語の出現頻度をカウントした辞書を返す"""
    all_words_raw = []
    for title in titles:
        # 記号を除去し、スペースで区切る
        clean_title = re.sub(r'[【】\[\]（）()!！?？♪☆★*＊/／¥,]+', ' ', title)
        # 2文字以上の単語を抽出（日本語文字を含むもののみ）
        words = [w for w in clean_title.split() if len(w) > 1 and JP_PATTERN.search(w)]
        all_words_raw.extend(words)
    
    counts = Counter(all_words_raw)  # 日本語は大文字小文字変換不要
    # 2回以上出現した単語を抽出
    freq_list = {w: counts[w] for w in counts if counts[w] >= 2}
    return freq_list

def log_frequent_words(freq_data, label):
    """頻出単語の上位10個をログに出力する"""
    if freq_data:
        sorted_freq = sorted(freq_data.items(), key=lambda x: x[1], reverse=True)
        freq_str = ", ".join([f"{w}({c})" for w, c in sorted_freq[:10]])
        print(f"    [*] {label}頻出単語 (Top 10): {freq_str}")

def call_llm_api(model_id, prompt, response_format=None):
    """OpenRouter APIを呼び出す共通関数"""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/eBayResearchSystem",
        "X-Title": "eBay Research System"
    }
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0
    }
    if response_format:
        payload["response_format"] = response_format
        
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"    [!] {model_id} API Error: {response.status_code}")
            return None
    except Exception as e:
        print(f"    [!] {model_id} Connection Error: {e}")
        return None

def parse_llm_json(content):
    """AIの出力からJSON部分を抽出してパースする"""
    try:
        # コードブロックの除去
        json_str = re.sub(r"```json\s?|```", "", content).strip()
        # 万が一テキストが混じっている場合、最初の { から最後 } までを切り出し
        match = re.search(r"(\{.*\})", json_str, re.DOTALL)
        if match:
            json_str = match.group(1)
        return json.loads(json_str)
    except Exception as e:
        print(f"    [!] JSONパース失敗: {e}")
        return None

def extract_product_name(ebay_title, scored_candidates):
    """
    Step-3.5-Flash をメインに、GLM-4.5 をバックアップとして使用する。
    """
    if not OPENROUTER_API_KEY:
        return {"brand": "", "series": "", "model": "", "keywords": "", "full_name": ebay_title.split()[0]}
        
    # 1. 頻出単語をカウント
    titles = [c['title'] for c in scored_candidates if c.get('title')]
    freq_data = get_word_frequencies(titles)
    log_frequent_words(freq_data, "国内タイトル")
    
    # プロンプト作成
    prompt = (
        "あなたはプロのEC商品アナリストです。以下の情報を分析し、"
        "日本のマーケットで最も検索されやすく正確な『日本語の商品名』を抽出してJSONで出力してください。\n\n"
        f"【eBayタイトル】: {ebay_title}\n"
        f"【国内候補の頻出単語リスト（単語: 出現数）】: {json.dumps(freq_data, ensure_ascii=False)}\n\n"
        "【抽出・出力ルール】\n"
        "1. 日本語の名称が頻出単語リストにあれば、それを優先的に採用してください。\n"
        "2. 「brand」「series」「model」を明確に分けてください。\n"
        "3. 「keywords」は、色や限定版などの判別に不可欠な情報を3つ以内に絞って抽出してください（出現数3以上の単語を優先）。\n"
        "4. 必ず以下のJSON形式のみを出力してください（解説は一切不要）。\n"
        "   {\"brand\": \"ブランド名\", \"series\": \"シリーズ名\", \"model\": \"型番\", \"keywords\": \"キーワード\"}\n"
        "5. 【重要】brandは必ず正式なメーカー・ブランド名を使用してください。例：エポっち→エポック社、タカラ→タカラトミー、バンダイナムコ→バンダイ。略称・誤字・口語表現は必ず正式名称に修正してください。"
    )

    models = [
        {"id": "stepfun/step-3.5-flash:free", "json": False},
        {"id": "z-ai/glm-4.5-air:free", "json": True}
    ]

    for m in models:
        print(f"[*] AI ({m['id']}) で商品名を抽出中...")
        res = call_llm_api(m['id'], prompt, response_format={"type": "json_object"} if m['json'] else None)
        
        if res and "choices" in res:
            content = res["choices"][0]["message"]["content"].strip()
            data = parse_llm_json(content)
            if data:
                brand = data.get("brand", "").strip()
                series = data.get("series", "").strip()
                model = data.get("model", "").strip()
                keywords = data.get("keywords", "").strip()
                
                full_name = f"{brand} {series} {model} {keywords}".strip().replace("  ", " ")
                print(f"    - 抽出成功: {full_name}")
                return {
                    "brand": brand, "series": series, "model": model, "keywords": keywords,
                    "full_name": full_name
                }
        print(f"    [!] {m['id']} での抽出に失敗しました。")

    # 全て失敗時のインテリジェント・フォールバック
    words = ebay_title.split()
    model_candidate = ""
    for w in words:
        if any(c.isdigit() for c in w) and len(w) > 4:
            model_candidate = w
            break
            
    fallback_name = f"{words[0]} {model_candidate}".strip()
    print(f"    [!] フォールバック商品名を使用: {fallback_name}")
    return {
        "brand": words[0], "series": "", "model": model_candidate, "keywords": "",
        "full_name": fallback_name
    }

def extract_english_product_name(ebay_title, scored_candidates):
    """eBay競合検索用の英語商品名（検索クエリ）を抽出する"""
    if not OPENROUTER_API_KEY:
        return {"brand": "", "series": "", "model": "", "keywords": "", "full_name": ebay_title.split()[0]}
        
    titles = [c['title'] for c in scored_candidates if c.get('title')]
    freq_data = get_word_frequencies(titles)
    log_frequent_words(freq_data, "海外タイトル")
    
    prompt = (
        "あなたはプロのEC商品アナリストです。以下の情報を分析し、"
        "eBay（USやUKなど）で最も検索されやすく正確な『英語の商品名（検索クエリ）』を抽出してJSONで出力してください。\n\n"
        f"【eBayタイトル（元）】: {ebay_title}\n"
        f"【海外候補の頻出単語リスト（単語: 出現数）】: {json.dumps(freq_data, ensure_ascii=False)}\n\n"
        "【抽出・出力ルール】\n"
        "1. 英語の名称が頻出単語リストにあれば、それを優先的に採用してください。\n"
        "2. 「brand」「series」「model」を明確に分けてください。\n"
        "3. 「keywords」は、色や限定版などの判別に不可不可欠な情報を3つ以内に絞って抽出してください（出現数3以上の単語を優先）。\n"
        "4. 必ず以下のJSON形式のみを出力してください（解説は一切不要）。\n"
        "   {\"brand\": \"Brand\", \"series\": \"Series\", \"model\": \"Model\", \"keywords\": \"Keywords\"}\n"
        "5. 【重要】動画共有プラットフォーム名、レビューや個人的な感想を示す単語、主観的な評価など、商品の型番や固有名称を特定する上で無関係なノイズワードは完全に除外してください。\n"
        "6. 【重要】ただし、商品の素材、製造年代、希少性、限定仕様など、商品の「客観的な価値」や「検索意図」に直結する属性キーワードは、検索クエリとして不可欠なため必ず保持してください。"
    )

    models = [
        {"id": "stepfun/step-3.5-flash:free", "json": False},
        {"id": "z-ai/glm-4.5-air:free", "json": True}
    ]

    for m in models:
        print(f"[*] AI ({m['id']}) で英語商品名を抽出中...")
        res = call_llm_api(m['id'], prompt, response_format={"type": "json_object"} if m['json'] else None)
        
        if res and "choices" in res:
            content = res["choices"][0]["message"]["content"].strip()
            data = parse_llm_json(content)
            if data:
                brand = data.get("brand", "").strip()
                series = data.get("series", "").strip()
                model = data.get("model", "").strip()
                keywords = data.get("keywords", "").strip()
                
                full_name = f"{brand} {series} {model} {keywords}".strip().replace("  ", " ")
                print(f"    - 抽出成功: {full_name}")
                return {"brand": brand, "series": series, "model": model, "keywords": keywords, "full_name": full_name}
                
    # 全て失敗時のフォールバック
    words = ebay_title.split()
    model_candidate = next((w for w in words if any(c.isdigit() for c in w) and len(w) > 4), "")
    fallback_name = f"{words[0]} {model_candidate}".strip()
    return {"brand": words[0], "series": "", "model": model_candidate, "keywords": "", "full_name": fallback_name}
