# eBay/Mercari Research Tool

eBayとメルカリの価格差を調査し、無在庫転売の検知やリサーチを効率化するツールです。
AI（CLIP/DINOv2）による画像類似度判定機能を備えています。

## 機能
- eBay商品検索
- メルカリ商品検索・スクレイピング
- 画像類似度判定（CUDA/GPU対応）
- 商品名・説明文のAI分析

## セットアップ
1. リポジトリをクローン
2. 依存関係のインストール: `pip install -r requirements.txt`
3. `.env.example` を `.env` にコピーし、APIキーを設定
4. `python main.py` を実行
