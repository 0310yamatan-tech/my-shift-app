import streamlit as st
import pulp
import pandas as pd
import json
import os
import jpholiday
from datetime import datetime, timedelta

st.set_page_config(page_title="自動シフト作成アプリ (完全修復版)", layout="wide")
st.title("📅 自動シフト作成アプリ (全機能統合・完全版)")
st.write("スタッフから出勤者を選び、4週間分のシフトを自動作成します。祝日自動休み、希望休、曜日固定、事前バリデーション、段階的緩和、手動入れ替えのすべてが搭載されています。")

import tempfile
BALANCE_FILE = os.path.join(tempfile.gettempdir(), "shift_balance.json")

# ------------------------------------------------------------------
# 0. 繰越残高（バランス）の読み書き
# ------------------------------------------------------------------
def load_balance():
    if os.path.exists(BALANCE_FILE):
        with open(BALANCE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("balance", {}), data.get("last_updated", None)
    return {}, None

def save_balance(balance: dict):
    with open(BALANCE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"balance": balance, "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
            f, ensure_ascii=False, indent=2
        )

def reset_balance():
    if os.path.exists(BALANCE_FILE):
        os.remove(BALANCE_FILE)

# ------------------------------------------------------------------
# 1. 基本設定
# ------------------------------------------------------------------
st.subheader("⚙️ 基本設定")

col1, col2, col_date = st.columns(3)
with col1:
    include_weekend = st.checkbox("土日を含める", value=False)
with col2:
    n_staff = st.number_input("スタッフ人数", min_value=2, max_value=20, value=6, step=1)
with col_date:
    today = datetime.today()
    start_monday = today - timedelta(days=today.weekday())
    start_date = st.date_input("シフトを開始する月曜日", value=start_monday)
    if start_date.weekday() != 0:
        st.warning("⚠️ 月曜日を選択してください。シフト計算がズレる原因になります。")

# --- 日付リストの生成と祝日の自動判定 ---
DAYS_BASE_WEEK = ["月", "火", "水", "木", "金", "土", "日"] if include_weekend else ["月", "火", "水", "木", "金"]
N_WEEKS = 4

DAYS = []          
DAY_TO_DATE = {}   
HOLIDAYS = set()   

current_day = start_date
for w in range(1, N_WEEKS + 1):
    for d_name in ["月", "火", "水", "木", "金", "土", "日"]:
        if d_name in DAYS_BASE_WEEK:
            day_key = f"{w}週目_{d_name}({current_day.strftime('%m/%d')})"
            DAYS.append(day_key)
            DAY_TO_DATE[day_key] = current_day
            if jpholiday.is_holiday(current_day):
                HOLIDAYS.add(day_key)
        current_day += timedelta(days=1)

st.info(f"💡 選択された期間内の祝日(自動休み): {', '.join([k for k in HOLIDAYS]) if HOLIDAYS else 'なし'}")

col3, col4 = st.columns(2)
with col3:
    n_a = st.number_input("A（複数人作業）の人数", min_value=1, max_value=int(n_staff), value=3, step=1)
with col4:
    n_b = st.number_input("B（少人数作業）の人数", min_value=0, max_value=int(n_staff), value=1, step=1)

n_work = n_a + n_b
if n_work > n_staff:
    st.error(f"⚠️ A+B の人数（{n_work}人）がスタッフ総数（{n_staff}人）を超えています。")
    st.stop()
n_off = n_staff - n_work

active_days_count = len(DAYS) - len(HOLIDAYS)
total_workdays = n_work * active_days_count

if n_staff > 0:
    base, rem = divmod(total_workdays, n_staff)
    st.caption(f"1日あたり（平日）: A={n_a}人 / B={n_b}人 / 休み={n_off}人")
    if rem == 0:
        st.info(f"💡 祝日を除いた稼働日（{active_days_count}日）では、全員がちょうど **{base}日** ずつ出勤すれば完全に均等になります。")
    else:
        st.info(
            f"💡 今回の出勤枠は合計 **{total_workdays}人日**、スタッフは **{n_staff}人** なので、"
            f"{n_staff - rem}人が **{base}日**、{rem}人が **{base + 1}日** の出勤となり、今回だけを見れば1日差が生まれます。"
            f"残高調整により、回数を重ねることで均等に近づきます。"
        )

# ------------------------------------------------------------------
# 2. スタッフ設定
# ------------------------------------------------------------------
st.subheader("👥 スタッフの設定")

members = []
name_slots = st.columns(2)
for i in range(int(n_staff)):
    with name_slots[i % 2]:
        name = st.text_input(f"スタッフ {i+1}", value=f"スタッフ{i+1}", key=f"member_{i}")
        members.append(name.strip())

if len(members) != len(set(members)):
    st.error("⚠️ 名前が重複しています。名前は全員分ユニークにしてください。")
    st.stop()
if any(m == "" for m in members):
    st.error("⚠️ 空欄の名前があります。全員分入力してください。")
    st.stop()

# --- 📌 曜日固定枠の設定 ---
st.subheader("📌 曜日固定の設定 (任意)")
fixed_rules = {m: {} for m in members}
with st.expander("各スタッフの曜日固定を設定する", expanded=False):
    for m in members:
        st.write(f"**{m} の固定ルール**")
        cols = st.columns(len(DAYS_BASE_WEEK))
        for d, c in zip(DAYS_BASE_WEEK, cols):
            with c:
                choice = st.selectbox(f"{d}曜日", options=["指定なし", "🔴 A固定", "🔵 B固定", "休 固定"], key=f"fix_{m}_{d}")
                if choice == "🔴 A固定": fixed_rules[m][d] = "A"
                elif choice == "🔵 B固定": fixed_rules[m][d] = "B"
                elif choice == "休 固定": fixed_rules[m][d] = "Off"

# --- 🙅 希望休の設定 ---
st.subheader("🙅 希望休（任意）")
preferred_off = {m: set() for m in members}
with st.expander("特定の曜日をすべて希望休にする（上記で『休 固定』にした場合はチェック不要です）", expanded=False):
    for m in members:
        st.write(f"**{m}**")
        cols = st.columns(len(DAYS_BASE_WEEK))
        for d, c in zip(DAYS_BASE_WEEK, cols):
            with c:
                if st.checkbox(d, key=f"off_{m}_{d}"):
                    for d_key in DAYS:
                        # 日付文字列から曜日部分（例: "月"）を抽出して比較
                        weekday_part = d_key.split("_")[1].split("(")[0]
                        if weekday_part == d:
                            preferred_off[m].add(d_key)

max_consecutive = st.slider("連続勤務の上限（日）", min_value=1, max_value=7, value=4)

# 連勤判定用の週ブロック
days_per_week = len(DAYS_BASE_WEEK)
blocks = [DAYS[i*days_per_week:(i+1)*days_per_week] for i in range(N_WEEKS)]

# 繰越残高の表示
st.subheader("📊 繰越残高")
raw_balance, last_updated = load_balance()
balance = {m: float(raw_balance.get(m, 0.0)) for m in members}
bal_df = pd.DataFrame([{"名前": m, "繰越残高（日）": round(balance[m], 2)} for m in members]).set_index("名前")
st.dataframe(bal_df, use_container_width=True)

if st.button("🗑️ 繰越残高をリセットする"):
    reset_balance()
    st.success("リセットしました。ページを再読み込みしてください。")
    st.stop()

# ------------------------------------------------------------------
# 2.6 事前バリデーション
# ------------------------------------------------------------------
def validate_fixed_rules(members, days_base, fixed_rules, n_a, n_b, n_off, max_consecutive, blocks):
    errors = []
    for d in days_base:
        fixed_a = [m for m in members if fixed_rules[m].get(d) == "A"]
        fixed_b = [m for m in members if fixed_rules[m].get(d) == "B"]
        fixed_off = [m for m in members if fixed_rules[m].get(d) == "Off"]

        if len(fixed_a) > n_a:
            errors.append(f"「{d}曜日」の🔴A固定が **{len(fixed_a)}人** 指定されていますが、A枠は **{n_a}人** までです。")
        if len(fixed_b) > n_b:
            errors.append(f"「{d}曜日」の🔵B固定が **{len(fixed_b)}人** 指定されていますが、B枠は **{n_b}人** までです。")
        if len(fixed_off) > n_off:
            errors.append(f"「
