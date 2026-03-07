import requests
import json
import time
import re
from collections import Counter
from config import OPENROUTER_API_KEY

def get_word_frequencies(titles):
    """タイトル群から単語の出現頻度をカウントした辞書を返す"""
    all_words_raw = []
    for title in titles:
        # 記号を除去し、スペースで区切る
        clean_title = re.sub(r'[【】\[\]（）()!！?？♪☆★*＊/／¥,]+', ' ', title)
        # 2文字以上の単語を抽出
        words = [w for w in clean_title.split() if len(w) > 1]
        all_words_raw.extend(words)
    
    counts = Counter(w.lower() for w in all_words_raw)
    # 上位30個程度の頻出単語を抽出
    freq_list = {w: counts[w] for w in counts if counts[w] >= 2}
    return freq_list

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
        "   {\"brand\": \"ブランド名\", \"series\": \"シリーズ名\", \"model\": \"型番\", \"keywords\": \"キーワード\"}"
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
