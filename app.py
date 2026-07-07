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
            errors.append(f"「{d}曜日」のA固定が {len(fixed_a)}人 指定されていますが、A枠は {n_a}人 までです。")
        if len(fixed_b) > n_b:
            errors.append(f"「{d}曜日」のB固定が {len(fixed_b)}人 指定されていますが、B枠は {n_b}人 までです。")
        if len(fixed_off) > n_off:
            errors.append(f"「{d}曜日」の休固定が {len(fixed_off)}人 指定されていますが、休み枠は {n_off}人 までです。")

    for m in members:
        for block in blocks:
            run = 0
            run_start = None
            for day in block:
                weekday = day.split("_")[1].split("(")[0]
                if fixed_rules[m].get(weekday) in ("A", "B"):
                    if run == 0: run_start = day
                    run += 1
                else:
                    run = 0
                if run > max_consecutive:
                    errors.append(f"{m}さんは「{run_start}〜{day}」の固定ルールだけで{run}連勤になり、連続勤務上限を超えています。")
                    break
    return errors

validation_errors = validate_fixed_rules(members, DAYS_BASE_WEEK, fixed_rules, int(n_a), int(n_b), int(n_off), int(max_consecutive), blocks)

if validation_errors:
    st.subheader("🚫 固定ルールの設定に矛盾があります")
    st.caption("下記を解消してから「シフトを自動作成する」を押してください。")
    for e in validation_errors:
        st.error(e)

# ------------------------------------------------------------------
# 3. ソルバー本体 & 段階的緩和
# ------------------------------------------------------------------
def build_and_solve(days, holidays, members, n_a, n_b, n_off, preferred_off, max_consecutive, balance, fixed_rules, blocks,
                    enforce_fair_work=True, fair_tol=1, enforce_consecutive=True, enforce_preferred_off=True):
    roles = ["A", "B", "Off"]
    prob = pulp.LpProblem("Shift_Scheduling", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("x", ((d, m, r) for d in days for m in members for r in roles), cat="Binary")

    max_work = pulp.LpVariable("max_work", cat="Continuous")
    min_work = pulp.LpVariable("min_work", cat="Continuous")
    prob += (max_work - min_work)

    for d in days:
        if d in holidays:
            for m in members:
                prob += x[d, m, "Off"] == 1
        else:
            prob += pulp.lpSum(x[d, m, "A"] for m in members) == n_a
            prob += pulp.lpSum(x[d, m, "B"] for m in members) == n_b
            prob += pulp.lpSum(x[d, m, "Off"] for m in members) == n_off
            
            day_name = d.split("_")[1].split("(")[0]
            for m in members:
                if day_name in fixed_rules[m]:
                    prob += x[d, m, fixed_rules[m][day_name]] == 1

        for m in members:
            prob += pulp.lpSum(x[d, m, r] for r in roles) == 1

    for m in members:
        total_work = pulp.lpSum(x[d, m, "A"] + x[d, m, "B"] for d in days if d not in holidays)
        adjusted_work = total_work + balance.get(m, 0.0)
        prob += adjusted_work <= max_work
        prob += adjusted_work >= min_work

    if enforce_consecutive:
        for m in members:
            for block in blocks:
                for i in range(len(block) - max_consecutive):
                    window = block[i:i + max_consecutive + 1]
                    prob += pulp.lpSum(x[d, m, "A"] + x[d, m, "B"] for d in window) <= max_consecutive

    if enforce_preferred_off:
        for m in members:
            for d in preferred_off.get(m, set()):
                if d in holidays: continue
                day_name = d.split("_")[1].split("(")[0]
                if fixed_rules[m].get(day_name, "Off") != "Off":
                    prob += x[d, m, "Off"] == 1

    if enforce_fair_work:
        prob += max_work - min_work <= fair_tol

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=15))
    return status, x

def try_solve_with_relaxation(days, holidays, members, n_a, n_b, n_off, preferred_off, max_consecutive, balance, fixed_rules, blocks):
    stages = [
        dict(label="① すべての条件（公平性±1日・固定枠・希望休・連勤上限）を満たす解", enforce_fair_work=True, fair_tol=1, enforce_consecutive=True, enforce_preferred_off=True),
        dict(label="② 公平性の許容差を±2日に緩和した解", enforce_fair_work=True, fair_tol=2, enforce_consecutive=True, enforce_preferred_off=True),
        dict(label="③ 希望休を一部無視した解（固定枠・公平性を優先）", enforce_fair_work=True, fair_tol=2, enforce_consecutive=True, enforce_preferred_off=False),
        dict(label="④ 連勤上限を無視した解", enforce_fair_work=True, fair_tol=2, enforce_consecutive=False, enforce_preferred_off=False),
        dict(label="⑤ 公平性を努力目標に戻した解（最終フォールバック）", enforce_fair_work=False, fair_tol=999, enforce_consecutive=False, enforce_preferred_off=False),
    ]
    log = []
    for stage in stages:
        label = stage.pop("label")
        status, x = build_and_solve(days, holidays, members, n_a, n_b, n_off, preferred_off, max_consecutive, balance, fixed_rules, blocks, **stage)
        status_str = pulp.LpStatus[status]
        ok = status_str == "Optimal"
        log.append((label, ok, status_str))
        if ok: return status, x, log
    return status, x, log

# ------------------------------------------------------------------
# 4. 実行 & 表示 & 手動入れ替え
# ------------------------------------------------------------------
if "shift_dict" not in st.session_state: st.session_state["shift_dict"] = None
if "snapshot_members" not in st.session_state: st.session_state["snapshot_members"] = []
if "log" not in st.session_state: st.session_state["log"] = []

if not validation_errors:
    if st.button("✨ 1ヶ月分のシフトを自動作成する", type="primary"):
        with st.spinner("計算中です..."):
            status, x, log = try_solve_with_relaxation(DAYS, HOLIDAYS, members, int(n_a), int(n_b), int(n_off), preferred_off, int(max_consecutive), balance, fixed_rules, blocks)
            st.session_state["log"] = log
            
            if pulp.LpStatus[status] == "Optimal":
                st.session_state["snapshot_members"] = list(members)
                s_dict = {}
                for d in DAYS:
                    s_dict[d] = {}
                    for m in members:
                        if d in HOLIDAYS: s_dict[d][m] = "休(祝)"
                        elif x[d, m, "A"].varValue == 1: s_dict[d][m] = "🔴 A"
                        elif x[d, m, "B"].varValue == 1: s_dict[d][m] = "🔵 B"
                        else: s_dict[d][m] = "休"
                st.session_state["shift_dict"] = s_dict
                st.success("🎉 シフトが完成しました！")
            else:
                st.error("❌ シフトの作成に失敗しました。設定を見直してください。")

# 求解過程の表示
if st.session_state["log"]:
    with st.expander("🔍 求解の過程（段階的緩和アルゴリズム）", expanded=False):
        for label, ok, status_str in st.session_state["log"]:
            st.write(("✅ " if ok else "❌ ") + label + ("" if ok else f"（不可: {status_str}）"))

# 完成したシフトの操作パネル
if st.session_state["shift_dict"] is not None:
    s_dict = st.session_state["shift_dict"]
    current_members = st.session_state["snapshot_members"]

    st.markdown("---")
    st.subheader("🔄 シフトの手動入れ替え・微調整")
    swap_col1, swap_col2, swap_col3, swap_btn = st.columns([2, 2, 2, 1])
    with swap_col1:
        target_day = st.selectbox("入れ替えたい日を選択", options=DAYS)
    with swap_col2:
        staff_1 = st.selectbox("スタッフ ①", options=current_members, key="s1")
    with swap_col3:
        staff_2 = st.selectbox("スタッフ ②", options=current_members, key="s2")
    with swap_btn:
        st.write("")
        if st.button("🔄 入れ替える", use_container_width=True):
            if staff_1 == staff_2:
                st.error("同じスタッフ同士は入れ替えられません。")
            else:
                s_dict[target_day][staff_1], s_dict[target_day][staff_2] = s_dict[target_day][staff_2], s_dict[target_day][staff_1]
                st.session_state["shift_dict"] = s_dict
                st.toast(f"📢 {target_day} の {staff_1} と {staff_2} を入れ替えました！")

    # シフト表作成
    shift_data = []
    work_days_map = {m: 0 for m in current_members}
    for m in current_members:
        row = {"名前": m}
        for d in DAYS:
            val = s_dict[d].get(m, "休")
            row[d] = val
            if val in ("🔴 A", "🔵 B"): work_days_map[m] += 1
        row["実出勤日数"] = work_days_map[m]
        shift_data.append(row)
    df = pd.DataFrame(shift_data).set_index("名前")

    st.markdown("---")
    st.subheader("📋 完成したシフト表")
    for w in range(1, N_WEEKS + 1):
        st.markdown(f"#### 📅 第 {w} 週目")
        week_cols = [d for d in DAYS if d.startswith(f"{w}週目_")]
        st.dataframe(df[week_cols + ["実出勤日数"]], use_container_width=True)

    st.markdown("---")
    st.subheader("📈 今回反映後の繰越残高（プレビュー）")
    period_avg = total_workdays / len(current_members) if len(current_members) > 0 else 0
    new_balance_preview = {m: round(balance.get(m, 0.0) + work_days_map[m] - period_avg, 3) for m in current_members}
    
    preview_rows = [{"名前": m, "現在の残高": round(balance.get(m, 0.0), 2), "今回の出勤": work_days_map[m], "更新後の残高": new_balance_preview[m]} for m in current_members]
    st.dataframe(pd.DataFrame(preview_rows).set_index("名前"), use_container_width=True)

    if st.button("✅ この結果を確定して繰越残高を保存する"):
        save_balance(new_balance_preview)
        st.success("繰越残高を保存しました！")
