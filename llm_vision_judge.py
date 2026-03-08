import requests
import json
import re
import base64
from config import OPENROUTER_API_KEY, GEMINI_API_KEY

def estimate_weight_with_llm(ebay_img_url, final_name):
    """
    Gemma 3 (Vision) を用いて、商品の見た目と名称から「重量(kg)」と「サイズ(cm)」を推論する。
    """
    if not OPENROUTER_API_KEY:
        return {"weight": "不明", "dimensions": "不明"}
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    prompt = (
        f"あなたはプロの物流・梱包エキスパートです。添付された商品の画像と商品名（{final_name}）を元に、この商品の重量とサイズを推論してください。\n\n"
        "【推論のステップ】\n"
        "1. 画像と商品名から商品の材質（金属、プラスチック、木材等）と一般的なサイズ感を特定する。\n"
        "2. 外寸サイズ（縦x横x高さ mm）を推測する。\n"
        "3. 推測したサイズと材質から、中身の正味重量（g）を算出する。特に密度（金属は重く、プラスチックは軽い等）を考慮すること。\n"
        "4. 国際発送用の梱包（ダンボール、緩衝材）を含めた最終的なスペックを出す。梱包材の重さとして +100g、サイズとして 縦+20mm, 横+20mm, 高さ+10mm を加算すること。\n\n"
        "【出力形式】\n"
        "必ず以下のJSON形式でのみ出力してください（文章は一切不要です）。\n"
        "{\"weight\": \"数値g\", \"dimensions\": \"縦x横x高さmm\"}"
    )

    payload = {
        "model": "google/gemma-3-27b-it:free",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": ebay_img_url}}
                ]
            }
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"}
    }

    try:
        print(f"[*] Gemma 3 が画像から重量・サイズを推論しています...")
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        if response.status_code == 200:
            content = response.json()["choices"][0]["message"]["content"].strip()
            data = json.loads(content)
            return {
                "weight": data.get("weight", "不明"),
                "dimensions": data.get("dimensions", "不明")
            }
        elif response.status_code == 429:
            print(f"    [!] OpenRouter 429 Error (Spec). Gemini API でリトライします...")
            return estimate_weight_with_gemini(ebay_img_url, final_name, prompt)
    except Exception as e:
        print(f"[Error] スペック推論失敗: {e}")
    
    return {"weight": "不明", "dimensions": "不明"}

def estimate_weight_with_gemini(ebay_img_url, final_name, prompt):
    """スペック推論のGeminiフォールバック"""
    if not GEMINI_API_KEY: return {"weight": "不明", "dimensions": "不明"}
    try:
        img_resp = requests.get(ebay_img_url, timeout=10)
        if img_resp.status_code != 200: return {"weight": "不明", "dimensions": "不明"}
        img_data = base64.b64encode(img_resp.content).decode('utf-8')
        mime_type = img_resp.headers.get('Content-Type', 'image/jpeg')

        model = "gemini-3.1-flash-lite-preview"
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "parts": [{"text": prompt}, {"inline_data": {"mime_type": mime_type, "data": img_data}}]
            }]
        }
        resp = requests.post(api_url, json=payload, timeout=20)
        if resp.status_code == 200:
            res_json = resp.json()
            text_content = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
            # JSON部分を抽出
            json_match = re.search(r'\{.*\}', text_content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return {
                    "weight": data.get("weight", "不明"),
                    "dimensions": data.get("dimensions", "不明")
                }
    except Exception as e:
        print(f"    [!] Geminiスペック推論失敗: {e}")
    return {"weight": "不明", "dimensions": "不明"}

def analyze_item_safety_and_tariff(ebay_img_url):
    """
    Gemma 3 (Vision) を用いて、商品の「カテゴリー（アルコールか？）」と
    「素材（鉄・皮製品などの高関税対象か？）」を判定する。
    """
    if not OPENROUTER_API_KEY:
        return {"is_alcohol": False, "is_high_tariff": False, "label": "不明"}
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    prompt = (
        "あなたは税関および物流の専門家です。添付された商品の画像を見て、以下の2点を判定してください。\n\n"
        "1. 【アルコール製品か？】 ビール、ワイン、ウィスキー、酒類、またはアルコールを含有する製品（消毒液等含む）であれば 'true'、そうでなければ 'false'。\n"
        "2. 【高関税素材か？】 商品の材質に '金属（Metal）' または '革（Leather）' が含まれるか？ 含まれるなら 'true'、そうでなければ 'false'。\n\n"
        "【出力形式】\n"
        "以下のJSON形式でのみ出力してください（文章は一切不要です）。\n"
        "{\"is_alcohol\": bool, \"is_high_tariff\": bool, \"material_label\": \"見つかった素材名またはnull\"}"
    )

    payload = {
        "model": "google/gemma-3-27b-it:free", # ご安心を！フリーモデルを使用します！
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": ebay_img_url}}
                ]
            }
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"}
    }

    try:
        print(f"[*] Gemma 3 が商品の安全性をチェックしています（アルコール/高関税素材）...")
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code == 200:
            content = response.json()["choices"][0]["message"]["content"].strip()
            # JSONをパース
            data = json.loads(content)
            return {
                "is_alcohol": data.get("is_alcohol", False),
                "is_high_tariff": data.get("is_high_tariff", False),
                "label": data.get("material_label", "なし")
            }
        elif response.status_code == 429:
            print(f"     [!] OpenRouter 429 Error (Rate Limit). Gemini API でリトライします...")
            return analyze_item_safety_with_gemini(ebay_img_url, prompt)
            
    except Exception as e:
        print(f"     [!] 安全性チェック失敗: {e}")
    
    return {"is_alcohol": False, "is_high_tariff": False, "label": "判定エラー"}

def analyze_item_safety_with_gemini(ebay_img_url, prompt):
    """
    OpenRouterが制限にかかった際のバックアップ。
    Gemini API を直接叩いて判定を行う。
    """
    if not GEMINI_API_KEY:
        return {"is_alcohol": False, "is_high_tariff": False, "label": "Gemini APIキー未設定"}

    try:
        # 1. 画像をダウンロード
        img_resp = requests.get(ebay_img_url, timeout=10)
        if img_resp.status_code != 200:
            return {"is_alcohol": False, "is_high_tariff": False, "label": "画像DL失敗(Gemini)"}
        
        img_data = base64.b64encode(img_resp.content).decode('utf-8')
        mime_type = img_resp.headers.get('Content-Type', 'image/jpeg')

        # 2. Gemini API 呼び出し (v1beta)
        # 司令官のご指定通り gemini-3.1-flash-lite-preview を使用！
        model = "gemini-3.1-flash-lite-preview"
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": img_data
                        }
                    }
                ]
            }]
        }

        resp = requests.post(api_url, json=payload, timeout=20)
        if resp.status_code == 200:
            res_json = resp.json()
            text_content = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
            
            # JSON部分を抽出
            json_match = re.search(r'\{.*\}', text_content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return {
                    "is_alcohol": data.get("is_alcohol", False),
                    "is_high_tariff": data.get("is_high_tariff", False),
                    "label": data.get("material_label", "なし")
                }
        else:
            print(f"     [!] Gemini API エラー: {resp.status_code} - {resp.text}")

    except Exception as e:
        print(f"     [!] Gemini 回避判定失敗: {e}")

    return {"is_alcohol": False, "is_high_tariff": False, "label": "Gemini判定エラー"}

def judge_similarity_with_llm(ebay_img_url, scraped_items):
    """
    OpenRouter (Gemma 3) のVision機能を用いて、
    eBay元画像とスクレイピングしたメルカリ画像を直接比較し、
    同一商品の可能性を0〜100のスコアで返す。
    """
    if not OPENROUTER_API_KEY:
        print("WARNING: OPENROUTER_API_KEYが設定されていないため、LLM画像判定をスキップします。")
        return scraped_items
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/eBayResearchSystem",
        "X-Title": "eBay Research System"
    }

    results = []
    # 最大5件に制限
    items_to_judge = scraped_items[:5]
    print(f"\n[*] LLM (Gemma 3) を用いた画像比較を開始します（合計 {len(items_to_judge)}件）...")

    for item in items_to_judge:
        # 20 RPM (1分間に20回) 制限のため3秒待機
        import time
        time.sleep(3)
        
        mercari_img_url = item.get("img_url")
        if not mercari_img_url:
            item["score"] = 0
            results.append(item)
            continue
            
        # Gemma 3 (Google AI Studio) は system ロールをサポートしていない場合があるため、user に統合する
        combined_prompt = (
            "あなたはプロの真贋鑑定士・ECリサーチャーです。以下の指示に従って2つの画像を比較してください。\n\n"
            "【指示】\n"
            "2つの商品画像（画像1: eBayの元画像、画像2: Web検索の候補画像）を比較し、これらが「完全に同一の型番・モデルの商品」である確率を、0から100の数値（スコア）で判定してください。\n"
            "【注意点】\n"
            "- 背景、照明、撮影角度、付属品や箱の有無などは無視してください。\n"
            "- 本体の形状、テクスチャ、ロゴの配置、文字盤のデザインなど「物理的な製品特徴」が一致しているかを厳格に見てください。\n"
            "- 同一製品の別カラーバリエーションは「同一モデル」ではないため、低いスコア（0〜10点）にしてください。\n\n"
            "出力は必ずスコアの数値（例：85）のみとし、文章や理由は一切含めないでください。\n\n"
            "画像1（eBayの元画像）と画像2（候補画像）を比較し、同じ商品モデルである確率のスコアを出力せよ。"
        )
        
        payload = {
            "model": "google/gemma-3-27b-it:free",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": combined_prompt},
                        {"type": "image_url", "image_url": {"url": ebay_img_url}},
                        {"type": "image_url", "image_url": {"url": mercari_img_url}}
                    ]
                }
            ],
            "temperature": 0.0
        }

        try:
            print(f"  -> 画像比較中: {mercari_img_url[:60]}...")
            
            score = 0
            for attempt in range(3):
                response = requests.post(url, headers=headers, json=payload)
                if response.status_code == 200:
                    result_data = response.json()
                    if "choices" in result_data and len(result_data["choices"]) > 0:
                        if attempt > 0:
                            print(f"     [*] リトライに成功しました。")
                        content = result_data["choices"][0]["message"]["content"].strip()
                        # 数字のみを抽出
                        match = re.search(r'\d+', content)
                        if match:
                            score = int(match.group())
                        break
                elif response.status_code == 429:
                    print(f"     [!] Rate limit (429) 検出。Gemini API でリトライします...")
                    score = judge_similarity_with_gemini(ebay_img_url, mercari_img_url, combined_prompt)
                    break
                else:
                    print(f"     [!] エラー: {response.status_code} - {response.text}")
                    break
                
        except Exception as e:
            print(f"     [!] リクエスト失敗: {e}")
            score = 0

        item["score"] = score
        results.append(item)

    # スコアの降順でソート
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results

def judge_similarity_with_gemini(ebay_img_url, mercari_img_url, prompt):
    """画像類似度判定のGeminiフォールバック"""
    if not GEMINI_API_KEY: return 0
    try:
        # 1. 2つの画像をダウンロード
        resp1 = requests.get(ebay_img_url, timeout=10)
        resp2 = requests.get(mercari_img_url, timeout=10)
        if resp1.status_code != 200 or resp2.status_code != 200: return 0
        
        data1 = base64.b64encode(resp1.content).decode('utf-8')
        data2 = base64.b64encode(resp2.content).decode('utf-8')
        mime1 = resp1.headers.get('Content-Type', 'image/jpeg')
        mime2 = resp2.headers.get('Content-Type', 'image/jpeg')

        model = "gemini-3.1-flash-lite-preview"
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime1, "data": data1}},
                    {"inline_data": {"mime_type": mime2, "data": data2}}
                ]
            }]
        }
        resp = requests.post(api_url, json=payload, timeout=20)
        if resp.status_code == 200:
            text = resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            match = re.search(r'\d+', text)
            return int(match.group()) if match else 0
    except Exception as e:
        print(f"    [!] Gemini類似度判定失敗: {e}")
    return 0

def verify_model_match(ref_img_url, candidate_img_url, model_number):
    """
    LLM (Gemma 3 Vision) を用いて、2つの商品画像が同一型番かどうかを判定する。
    DINOv2パス後の最終フィルターとして使用。

    Returns:
        True  → 同一型番と判定（採用）
        False → 別型番と判定（REJECT）
    """
    if not OPENROUTER_API_KEY:
        print("    [WARN] OPENROUTER_API_KEY未設定のためLLM型番判定をスキップ（通過扱い）")
        return True

    prompt = (
        f"あなたはプロの時計・商品鑑定士です。以下の2枚の画像を比較してください。\n\n"
        f"【確認する型番】: {model_number}\n\n"
        "【判定基準】\n"
        "- 画像1（参照商品）と画像2（候補商品）が、完全に同じ型番・モデルであるかを判定してください。\n"
        "- ブランドロゴ、文字盤デザイン、ベゼル形状、バンドの素材・色、インデックス配置など物理的特徴を細かく比較してください。\n"
        "- カラーバリエーション違い（例：黒×赤 vs 黒×青）は「別型番」として扱ってください。\n"
        "- 撮影角度・背景・付属品の有無は無視してください。\n\n"
        "【出力形式】\n"
        "以下のJSON形式のみで出力してください（説明不要）。\n"
        "{\"match\": true/false, \"reason\": \"判定理由を一言で\"}"
    )

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "google/gemma-3-27b-it:free",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": ref_img_url}},
                {"type": "image_url", "image_url": {"url": candidate_img_url}}
            ]
        }],
        "temperature": 0.0,
        "response_format": {"type": "json_object"}
    }

    try:
        print(f"    [LLM] 型番一致判定中: {model_number}...")
        resp = requests.post(url, headers=headers, json=payload, timeout=20)

        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"].strip()
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                result = data.get("match", False)
                reason = data.get("reason", "不明")
                label = "✅ 一致" if result else "❌ 不一致"
                print(f"    [LLM] {label} | 理由: {reason}")
                return bool(result)

        elif resp.status_code == 429:
            print(f"    [LLM] OpenRouter 429 → Gemini APIでリトライ...")
            return _verify_model_match_with_gemini(ref_img_url, candidate_img_url, prompt)

        else:
            print(f"    [LLM] エラー ({resp.status_code}) → 通過扱いにします")
            return True

    except Exception as e:
        print(f"    [LLM] 型番判定例外: {e} → 通過扱いにします")
        return True


def _verify_model_match_with_gemini(ref_img_url, candidate_img_url, prompt):
    """verify_model_matchのGeminiフォールバック"""
    if not GEMINI_API_KEY:
        return True
    try:
        resp1 = requests.get(ref_img_url, timeout=10)
        resp2 = requests.get(candidate_img_url, timeout=10)
        if resp1.status_code != 200 or resp2.status_code != 200:
            return True

        data1 = base64.b64encode(resp1.content).decode('utf-8')
        data2 = base64.b64encode(resp2.content).decode('utf-8')
        mime1 = resp1.headers.get('Content-Type', 'image/jpeg')
        mime2 = resp2.headers.get('Content-Type', 'image/jpeg')

        model = "gemini-3.1-flash-lite-preview"
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime1, "data": data1}},
                    {"inline_data": {"mime_type": mime2, "data": data2}}
                ]
            }]
        }
        resp = requests.post(api_url, json=payload, timeout=20)
        if resp.status_code == 200:
            text = resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                result = data.get("match", False)
                reason = data.get("reason", "不明")
                label = "✅ 一致" if result else "❌ 不一致"
                print(f"    [LLM/Gemini] {label} | 理由: {reason}")
                return bool(result)
    except Exception as e:
        print(f"    [LLM/Gemini] フォールバック失敗: {e}")
    return True
