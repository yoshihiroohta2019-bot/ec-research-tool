import streamlit as st
import pandas as pd
from apify_client import ApifyClient
import io
import time
import re

# --- 設定 ---
st.set_page_config(page_title="EC市場リサーチ自動化ツール", layout="wide")

BESTSELLERS_ACTOR = "automation-lab/amazon-bestsellers-scraper"
REVIEWS_ACTOR     = "automation-lab/amazon-reviews-scraper"

# --- APIトークン読み込み ---
try:
    apify_token = st.secrets["APIFY_TOKEN"]
except Exception:
    apify_token = None

# --- タイトル ---
st.title("📦 EC市場リサーチ自動化ツール")
st.markdown("AmazonのランキングURLを入力すると、上位20件の競合データ＋レビューを自動抽出してExcelで出力します。")

# --- URL入力 ---
url_input = st.text_input(
    "AmazonのランキングURLを入力してください",
    placeholder="https://www.amazon.co.jp/gp/bestsellers/beauty/"
)

start_button = st.button("🚀 リサーチ開始", type="primary")

# --- ASINの抽出 ---
def extract_asin(url):
    match = re.search(r'/dp/([A-Z0-9]{10})', url)
    return match.group(1) if match else None

# --- 価格の整形 ---
def format_price(price_string):
    if not price_string:
        return ""
    cleaned = re.sub(r'[^\d]', '', str(price_string))
    return int(cleaned) if cleaned else ""

# --- レビュー抽出ロジック ---
def process_reviews(reviews):
    if not reviews:
        return ""
    good = [r for r in reviews if r.get('rating', 0) >= 4][:3]
    bad  = [r for r in reviews if r.get('rating', 0) <= 3][:2]
    result = []
    for r in good:
        body = str(r.get('body', ''))[:150]
        result.append(f"【好評】・{body} (星{r.get('rating')})")
    for r in bad:
        body = str(r.get('body', ''))[:150]
        result.append(f"【不満】・{body} (星{r.get('rating')})")
    return "\n".join(result)

# --- メイン処理 ---
if start_button:
    if not apify_token:
        st.error("APIトークンが設定されていません。管理者に連絡してください。")
    elif not url_input:
        st.warning("URLを入力してください。")
    elif not url_input.startswith("https://"):
        st.warning("URLは https:// から始まる形式で入力してください。")
    else:
        client = ApifyClient(apify_token)

        step1 = st.empty()
        step1.info("⏳ STEP 1/2：ランキングデータを取得中...")

        try:
            run1 = client.actor(BESTSELLERS_ACTOR).call(run_input={
               "categoryUrls": [url_input],
                "amazonMarketplace": "JP",
                "maxItemsPerCategory": 20,
            })
            ranking_items = list(client.dataset(run1["defaultDatasetId"]).list_items().items)
        except Exception as e:
            step1.error(f"ランキング取得エラー：{e}")
            st.stop()

        if not ranking_items:
            step1.error("ランキングデータが取得できませんでした。URLを確認してください。")
            st.stop()

        step1.success(f"✅ STEP 1完了：{len(ranking_items)}件のランキングデータを取得しました。")

        step2 = st.empty()
        step2.info("⏳ STEP 2/2：各商品のレビューを取得中...（1〜3分かかります）")

        asin_list = []
        for item in ranking_items:
            asin = extract_asin(item.get('url', ''))
            if asin:
                asin_list.append(asin)

        reviews_by_asin = {}
        if asin_list:
            try:
                run2 = client.actor(REVIEWS_ACTOR).call(run_input={
                    "asins": asin_list,
                    "marketplace": "JP",
                    "maxReviewsPerProduct": 20,
                    "sort": "helpful",
                })
                review_items = list(client.dataset(run2["defaultDatasetId"]).list_items().items)
                for r in review_items:
                    asin = r.get('asin', '')
                    if asin not in reviews_by_asin:
                        reviews_by_asin[asin] = []
                    reviews_by_asin[asin].append(r)
            except Exception as e:
                step2.warning(f"⚠️ レビュー取得でエラーが発生しました（レビューなしで続行）：{e}")

        step2.success(f"✅ STEP 2完了：{len(reviews_by_asin)}商品のレビューを取得しました。")

        rows = []
        success_count = 0

        for item in ranking_items:
            url   = item.get('url', '')
            asin  = extract_asin(url) or ""
            title = item.get('name', '')
            備考  = []

            reviews = reviews_by_asin.get(asin, [])
            reviews_text = process_reviews(reviews)

            if not title:        備考.append("商品名取得失敗")
            if not reviews_text: 備考.append("レビュー取得失敗")
            if title:
                success_count += 1

            rows.append({
                "カテゴリ順位":         item.get('rank', ''),
                "商品名":              title,
                "ASIN":               asin,
                "商品URL":             url,
                "価格（円）":           format_price(item.get('priceString', '')),
                "メイン画像URL":        item.get('thumbnail', ''),
                "平均星評価":           item.get('rating', ''),
                "総レビュー数":         item.get('reviewCount', ''),
                "カテゴリ":             item.get('categoryName', ''),
                "顧客の声（レビュー）": reviews_text,
                "備考（ステータス）":   " / ".join(備考),
            })

        df = pd.DataFrame(rows)

        total = len(ranking_items)
        if success_count == total:
            st.success(f"✅ {total}件中 {success_count}件のデータ取得に成功しました。")
        else:
            st.warning(f"⚠️ {total}件中 {success_count}件のデータ取得に成功しました。")

        st.subheader("📊 取得結果プレビュー（上位10件）")
        st.dataframe(df.head(10), use_container_width=True)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='リサーチ結果')

        st.download_button(
            label="📥 Excelファイルをダウンロード",
            data=output.getvalue(),
            file_name=f"amazon_research_{int(time.time())}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

st.divider()
st.caption("© 2026 ECリサーチ自動化ツール フェーズ1")
