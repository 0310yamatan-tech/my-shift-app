import streamlit as st
import pulp
import pandas as pd
import json
import os
import jpholiday
from datetime import datetime, timedelta
import tempfile
import uuid

st.set_page_config(page_title="自動シフト作成アプリ", layout="wide")
st.title("📅 自動シフト作成アプリ")
st.write("スタッフ全員の4週間分のシフトをボタン一つで自動作成します。祝日は自動的に全員『お休み』になります。出来上がったシフトを後から手動で入れ替えることも可能です。")

# ------------------------------------------------------------------
# 0. データ保存機能（永続性向上・マルチユーザー対応）
# ------------------------------------------------------------------
if "user_session_id" not in st.session_state:
    st.session_state["user_session_id"] = str(uuid.uuid4())[:8]

session_suffix = st.session_state["user_session_id"]
APP_DATA_DIR = os.path.join(os.path.dirname(__file__), "shift_app_data") if "__file__" in globals() else os.path.join(tempfile.gettempdir(), "shift_app_data")
os.makedirs(APP_DATA_DIR, exist_ok=True)

BALANCE_FILE = os.path.join(APP_DATA_DIR, f"shift_balance_{session_suffix}.json")
TABLE_FILE = os.path.join(APP_DATA_DIR, f"shift_table_{session_suffix}.json")

def load_json(file_path, default=None):
    if default is None:
        default = {}
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            st.warning(f"データ読み込みエラー: {file_path} - {str(e)}")
            return default
    return default

def save_json(file_path, data):
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        st.error(f"データ保存エラー: {file_path} - {str(e)}")
        return False

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
    today = datetime.today().date()
    start_monday = today - timedelta(days=today.weekday())
    start_date = st.date_input("シフトを開始する月曜日を選択", value=start_monday)
    if start_date.weekday() != 0:
        st.error("❌ 開始日は必ず「月曜日」を選択してください。月曜日以外の場合、カレンダーが正しく作成できません。")
        st.stop()

# --- 日付リストの生成と祝日の自動判定 ---
DAYS_BASE_WEEK = ["月", "火", "水", "木", "金", "土", "日"] if include_weekend else ["月", "火", "水", "木", "金"]
N_WEEKS = 4

DAYS = []
DAY_TO_DATE = {}
DATE_TO_DAYKEY = {}
HOLIDAYS = set()
all_days_sequence = []
week_blocks = {w: [] for w in range(1, N_WEEKS + 1)}

current_day = start_date
for w in range(1, N_WEEKS + 1):
    for d_idx, d_name in enumerate(["月", "火", "水", "木", "金", "土", "日"]):
        day_key = f"{w}週目_{d_name}({current_day.strftime('%m/%d')})"
        DAY_TO_DATE[day_key] = current_day
        DATE_TO_DAYKEY[current_day] = day_key
        all_days_sequence.append(day_key)
        
        if jpholiday.is_holiday(current_day):
            HOLIDAYS.add(day_key)
        
        if d_name in DAYS_BASE_WEEK:
            DAYS.append(day_key)
            week_blocks[w].append(day_key)
        current_day += timedelta(days=1)

sorted_holidays = sorted(HOLIDAYS, key=lambda k: DAY_TO_DATE[k])
if sorted_holidays:
    st.info(f"💡 対象期間の祝日（全員自動休み）: {', '.join(sorted_holidays)}")
else:
    st.info("💡 対象期間に祝日はありません。")

st.markdown("##### 👥 通常の日の配置人数")
col3, col4 = st.columns(2)
with col3:
    n_a = st.number_input("🔴 洗浄エリアに必要な人数", min_value=0, max_value=int(n_staff), value=3, step=1)
with col4:
    n_b = st.number_input("🔵 クリーンエリアに必要な人数", min_value=0, max_value=int(n_staff), value=2, step=1)

normal_work = int(n_a) + int(n_b)
normal_off_count = int(n_staff) - normal_work
if normal_work > int(n_staff):
    st.error(f"❌ 洗浄+クリーンの合計人数({normal_work}人)がスタッフ総数({n_staff}人)を超えています。")
    st.stop()
if normal_off_count < 0:
    st.error("❌ 休み人数が負数になる設定です。人数を調整してください。")
    st.stop()

# 📉 休みを1人増やす日の設定
st.markdown("##### 📉 【日付限定】出勤人数を減らしたい日の設定")
if "two_off_dates" not in st.session_state:
    st.session_state["two_off_dates"] = []

with st.expander("出勤人数を1人減らす日を登録", expanded=False):
    st.caption(f"通常は休み{normal_off_count}人 → この日は休み{normal_off_count + 1}人になります。")
    date_options = {DAY_TO_DATE[d]: d for d in DAYS if d not in HOLIDAYS}
    sorted_dates = sorted(date_options.keys())

    if sorted_dates:
        selected_target_date = st.selectbox("対象日を選択", options=sorted_dates, format_func=lambda x: x.strftime('%Y-%m-%d (%a)'), key="two_off_select")
        if st.button("➕ 追加"):
            day_key_str = date_options[selected_target_date]
            if day_key_str not in st.session_state["two_off_dates"]:
                st.session_state["two_off_dates"].append(day_key_str)
                st.success(f"{selected_target_date.strftime('%m/%d')} を追加しました")
                st.rerun()

    if st.session_state["two_off_dates"]:
        st.write("**登録済みの日付:**")
        valid_two_off = []
        for d_str in st.session_state["two_off_dates"]:
            if d_str in DAYS and d_str not in HOLIDAYS:
                valid_two_off.append(d_str)
                col_d_text, col_d_del = st.columns([4, 1])
                with col_d_text:
                    st.write(f"・{d_str}")
                with col_d_del:
                    if st.button("❌ 削除", key=f"del_date_{d_str}"):
                        st.session_state["two_off_dates"].remove(d_str)
                        st.rerun()
        st.session_state["two_off_dates"] = valid_two_off

# 各日の必要人数計算
day_requirements = {}
total_workdays = 0
for d_key in DAYS:
    if d_key in HOLIDAYS:
        req = {"A": 0, "B": 0, "Off": int(n_staff)}
    elif d_key in st.session_state["two_off_dates"]:
        reduce = 1
        if n_a >= reduce:
            target_a = n_a - reduce
            target_b = n_b
        elif n_b >= reduce:
            target_a = n_a
            target_b = n_b - reduce
        else:
            target_a = max(0, n_a - 1)
            target_b = max(0, n_b - (1 - (n_a - target_a)))
        req = {"A": target_a, "B": target_b, "Off": n_staff - (target_a + target_b)}
    else:
        req = {"A": n_a, "B": n_b, "Off": normal_off_count}

    day_requirements[d_key] = req
    total_workdays += (req["A"] + req["B"])

# ------------------------------------------------------------------
# 2. スタッフ設定
# ------------------------------------------------------------------
st.subheader("👥 2. スタッフの名前入力")

default_names = ["長谷川", "羽田", "遠藤", "大竹", "石井", "澤田"]
members = []
name_slots = st.columns(2)
for i in range(int(n_staff)):
    with name_slots[i % 2]:
        default_val = default_names[i] if i < len(default_names) else f"スタッフ{i+1}"
        name = st.text_input(f"スタッフ {i+1}", value=default_val, key=f"member_{i}")
        members.append(name.strip())

if len(members) != len(set(members)):
    dup = sorted({m for m in members if members.count(m) > 1})
    st.error(f"❌ 名前が重複しています: {', '.join(dup)}")
    st.stop()
if any(m == "" for m in members):
    st.error("❌ 名前が空欄の項目があります。")
    st.stop()

# --- 📌 曜日固定ルール ---
st.subheader("📌 3. 曜日固定ルール（任意）")
fixed_rules = {m: {} for m in members}
with st.expander("曜日ごとの役割・休みを固定する", expanded=False):
    for m in members:
        st.write(f"**{m} さん**")
        cols = st.columns(len(DAYS_BASE_WEEK))
        for d, c in zip(DAYS_BASE_WEEK, cols):
            with c:
                choice = st.selectbox(
                    f"{d}曜日",
                    options=["指定なし", "🔴 洗浄", "🔵 クリーン", "休み固定"],
                    key=f"fix_{m}_{d}"
                )
                if choice == "🔴 洗浄":
                    fixed_rules[m][d] = "A"
                elif choice == "🔵 クリーン":
                    fixed_rules[m][d] = "B"
                elif choice == "休み固定":
                    fixed_rules[m][d] = "Off"

# --- 🙅 希望休設定 ---
st.subheader("🙅 4. 希望休の設定")
if "perfect_preferred_offs" not in st.session_state:
    st.session_state["perfect_preferred_offs"] = {}

st.session_state["perfect_preferred_offs"] = {m: st.session_state["perfect_preferred_offs"].get(m, []) for m in members}

with st.expander("休みたい日を選択", expanded=True):
    available_days = [d for d in DAYS if d not in HOLIDAYS]
    for m in members:
        default_selected = [d for d in st.session_state["perfect_preferred_offs"][m] if d in available_days]
        selected_days = st.multiselect(
            f"👤 {m} さん",
            options=available_days,
            default=default_selected,
            key=f"off_{m}"
        )
        st.session_state["perfect_preferred_offs"][m] = selected_days

final_preferred_off = {m: set(st.session_state["perfect_preferred_offs"][m]) for m in members}

conflicts = []
for m in members:
    for d in final_preferred_off[m]:
        day_name = d.split("_")[1].split("(")[0]
        if fixed_rules[m].get(day_name) in ("A", "B"):
            conflicts.append(f"⚠️ {m} さん: {d} 希望休 → {day_name}曜日固定勤務のため固定ルールが優先されます")

if conflicts:
    with st.expander("設定の衝突があります", expanded=True):
        for msg in conflicts:
            st.warning(msg)

max_consecutive = st.slider("連続勤務の上限日数", min_value=1, max_value=7, value=4)

# 残高表示
st.subheader("📊 前回からの出勤数残高")
raw_balance = load_json(BALANCE_FILE)
balance = {m: float(raw_balance.get(m, 0.0)) for m in members}
bal_df = pd.DataFrame([{"名前": m, "繰越残高（日）": round(balance[m], 2)} for m in members]).set_index("名前")
st.dataframe(bal_df, use_container_width=True)

# リセット機能
st.subheader("🗑️ データリセット")
r_col1, r_col2 = st.columns(2)
with r_col1:
    if st.button("シフト表のみ消去", use_container_width=True):
        save_json(TABLE_FILE, {})
        st.session_state["shift_dict"] = None
        st.success("シフト表を消去しました")
        st.rerun()
with r_col2:
    if st.button("すべて完全リセット", type="secondary", use_container_width=True):
        save_json(BALANCE_FILE, {})
        save_json(TABLE_FILE, {})
        st.session_state.clear()
        st.success("全データをリセットしました")
        st.rerun()

# ------------------------------------------------------------------
# 入力バリデーション
# ------------------------------------------------------------------
def validate_settings():
    errors = []
    for d_name in DAYS_BASE_WEEK:
        fixed_a = sum(1 for m in members if fixed_rules[m].get(d_name) == "A")
        fixed_b = sum(1 for m in members if fixed_rules[m].get(d_name) == "B")
        fixed_off = sum(1 for m in members if fixed_rules[m].get(d_name) == "Off")
        
        sample_days = [k for k in day_requirements if k.split("_")[1].split("(")[0] == d_name and k not in HOLIDAYS]
        if sample_days:
            req = day_requirements[sample_days[0]]
            if fixed_a > req["A"]:
                errors.append(f"{d_name}曜日: 固定洗浄{fixed_a}人 > 必要{req['A']}人")
            if fixed_b > req["B"]:
                errors.append(f"{d_name}曜日: 固定クリーン{fixed_b}人 > 必要{req['B']}人")
            if fixed_off > req["Off"]:
                errors.append(f"{d_name}曜日: 固定休み{fixed_off}人 > 上限{req['Off']}人")
    return errors

validation_errors = validate_settings()
if validation_errors:
    st.subheader("🚫 設定に矛盾があります")
    for e in validation_errors:
        st.error(e)
    st.stop()

# ------------------------------------------------------------------
# 最適化計算エンジン（修正・デバッグ完了版）
# ------------------------------------------------------------------
def build_and_solve(**params):
    days = params["days"]
    holidays = params["holidays"]
    members = params["members"]
    day_reqs = params["day_reqs"]
    p_off = params["p_off"]
    max_consec = params["max_consecutive"]
    balance = params["balance"]
    fixed_rules = params["fixed_rules"]
    enforce_consec = params["enforce_consecutive"]
    allow_imbalance = params["allow_imbalance"]
    fair_tol = params.get("fair_tol", 1.0)  # 安全にデフォルト値を設定

    roles = ["A", "B", "Off"]
    prob = pulp.LpProblem("Shift_Scheduling", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("x", ((d, m, r) for d in days for m in members for r in roles), cat="Binary")

    max_adj = pulp.LpVariable("max_adj")
    min_adj = pulp.LpVariable("min_adj")
    
    # 目的関数
    if allow_imbalance:
        prob += (max_adj - min_adj) * 0.01  # 連勤上限と希望休のパズルクリアを最優先
    else:
        prob += (max_adj - min_adj)

    # 人数制約
    for d in days:
        req = day_reqs[d]
        prob += pulp.lpSum(x[d, m, "A"] for m in members) == req["A"]
        prob += pulp.lpSum(x[d, m, "B"] for m in members) == req["B"]
        prob += pulp.lpSum(x[d, m, "Off"] for m in members) == req["Off"]
        for m in members:
            prob += pulp.lpSum(x[d, m, r] for r in roles) == 1

    # 祝日・固定ルールの適用
    for d in days:
        if d in holidays:
            for m in members:
                prob += x[d, m, "Off"] == 1
            continue
        d_name = d.split("_")[1].split("(")[0]
        for m in members:
            if d_name in fixed_rules[m]:
                prob += x[d, m, fixed_rules[m][d_name]] == 1

    # 出勤バランス計算
    avg_work = total_workdays / len(members) if len(members) > 0 else 0
    for m in members:
        work = pulp.lpSum(x[d, m, "A"] + x[d, m, "B"] for d in days if d not in holidays)
        adj_work = work + balance.get(m, 0.0) - avg_work
        prob += adj_work <= max_adj
        prob += adj_work >= min_adj

    # 今月での強制割り切りをしない（allow_imbalance=True）ときは、この上限縛りをスキップする
    if not allow_imbalance:
        prob += max_adj - min_adj <= fair_tol

    # 連勤制約
    if enforce_consec:
        if include_weekend:
            check_blocks = [DAYS]
        else:
            check_blocks = [week_blocks[w] for w in range(1, N_WEEKS + 1)]

        for m in members:
            for block in check_blocks:
                if len(block) >= max_consec + 1:
                    for i in range(len(block) - max_consec):
                        window = block[i:i + max_consec + 1]
                        valid_days = [d for d in window if d in days and d not in holidays]
                        if len(valid_days) >= max_consec + 1:
                            prob += pulp.lpSum(x[d, m, "A"] + x[d, m, "B"] for d in valid_days) <= max_consec

    # 希望休の死守
    for m in members:
        for d in p_off.get(m, set()):
            if d in days and d not in holidays:
                d_name = d.split("_")[1].split("(")[0]
                if d_name not in fixed_rules[m]:
                    prob += x[d, m, "Off"] == 1

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=15))
    return status, x

def try_solve():
    stages = [
        # ① 【本命】4連勤上限・希望休を100%守り、割り切れないズレは次月の残高へ回すモード
        {"label": "① 完全条件（連勤上限・希望休死守＋残高繰越モード）", "enforce_consecutive": True, "allow_imbalance": True, "fair_tol": 999.0},
        # ② 予備（基本は通りません）
        {"label": "② 連勤制限緩和", "enforce_consecutive": False, "allow_imbalance": True, "fair_tol": 999.0},
        # ③ 最終手段（人数優先・ランダム）
        {"label": "③ 条件緩和（人数優先・ランダム作成）", "enforce_consecutive": False, "allow_imbalance": True, "fair_tol": 999.0},
    ]
    log = []
    base_params = {
        "days": DAYS, "holidays": HOLIDAYS, "members": members,
        "day_reqs": day_requirements, "p_off": final_preferred_off,
        "max_consecutive": max_consecutive, "balance": balance,
        "fixed_rules": fixed_rules
    }
    for stage in stages:
        label = stage.pop("label")
        if "ランダム" in label:
            current_params = base_params.copy()
            current_params["p_off"] = {m: set() for m in members}
            current_params["fixed_rules"] = {m: {} for m in members}
            status, x = build_and_solve(**current_params, **stage)
        else:
            status, x = build_and_solve(**base_params, **stage)
            
        ok = pulp.LpStatus[status] == "Optimal"
        log.append((label, ok, pulp.LpStatus[status]))
        if ok:
            return status, x, log
    return status, None, log

# ------------------------------------------------------------------
# シフト作成・表示・編集
# ------------------------------------------------------------------
if "shift_dict" not in st.session_state:
    st.session_state["shift_dict"] = load_json(TABLE_FILE)
if "solve_log" not in st.session_state:
    st.session_state["solve_log"] = []

if st.button("✨ シフトを自動作成する", type="primary", use_container_width=True):
    with st.spinner("最適なシフトを計算中..."):
        status, x, log = try_solve()
        st.session_state["solve_log"] = log

        if x is not None:
            s_dict = {}
            for d in DAYS:
                s_dict[d] = {}
                for m in members:
                    if d in HOLIDAYS:
                        s_dict[d][m] = "休(祝)"
                    elif x[d, m, "A"].varValue is not None and round(x[d, m, "A"].varValue) == 1:
                        s_dict[d][m] = "🔴 洗浄エリア"
                    elif x[d, m, "B"].varValue is not None and round(x[d, m, "B"].varValue) == 1:
                        s_dict[d][m] = "🔵 クリーンエリア"
                    else:
                        s_dict[d][m] = "休"
            st.session_state["shift_dict"] = s_dict
            save_json(TABLE_FILE, s_dict)
            ok_label = next(l for l, ok, _ in log if ok)
            st.success(f"✅ {ok_label} でシフトを作成しました")
        else:
            st.error("❌ 条件が厳しすぎるためシフトを作成できませんでした。")

if st.session_state["solve_log"]:
    with st.expander("🔍 計算過程", expanded=False):
        for label, ok, status_str in st.session_state["solve_log"]:
            st.write(f"{'✅' if ok else '❌'} {label} → {status_str}")

if st.session_state.get("shift_dict"):
    s_dict = st.session_state["shift_dict"]
    st.markdown("---")

    st.subheader("🔄 手動調整")
    swap_cols = st.columns([2, 2, 2, 1])
    with swap_cols[0]:
        day_list = sorted(s_dict.keys(), key=lambda k: DAY_TO_DATE[k])
        target_day = st.selectbox("日付選択", day_list)
    with swap_cols[1]:
        s1 = st.selectbox("入れ替え①", members, key="s1")
    with swap_cols[2]:
        s2 = st.selectbox("入れ替え②", members, key="s2")
    with swap_cols[3]:
        if st.button("🔄 入れ替え", use_container_width=True):
            if s1 == s2:
                st.warning("同じスタッフは選択できません")
            elif s_dict[target_day].get(s1) == "休(祝)" or s_dict[target_day].get(s2) == "休(祝)":
                st.warning("祝日は入れ替えできません")
            else:
                s_dict[target_day][s1], s_dict[target_day][s2] = s_dict[target_day][s2], s_dict[target_day][s1]
                a_cnt = sum(1 for v in s_dict[target_day].values() if v == "🔴 洗浄エリア")
                b_cnt = sum(1 for v in s_dict[target_day].values() if v == "🔵 クリーンエリア")
                req = day_requirements[target_day]
                if a_cnt == req["A"] and b_cnt == req["B"]:
                    st.success("入れ替え完了")
                else:
                    st.warning(f"⚠️ 人数変更: 洗浄{a_cnt}人/クリーン{b_cnt}人（元: {req['A']}/{req['B']}）")
                save_json(TABLE_FILE, s_dict)
                st.rerun()

    st.subheader("📋 完成シフト表")
    shift_rows = []
    work_count = {m: 0 for m in members}
    for m in members:
        row = {"名前": m}
        for d in DAYS:
            val = s_dict.get(d, {}).get(m, "休")
            row[d] = val
            if val in ("🔴 洗浄エリア", "🔵 クリーンエリア"):
                work_count[m] += 1
        row["出勤合計"] = work_count[m]
        shift_rows.append(row)
    df = pd.DataFrame(shift_rows).set_index("名前")

    for w in range(1, N_WEEKS + 1):
        week_cols = [c for c in df.columns if c.startswith(f"{w}週目_")]
        if week_cols:
            st.markdown(f"#### 📅 第{w}週")
            st.dataframe(df[week_cols], use_container_width=True)

    st.subheader("📈 次回への繰越残高")
    avg = total_workdays / len(members) if members else 0
    new_balance = {m: round(balance.get(m, 0.0) + work_count[m] - avg, 3) for m in members}
    bal_df_new = pd.DataFrame([
        {"名前": m, "前回残高": round(balance.get(m, 0.0), 2), "今回出勤": work_count[m], "新残高": new_balance[m]}
        for m in members
    ]).set_index("名前")
    st.dataframe(bal_df_new, use_container_width=True)

    if st.button("✅ 確定して残高を保存", type="primary"):
        save_json(BALANCE_FILE, new_balance)
        save_json(TABLE_FILE, s_dict)
        st.success("保存完了！次回のシフト作成に引き継がれます")

    st.subheader("💾 データ出力")
    filename = f"{start_date.strftime('%Y年%m月')}_シフト表.csv"
    csv = df.to_csv().encode("utf-8-sig")
    st.download_button("📥 CSVダウンロード", data=csv, file_name=filename, mime="text/csv", use_container_width=True)
