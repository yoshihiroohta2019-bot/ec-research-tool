import streamlit as st
import pandas as pd
from apify_client import ApifyClient
import io
import time
import re

st.set_page_config(page_title="EC市場リサーチ自動化ツール", layout="wide")

BESTSELLERS_ACTOR = "automation-lab/amazon-bestsellers-scraper"
REVIEWS_ACTOR     = "automation-lab/amazon-reviews-scraper"
DETAILS_ACTOR     = "apify/amazon-product-scraper"

try:
    apify_token = st.secrets["APIFY_TOKEN"]
except Exception:
    apify_token = None

st.title("📦 EC市場リサーチ自動化ツール")
st.markdown("AmazonのランキングURLを入力すると、上位20件の競合データ＋レビューを自動抽出してExcelで出力します。")

url_input = st.text_input(
    "AmazonのランキングURLを入力してください",
    placeholder="https://www.amazon.co.jp/gp/bestsellers/beauty/"
)

start_button = st.button("🚀 リサーチ開始", type="primary")

def extract_asin(url):
    match = re.search(r'/dp/([A-Z0-9]{10})', url)
    return match.group(1) if match else None

def format_price(price_string):
    if not price_string:
        return None
    cleaned = re.sub(r'[^\d]', '', str(price_string))
    return int(cleaned) if cleaned else None

def process_reviews(reviews):
    if not reviews:
        return ""
    good = [r for r in reviews if r.get('rating', 0) in (4, 5)][:3]
    bad  = [r for r in reviews if 1 <= r.get('rating', 0) <= 3][:2]
    result = []
    for r in good:
        body = str(r.get('body', ''))[:150]
        result.append(f"【好評】・{body} (星{r.get('rating')})")
    for r in bad:
        body = str(r.get('body', ''))[:150]
        result.append(f"【不満】・{body} (星{r.get('rating')})")
    return "\n".join(result)

def extract_features(details):
    features = details.get('features') or details.get('bullets') or details.get('description') or []
    if isinstance(features, list):
        return '\n'.join(str(f) for f in features)[:500]
    return str(features)[:500] if features else ""

def build_excel(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='リサーチ結果')
    return output.getvalue()

if start_button:
    if not apify_token:
        st.error("APIトークンが設定されていません。管理者に連絡してください。")
    elif not url_input:
        st.warning("URLを入力してください。")
    elif not url_input.startswith("https://"):
        st.warning("URLは https:// から始まる形式で入力してください。")
    else:
        client = ApifyClient(apify_token)

        # STEP 1: ランキング取得
        step1 = st.empty()
        step1.info("⏳ STEP 1/3：ランキングデータを取得中...")

        try:
            run1 = client.actor(BESTSELLERS_ACTOR).call(run_input={
                "categoryUrls": [url_input],
                "amazonMarketplace": "JP",
                "marketplace": "JP",
                "maxItemsPerCategory": 20,
            })
            ranking_items = list(client.dataset(run1["defaultDatasetId"]).list_items().items)[:20]
        except Exception as e:
            step1.error(f"ランキング取得エラー：{e}")
            st.stop()

        if not ranking_items:
            step1.error("ランキングデータが取得できませんでした。URLを確認してください。")
            st.stop()

        step1.success(f"✅ STEP 1完了：{len(ranking_items)}件のランキングデータを取得しました。")

        # ASINリスト作成
        asin_list = [
            item.get('asin') or extract_asin(item.get('url', ''))
            for item in ranking_items
        ]
        asin_list = [a for a in asin_list if a]

        # STEP 2 & 3: レビューと商品詳細を並列起動
        step2 = st.empty()
        step2.info("⏳ STEP 2/3：レビューデータを取得中...（1〜2分かかります）")

        step3 = st.empty()
        step3.info("⏳ STEP 3/3：商品詳細データを取得中...")

        # レビュー並列起動
        pending_reviews = {}
        for asin in asin_list:
            try:
                run = client.actor(REVIEWS_ACTOR).start(run_input={
                    "asins": [asin],
                    "marketplace": "JP",
                    "maxReviewsPerProduct": 20,
                })
                pending_reviews[asin] = run["id"]
            except Exception:
                pass

        # 商品詳細並列起動
        pending_details = {}
        for asin in asin_list:
            try:
                run = client.actor(DETAILS_ACTOR).start(run_input={
                    "startUrls": [{"url": f"https://www.amazon.co.jp/dp/{asin}"}],
                })
                pending_details[asin] = run["id"]
            except Exception:
                pass

        # レビュー完了待ち
        reviews_by_asin = {}
        for asin, run_id in pending_reviews.items():
            try:
                finished = client.run(run_id).wait_for_finish()
                items = list(client.dataset(finished["defaultDatasetId"]).list_items().items)
                if items:
                    reviews_by_asin[asin] = items
            except Exception:
                pass

        step2.success(f"✅ STEP 2完了：{len(reviews_by_asin)}/{len(asin_list)}商品のレビューを取得しました。")

        # 商品詳細完了待ち
        details_by_asin = {}
        for asin, run_id in pending_details.items():
            try:
                finished = client.run(run_id).wait_for_finish()
                items = list(client.dataset(finished["defaultDatasetId"]).list_items().items)
                if items:
                    details_by_asin[asin] = items[0]
            except Exception:
                pass

        step3.success(f"✅ STEP 3完了：{len(details_by_asin)}/{len(asin_list)}商品の詳細データを取得しました。")

        # データ結合
        rows = []
        success_count = 0

        for item in ranking_items:
            url   = item.get('url', '')
            url   = re.sub(r'amazon\.[a-z.]+/dp', 'amazon.co.jp/dp', url) if url else ''
            asin  = item.get('asin') or extract_asin(url) or ""
            title = item.get('name', '')
            備考  = []

            reviews      = reviews_by_asin.get(asin, [])
            reviews_text = process_reviews(reviews)
            details      = details_by_asin.get(asin, {})
            description  = extract_features(details)

            if not title:        備考.append("商品名取得失敗")
            if not reviews_text: 備考.append("レビュー取得失敗")
            if not description:  備考.append("商品詳細取得失敗")
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
                "商品の特徴（仕様）":   description,
                "顧客の声（レビュー）": reviews_text,
                "備考（ステータス）":   " / ".join(備考),
            })

        df = pd.DataFrame(rows)
        st.session_state['result_df'] = df

        total = len(ranking_items)
        if success_count == total:
            st.success(f"✅ {total}件中 {success_count}件のデータ取得に成功しました。")
        else:
            st.warning(f"⚠️ {total}件中 {success_count}件のデータ取得に成功しました。")

        st.subheader("📊 取得結果プレビュー（上位10件）")
        st.dataframe(df.head(10), use_container_width=True)

        st.download_button(
            label="📥 Excelファイルをダウンロード",
            data=build_excel(df),
            file_name=f"amazon_research_{int(time.time())}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

if 'result_df' in st.session_state and not start_button:
    df = st.session_state['result_df']
    st.info("💾 直前のリサーチ結果を表示しています。")
    st.dataframe(df.head(10), use_container_width=True)
    st.download_button(
        label="📥 Excelファイルをダウンロード（保持中）",
        data=build_excel(df),
        file_name=f"amazon_research_{int(time.time())}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

st.divider()
st.caption("© 2026 ECリサーチ自動化ツール フェーズ1")
