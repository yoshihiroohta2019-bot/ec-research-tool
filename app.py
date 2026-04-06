import streamlit as st
import pandas as pd
from apify_client import ApifyClient
import io
import time

st.set_page_config(page_title="ECリサーチ自動化ツール", layout="wide")

apify_token = st.secrets["APIFY_TOKEN"]
actor_id = "XVDTQc4a7MDTqSTMJ"

st.title("📦 EC市場リサーチ自動化ツール")
st.markdown("AmazonのURLを入力すると、上位20件の商品データとレビューを自動抽出します。")

with st.container():
    url_input = st.text_input("AmazonのランキングURL または 検索結果URLを入力してください", 
                             placeholder="https://www.amazon.co.jp/s?k=シャンプー")
    col1, col2 = st.columns([1, 4])
    with col1:
        start_button = st.button("🚀 リサーチ開始", type="primary")

def process_reviews(reviews_data):
    if not reviews_data or not isinstance(reviews_data, list):
        return "レビューデータなし"
    good_reviews = [r for r in reviews_data if r.get('stars', 0) >= 4][:3]
    bad_reviews = [r for r in reviews_data if r.get('stars', 0) <= 3][:2]
    result = []
    for r in good_reviews:
        result.append(f"【好評】・{r.get('text', '')[:150]} (星{r.get('stars')})")
    for r in bad_reviews:
        result.append(f"【不満】・{r.get('text', '')[:150]} (星{r.get('stars')})")
    return "\n".join(result)

if start_button:
    if not apify_token:
        st.error("Apify APIトークンをサイドバーに入力してください。")
    elif not url_input:
        st.warning("URLを入力してください。")
    else:
        client = ApifyClient(apify_token)
        with st.status("データを抽出中... (1〜3分ほどかかります)", expanded=True) as status:
            try:
                run_input = {
                    "categoryUrls": [{"url": url_input}],
                    "maxItems": 20,
                    "proxyConfiguration": {"useApifyProxy": True},
                    "scrapeProductDetails": True,
                    "scrapeReviews": True,
                    "maxReviews": 20
                }
                st.write("Apifyを実行しています...")
                run = client.actor(actor_id).call(run_input=run_input)
                st.write("結果を取得しています...")
                dataset_items = list(client.dataset(run["defaultDatasetId"]).list_items().items)
                if not dataset_items:
                    st.error("データが取得できませんでした。URLが正しいか確認してください。")
                else:
                    rows = []
                    for i, item in enumerate(dataset_items):
                        row = {
                            "カテゴリ順位": i + 1,
                            "商品名": item.get("title"),
                            "価格（円）": item.get("price", {}).get("value") if isinstance(item.get("price"), dict) else item.get("price"),
                            "ブランド": item.get("brand"),
                            "評価": item.get("stars"),
                            "レビュー件数": item.get("reviewsCount"),
                            "カテゴリ": item.get("breadCrumbs"),
                            "顧客の声（厳選レビュー）": process_reviews(item.get("reviews")),
                            "画像URL": item.get("thumbnailImage"),
                            "商品URL": item.get("url"),
                            "備考": "" if item.get("title") else "データ取得失敗"
                        }
                        rows.append(row)
                    df = pd.DataFrame(rows)
                    st.success(f"完了！ {len(df)} 件のデータを取得しました。")
                    st.dataframe(df.head(10))
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='openpyxl') as writer:
                        df.to_excel(writer, index=False, sheet_name='リサーチ結果')
                    st.download_button(
                        label="📥 Excelファイルをダウンロード",
                        data=output.getvalue(),
                        file_name=f"amazon_research_{int(time.time())}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                status.update(label="リサーチ完了！", state="complete", expanded=False)
            except Exception as e:
                st.error(f"エラーが発生しました: {e}")
                status.update(label="エラー終了", state="error")

st.divider()
st.caption("© 2026 ECリサーチ自動化ツール フェーズ1")
