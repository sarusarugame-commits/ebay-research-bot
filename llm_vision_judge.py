import requests
import json
import re
from config import OPENROUTER_API_KEY

def estimate_weight_with_llm(ebay_img_url, dimensions):
    """
    Gemma 3 (Vision) を用いて、商品の見た目とサイズ情報から重量(kg)を推論する。
    梱包バッファ(0.5kg)を自動的に加算して返す。
    """
    if not OPENROUTER_API_KEY:
        return "不明"
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    prompt = (
        f"あなたはプロの物流・梱包エキスパートです。添付された商品の画像と、判明しているサイズ情報（{dimensions}）を元に、この商品の重量を推論してください。\n\n"
        "【推論のステップ】\n"
        "1. 画像から商品の材質（金属、プラスチック、ガラス等）と密度を推測する。\n"
        f"2. サイズ（{dimensions}）から体積を計算し、中身の正味重量（Net Weight）をkg単位で算出する。\n"
        "3. 国際発送用の梱包材（ダンボール、緩衝材）の重さとして、さらに 0.5kg を加算する。\n\n"
        "【出力形式】\n"
        "最終的な『梱包込みの総重量』を、単位(kg)を付けて数値のみで出力してください（例：1.2kg）。文章は一切不要です。"
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
        "temperature": 0.0
    }

    try:
        print(f"[*] Gemma 3 が画像とサイズから重量を推論しています...")
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            content = response.json()["choices"][0]["message"]["content"].strip()
            # 1.2kg などの形式を抽出
            match = re.search(r"(\d+(\.\d+)?)\s?kg", content, re.I)
            if match:
                return match.group(0)
            return content
    except Exception as e:
        print(f"[Error] 重量推論失敗: {e}")
    
    return "不明"

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
        "1. 【アルコール飲料か？】ビール、ワイン、ウィスキー、日本酒などのアルコール類であれば 'true'、そうでなければ 'false'。\n"
        "2. 【高関税素材か？】商品の主要な材質に '鉄・鋼鉄（Iron/Steel）' または '革（Leather）' が含まれるか？ 含まれるなら 'true'、そうでなければ 'false'。\n\n"
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
    except Exception as e:
        print(f"     [!] 安全性チェック失敗: {e}")
    
    return {"is_alcohol": False, "is_high_tariff": False, "label": "判定エラー"}

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
                    print(f"     [!] Rate limit (429) 検出。5秒待機してリトライします... (試行 {attempt+1}/3)")
                    time.sleep(5)
                    continue
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

def verify_specs_with_llm(ebay_img_url, current_weight, current_dims):
    """
    Gemma 3 (Vision) を用いて、最終的なサイズと重量が画像から見て妥当か判定・調整する。
    """
    if not OPENROUTER_API_KEY:
        return current_weight, current_dims
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    prompt = (
        "あなたはプロの物流・梱包エキスパートです。添付された商品の画像と、現在算出されているサイズ・重量情報を確認してください。\n\n"
        f"【現在のデータ】\n"
        f"- 重量: {current_weight}\n"
        f"- サイズ: {current_dims}\n\n"
        "【指示】\n"
        "1. 梱包（箱、緩衝材）を含めた場合、画像から判断して上記の数値が妥当か判定してください。\n"
        "2. もし数値が「不明」であったり、明らかに足りない、または不自然な場合は、画像から推測して適切な値に調整してください。\n"
        "3. 数値が妥当な場合は、そのままの値を返してください。\n\n"
        "【出力形式】\n"
        "以下のJSON形式でのみ出力してください（文章は一切不要です）。\n"
        "{\"weight\": \"〇〇kgまたは〇〇g\", \"dimensions\": \"縦x横x高 単位\"}"
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
        print(f"[*] Gemma 3 が画像から最終的なスペック（梱包込）を検証・調整中...")
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        if response.status_code == 200:
            content = response.json()["choices"][0]["message"]["content"].strip()
            data = json.loads(content)
            return data.get("weight", current_weight), data.get("dimensions", current_dims)
    except Exception as e:
        print(f"     [!] スペック検証失敗: {e}")
    
    return current_weight, current_dims
