import streamlit as st
import pulp
import pandas as pd
import json
import os
import jpholiday
from datetime import datetime, timedelta

st.set_page_config(page_title="自動シフト作成アプリ", layout="wide")
st.title("📅 自動シフト作成アプリ")
st.write("スタッフ全員の4週間分のシフトをボタン一つで自動作成します。祝日は自動的に全員『お休み』になります。出来上がったシフトを後から手動で入れ替えることも可能です。")

# ------------------------------------------------------------------
# 0. 【安全版】サーバーの一時フォルダを使ってデータを保存する仕組み
# ------------------------------------------------------------------
import tempfile
BALANCE_FILE = os.path.join(tempfile.gettempdir(), "shift_balance_v7.json")
TABLE_FILE = os.path.join(tempfile.gettempdir(), "shift_table_v7.json")

def load_data_from_server(file_path):
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_data_to_server(file_path, data_dict):
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data_dict, f, ensure_ascii=False, indent=2)
    except:
        pass

# ------------------------------------------------------------------
# 1. 基本設定
# ------------------------------------------------------------------
st.subheader("⚙️ 1. 基本設定（人数や日付の指定）")

col1, col2, col_date = st.columns(3)
with col1:
    include_weekend = st.checkbox("土曜日・日曜日もシフトに含める", value=False)
with col2:
    n_staff = st.number_input("スタッフの合計人数", min_value=2, max_value=20, value=6, step=1)
with col_date:
    today = datetime.today()
    start_monday = today - timedelta(days=today.weekday())
    start_date = st.date_input("シフトを開始する月曜日を選択", value=start_monday)
    if start_date.weekday() != 0:
        st.warning("⚠️ 必ず「月曜日」の日付を選択してください。そうしないとカレンダーがズレてしまいます。")

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

st.info(f"💡 選択された期間の中にある祝日（全員自動でお休みになります）: {', '.join([k for k in HOLIDAYS]) if HOLIDAYS else 'なし'}")

st.markdown("##### 👥 通常の日の配置人数")
col3, col4 = st.columns(2)
with col3:
    n_a = st.number_input("🔴 通常の日の 洗浄エリアに必要な人数", min_value=1, max_value=int(n_staff), value=3, step=1)
with col4:
    n_b = st.number_input("🔵 通常の日の クリーンエリアに必要な人数", min_value=0, max_value=int(n_staff), value=2, step=1)

# 📉 【日付限定】ピンポイントで仕事が少ない（2人休み）日の設定
st.markdown("##### 📉 【日付限定】ピンポイントで仕事が少ない（2人休み）日の設定")
if "two_off_dates" not in st.session_state:
    st.session_state["two_off_dates"] = []

with st.expander("ここをクリックして、仕事が少なく【2人休み】にしたい日付を登録する", expanded=False):
    st.caption("通常の日（出勤5人）より出勤人数が1人減って4人出勤（2人休み）になります。")
    date_options = {DAY_TO_DATE[d]: d for d in DAYS if d not in HOLIDAYS}
    sorted_dates = sorted(list(date_options.keys()))
    
    if sorted_dates:
        selected_target_date = st.selectbox("2人休みにしたい日を選択", options=sorted_dates, format_func=lambda x: x.strftime('%m/%d'), key="two_off_select")
        if st.button("➕ この日付を「2人休み」に指定する"):
            day_key_str = date_options[selected_target_date]
            if day_key_str not in st.session_state["two_off_dates"]:
                st.session_state["two_off_dates"].append(day_key_str)
                st.toast(f"📢 {selected_target_date.strftime('%m/%d')} を2人休みの日に指定しました！")
    
    if st.session_state["two_off_dates"]:
        st.write("**現在指定されている「2人休み」の日付一覧:**")
        for d_str in list(st.session_state["two_off_dates"]):
            if d_str not in DAYS:
                st.session_state["two_off_dates"].remove(d_str)
                continue
            col_d_text, col_d_del = st.columns([4, 1])
            with col_d_text: st.write(f"・{d_str}")
            with col_d_del:
                if st.button("❌ 削除", key=f"del_date_{d_str}"):
                    st.session_state["two_off_dates"].remove(d_str)
                    st.rerun()

# 各日程の必要人数を計算
day_requirements = {}
total_workdays = 0
for d_key in DAYS:
    if d_key in HOLIDAYS:
        day_requirements[d_key] = {"A": 0, "B": 0, "Off": int(n_staff)}
    elif d_key in st.session_state["two_off_dates"]:
        target_a = int(n_a) - 1 if int(n_a) > 1 else int(n_a)
        target_b = int(n_b) if int(n_a) > 1 else max(0, int(n_b) - 1)
        day_requirements[d_key] = {"A": target_a, "B": target_b, "Off": 2}
    else:
        day_requirements[d_key] = {"A": int(n_a), "B": int(n_b), "Off": int(n_staff) - (int(n_a) + int(n_b))}
        
    if day_requirements[d_key]["A"] + day_requirements[d_key]["B"] > n_staff:
        st.error(f"⚠️ エラー：{d_key} の必要人数がスタッフ全体の人数（{n_staff}人）を超えています。")
        st.stop()
    total_workdays += (day_requirements[d_key]["A"] + day_requirements[d_key]["B"])

# ------------------------------------------------------------------
# 2. スタッフ設定 & 希望休（曜日＋ピンポイント日付）
# ------------------------------------------------------------------
st.subheader("👥 2. スタッフの名前入力")

default_names = ["長谷川", "羽田", "遠藤", "大竹", "石井", "澤田"]
members = []
name_slots = st.columns(2)
for i in range(int(n_staff)):
    with name_slots[i % 2]:
        default_val = default_names[i] if i < len(default_names) else f"スタッフ{i+1}"
        name = st.text_input(f"スタッフ {i+1} の名前", value=default_val, key=f"member_{i}")
        members.append(name.strip())

if len(members) != len(set(members)):
    st.error("⚠️ エラー：名前が同じになっている人がいます。")
    st.stop()
if any(m == "" for m in members):
    st.error("⚠️ エラー：名前が空欄になっているところがあります。")
    st.stop()

# --- 📌 曜日固定枠の設定 ---
st.subheader("📌 3. 曜日固定の設定（特定の曜日をいつも同じ役割にする場合のみ設定・任意）")
fixed_rules = {m: {} for m in members}
with st.expander("ここをクリックして各スタッフの曜日固定を設定する", expanded=False):
    for m in members:
        st.write(f"**【{m}】さんの曜日固定ルール**")
        cols = st.columns(len(DAYS_BASE_WEEK))
        for d, c in zip(DAYS_BASE_WEEK, cols):
            with c:
                choice = st.selectbox(f"{d}曜日", options=["指定なし", "🔴 洗浄エリア", "🔵 クリーンエリア", "休 固定"], key=f"fix_{m}_{d}")
                if choice == "🔴 洗浄エリア": fixed_rules[m][d] = "A"
                elif choice == "🔵 クリーンエリア": fixed_rules[m][d] = "B"
                elif choice == "休 固定": fixed_rules[m][d] = "Off"

# --- 🙅 希望休の設定 ---
st.subheader("🙅 4. 希望休の設定")
preferred_off = {m: set() for m in members}

if "individual_off_dates" not in st.session_state:
    st.session_state["individual_off_dates"] = {m: [] for m in default_names + [f"スタッフ{i}" for i in range(1, 21)]}

for m in members:
    if m not in st.session_state["individual_off_dates"]:
        st.session_state["individual_off_dates"][m] = []

with st.expander("ここをクリックして各スタッフの【希望休（曜日・特定の日付）】を設定する", expanded=False):
    for m in members:
        st.markdown(f"#### 👤 【{m}】さんの希望休設定")
        
        st.write("**📅 曜日でお休みを指定する:**")
        cols = st.columns(len(DAYS_BASE_WEEK))
        for d, c in zip(DAYS_BASE_WEEK, cols):
            with c:
                if st.checkbox(f"毎週 {d}曜", key=f"off_w_{m}_{d}"):
                    for d_key in DAYS:
                        weekday_part = d_key.split("_")[1].split("(")[0]
                        if weekday_part == d:
                            preferred_off[m].add(d_key)
        
        st.write("**📌 ピンポイントで特定の日付をお休みにする:**")
        valid_date_options = {DAY_TO_DATE[d]: d for d in DAYS if d not in HOLIDAYS}
        sorted_valid_dates = sorted(list(valid_date_options.keys()))
        
        if sorted_valid_dates:
            col_sel, col_add = st.columns([3, 1])
            with col_sel:
                sel_date = st.selectbox(f"お休みしたい日を選択 ({m}さん)", options=sorted_valid_dates, format_func=lambda x: x.strftime('%m/%d'), key=f"sel_date_{m}")
            with col_add:
                st.write("")
                if st.button("➕ 希望休に追加", key=f"add_date_btn_{m}"):
                    target_day_str = valid_date_options[sel_date]
                    if target_day_str not in st.session_state["individual_off_dates"][m]:
                        st.session_state["individual_off_dates"][m].append(target_day_str)
                        st.toast(f"📢 {m}さんの希望休に {sel_date.strftime('%m/%d')} を追加しました")
        
        m_saved_dates = st.session_state["individual_off_dates"][m]
        current_active_saved = []
        if m_saved_dates:
            st.write(f"*{m}さんの指定済みの希望日:*")
            for d_str in list(m_saved_dates):
                if d_str not in DAYS:
                    st.session_state["individual_off_dates"][m].remove(d_str)
                    continue
                current_active_saved.append(d_str)
                preferred_off[m].add(d_str)
                
            for d_str in current_active_saved:
                c_txt, c_del = st.columns([5, 1])
                with c_txt: st.write(f" ・ {d_str}")
                with c_del:
                    if st.button("🗑️ 取消", key=f"del_indiv_{m}_{d_str}"):
                        st.session_state["individual_off_dates"][m].remove(d_str)
                        st.rerun()
        st.markdown("---")

max_consecutive = st.slider("連続で勤務してよい上限日数", min_value=1, max_value=7, value=4)

days_per_week = len(DAYS_BASE_WEEK)
blocks = [DAYS[i*days_per_week:(i+1)*days_per_week] for i in range(N_WEEKS)]

st.subheader("📊 前回までの出勤数のズレ（繰越残高）")
raw_balance = load_data_from_server(BALANCE_FILE)
balance = {m: float(raw_balance.get(m, 0.0)) for m in members}
bal_df = pd.DataFrame([{"名前": m, "これまでのズレ（日分）": round(balance[m], 2)} for m in members]).set_index("名前")
st.dataframe(bal_df, use_container_width=True)

st.subheader("🗑️ データの消去・リセット操作")
r_col1, r_col2 = st.columns(2)
with r_col1:
    if st.button("❌ 現在画面に出ているシフト表のみを消去する", use_container_width=True):
        save_data_to_server(TABLE_FILE, {})
        if "shift_dict" in st.session_state:
            st.session_state["shift_dict"] = None
        st.success("画面のシフト表を消去しました。新しい設定で作り直せます。")
        st.rerun()
with r_col2:
    if st.button("⚠️ 過去の出勤ズレ（繰越残高）もすべて含めて完全リセットする", use_container_width=True):
        save_data_to_server(BALANCE_FILE, {})
        save_data_to_server(TABLE_FILE, {})
        if "shift_dict" in st.session_state:
            st.session_state["shift_dict"] = None
        st.session_state["two_off_dates"] = []
        st.session_state["individual_off_dates"] = {}
        st.success("すべての記憶データを完全にリセットしました。")
        st.rerun()

# ------------------------------------------------------------------
# 2.6 事前入力チェック（バリデーション）
# ------------------------------------------------------------------
def validate_fixed_rules(members, days_base, fixed_rules, day_reqs, max_consecutive, blocks):
    errors = []
    for d in days_base:
        fixed_a = [m for m in members if fixed_rules[m].get(d) == "A"]
        fixed_b = [m for m in members if fixed_rules[m].get(d) == "B"]
        fixed_off = [m for m in members if fixed_rules[m].get(d) == "Off"]
        sample_day = [k for k in day_reqs if k.split("_")[1].split("(")[0] == d]
        if sample_day:
            req = day_reqs[sample_day[0]]
            if len(fixed_a) > req["A"]:
                errors.append(f"「{d}曜日」に『🔴洗浄エリア』が {len(fixed_a)}人 指定されていますが、最大枠は {req['A']}人 です。")
            if len(fixed_b) > req["B"]:
                errors.append(f"「{d}曜日」に『
