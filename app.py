import streamlit as st
import pulp
import pandas as pd
import json
import os
import jpholiday
from datetime import datetime, timedelta

st.set_page_config(page_title="自動シフト作成アプリ", layout="wide")
st.title("📅 自動シフト作成アプリ")
st.write("スタッフ全員の4週間分のシフトをボタン一つで自動作成します。祝日は自動的に全員『お休み』になります。出来上がったシフトを後から手動で入れ替える（微調整する）ことも可能です。")

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

col3, col4 = st.columns(2)
with col3:
    n_a = st.number_input("🔴 A作業（複数人）に必要な人数", min_value=1, max_value=int(n_staff), value=3, step=1)
with col4:
    n_b = st.number_input("🔵 B作業（少人数）に必要な人数", min_value=0, max_value=int(n_staff), value=1, step=1)

n_work = n_a + n_b
if n_work > n_staff:
    st.error(f"⚠️ エラー：必要な人数（AとBの合計 {n_work}人）が、スタッフ全体の人数（{n_staff}人）を超えています。")
    st.stop()
n_off = n_staff - n_work

active_days_count = len(DAYS) - len(HOLIDAYS)
total_workdays = n_work * active_days_count

if n_staff > 0:
    base, rem = divmod(total_workdays, n_staff)
    st.caption(f"【日々の配置人数】 1日あたり：A作業={n_a}人 / B作業={n_b}人 / お休み={n_off}人")
    if rem == 0:
        st.info(f"💡 今回の期間（祝日を除く平日の合計 {active_days_count}日間）では、全員がちょうど **{base}日ずつ** 出勤すればピッタリ均等になります。")
    else:
        st.info(
            f"💡 今回必要な出勤枠は合計で **{total_workdays}回分** です。"
            f"スタッフ {n_staff}人で割ると割り切れないため、"
            f"{n_staff - rem}人が **{base}日出勤**、{rem}人が **{base + 1}日出勤** となり、今回だけを見ると1日分の差が出ます。"
            f"（※この差は、次回以降のシフト作成時に自動で引き継がれて調整されます）"
        )

# ------------------------------------------------------------------
# 2. スタッフ設定
# ------------------------------------------------------------------
st.subheader("👥 2. スタッフの名前入力")

members = []
name_slots = st.columns(2)
for i in range(int(n_staff)):
    with name_slots[i % 2]:
        name = st.text_input(f"スタッフ {i+1} の名前", value=f"スタッフ{i+1}", key=f"member_{i}")
        members.append(name.strip())

if len(members) != len(set(members)):
    st.error("⚠️ エラー：名前が同じになっている人がいます。「田中A」「田中B」のように、必ず全員別々の名前（違う文字）を入力してください。")
    st.stop()
if any(m == "" for m in members):
    st.error("⚠️ エラー：名前が空欄になっているところがあります。全員分の名前を入力してください。")
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
                choice = st.selectbox(f"{d}曜日", options=["指定なし", "🔴 A固定", "🔵 B固定", "休 固定"], key=f"fix_{m}_{d}")
                if choice == "🔴 A固定": fixed_rules[m][d] = "A"
                elif choice == "🔵 B固定": fixed_rules[m][d] = "B"
                elif choice == "休 固定": fixed_rules[m][d] = "Off"

# --- 🙅 希望休の設定 ---
st.subheader("🙅 4. 希望休の設定（お休みにしたい曜日がある場合のみチェック・任意）")
preferred_off = {m: set() for m in members}
with st.expander("ここをクリックして希望休を設定する（上の設定で『休 固定』にした曜日はチェック不要です）", expanded=False):
    for m in members:
        st.write(f"**【{m}】さんのお休み希望**")
        cols = st.columns(len(DAYS_BASE_WEEK))
        for d, c in zip(DAYS_BASE_WEEK, cols):
            with c:
                if st.checkbox(d, key=f"off_{m}_{d}"):
                    for d_key in DAYS:
                        weekday_part = d_key.split("_")[1].split("(")[0]
                        if weekday_part == d:
                            preferred_off[m].add(d_key)

max_consecutive = st.slider("連続で勤務してよい上限日数", min_value=1, max_value=7, value=4)

# 連勤判定用の週ブロック
days_per_week = len(DAYS_BASE_WEEK)
blocks = [DAYS[i*days_per_week:(i+1)*days_per_week] for i in range(N_WEEKS)]

# 繰越残高の表示
st.subheader("📊 前回までの出勤数のズレ（繰越残高）")
raw_balance, last_updated = load_balance()
balance = {m: float(raw_balance.get(m, 0.0)) for m in members}
bal_df = pd.DataFrame([{"名前": m, "これまでのズレ（日分）": round(balance[m], 2)} for m in members]).set_index("名前")
st.dataframe(bal_df, use_container_width=True)

if st.button("🗑️ 過去の出勤ズレ（繰越残高）をすべてリセットする"):
    reset_balance()
    st.success("過去のデータをリセットしました。ページを再読み込み（リフレッシュ）してください。")
    st.stop()

# ------------------------------------------------------------------
# 2.6 事前入力チェック（バリデーション）
# ------------------------------------------------------------------
def validate_fixed_rules(members, days_base, fixed_rules, n_a, n_b, n_off, max_consecutive, blocks):
    errors = []
    for d in days_base:
        fixed_a = [m for m in members if fixed_rules[m].get(d) == "A"]
        fixed_b = [m for m in members if fixed_rules[m].get(d) == "B"]
        fixed_off = [m for m in members if fixed_rules[m].get(d) == "Off"]

        if len(fixed_a) > n_a:
            errors.append(f"「{d}曜日」に『🔴A固定』が {len(fixed_a)}人 指定されていますが、全体の枠が {n_a}人 しかないため計算できません。")
        if len(fixed_b) > n_b:
            errors.append(f"「{d}曜日」に『🔵B固定』が {len(fixed_b)}人 指定されていますが、全体の枠が {n_b}人 しかないため計算できません。")
        if len(fixed_off) > n_off:
            errors.append(f"「{d}曜日」に『休 固定』が {len(fixed_off)}人 指定されていますが、お休みできる上限（{n_off}人）を超えているため計算できません。")

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
                    errors.append(f"【{m}】さんは「{run_start}〜{day}」の固定ルールの組み合わせだけで{run}連勤になってしまい、設定された連続勤務の上限を超えてしまいます。")
                    break
    return errors

validation_errors = validate_fixed_rules(members, DAYS_BASE_WEEK, fixed_rules, int(n_a), int(n_b), int(n_off), int(max_consecutive), blocks)

if validation_errors:
    st.subheader("🚫 入力内容に矛盾（ムリな設定）があります")
    st.caption("以下の原因を解消してから、もう一度「シフトを自動作成する」ボタンを押してください。")
    for e in validation_errors:
        st.error(e)

# ------------------------------------------------------------------
# 3. 計算エンジン & 条件の段階的緩和
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
        dict(label="① すべての条件（全員公平・固定ルール・希望休・連勤上限）を完璧に満たすシフト", enforce_fair_work=True, fair_tol=1, enforce_consecutive=True, enforce_preferred_off=True),
        dict(label="② 全員の出勤数の差を「プラスマイナス2日」まで少し広げて作ったシフト", enforce_fair_work=True, fair_tol=2, enforce_consecutive=True, enforce_preferred_off=True),
        dict(label="③ 希望休（お休み希望）を一部だけスキップして、固定ルールと出勤数の公平さを優先したシフト", enforce_fair_work=True, fair_tol=2, enforce_consecutive=True, enforce_preferred_off=False),
        dict(label="④ 連続勤務の上限日数を一時的に無視して作ったシフト", enforce_fair_work=True, fair_tol=2, enforce_consecutive=False, enforce_preferred_off=False),
        dict(label="⑤ 出勤数の公平さを努力目標に落として、ひとまず人数枠を埋めることを最優先したシフト", enforce_fair_work=False, fair_tol=999, enforce_consecutive=False, enforce_preferred_off=False),
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
        with st.spinner("コンピューターが一番良いシフトを計算しています。少しお待ちください..."):
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
                st.success("🎉 シフト表が完成しました！画面の下に進んで確認してください。")
            else:
                st.error("❌ 条件が厳しすぎるためシフトを作れませんでした。お休み希望や曜日固定を少し減らしてください。")

# 完成したシフトの操作パネル
if st.session_state["shift_dict"] is not None:
    s_dict = st.session_state["shift_dict"]
    current_members = st.session_state["snapshot_members"]

    st.markdown("---")
    st.subheader("🔄 シフトの手動入れ替え・微調整（現場での微調整用）")
    st.caption("「指定した日」の「スタッフ2人」の役割を、ピンポイントでその場でガチャンと入れ替えることができます。")
    swap_col1, swap_col2, swap_col3, swap_btn = st.columns([2, 2, 2, 1])
    with swap_col1:
        target_day = st.selectbox("入れ替えたい日を選択してください", options=DAYS)
    with swap_col2:
        staff_1 = st.selectbox("入れ替えるスタッフ ①", options=current_members, key="s1")
    with swap_col3:
        staff_2 = st.selectbox("入れ替えるスタッフ ②", options=current_members, key="s2")
    with swap_btn:
        st.write("")
        if st.button("🔄 この2人を入れ替える", use_container_width=True):
            if staff_1 == staff_2:
                st.error("同じ人同士は入れ替えられません。別々のスタッフを選んでください。")
            else:
                s_dict[target_day][staff_1], s_dict[target_day][staff_2] = s_dict[target_day][staff_2], s_dict[target_day][staff_1]
                st.session_state["shift_dict"] = s_dict
                st.toast(f"📢 {target_day} の 【{staff_1}】さんと【{staff_2}】さんのシフトを入れ替えました！")

    # シフト表作成
    shift_data = []
    work_days_map = {m: 0 for m in current_members}
    for m in current_members:
        row = {"名前": m}
        for d in DAYS:
            val = s_dict[d].get(m, "休")
            row[d] = val
            if val in ("🔴 A", "🔵 B"): work_days_map[m] += 1
        row["実際の出勤日数"] = work_days_map[m]
        shift_data.append(row)
    df = pd.DataFrame(shift_data).set_index("名前")

    st.markdown("---")
    st.subheader("📋 完成したシフト表")
    for w in range(1, N_WEEKS + 1):
        st.markdown(f"#### 📅 第 {w} 週目")
        week_cols = [d for d in DAYS if d.startswith(f"{w}週目_")]
        st.dataframe(df[week_cols + ["実際の出勤日数"]], use_container_width=True)

    st.markdown("---")
    st.subheader("📈 今回のシフトを反映したあとの『出勤ズレ残高』（次回の調整用）")
    period_avg = total_workdays / len(current_members) if len(current_members) > 0 else 0
    new_balance_preview = {m: round(balance.get(m, 0.0) + work_days_map[m] - period_avg, 3) for m in current_members}
    
    preview_rows = [{"名前": m, "これまでのズレ": round(balance.get(m, 0.0), 2), "今回の合計出勤数": work_days_map[m], "更新後の新しいズレ": new_balance_preview[m]} for m in current_members]
    st.dataframe(pd.DataFrame(preview_rows).set_index("名前"), use_container_width=True)

    if st.button("✅ このシフト結果で確定して、出勤数のズレを次回に引き継ぐ"):
        save_balance(new_balance_preview)
        st.success("今回の出勤データを保存しました！次回のシフト作成時は、今回のズレ（残り）を考慮してさらに公平に計算します。")

    # ーーー 💾 【自動ファイル名機能つき】ダウンロードボタン ーーー
    st.markdown("---")
    st.subheader("💾 5. 完成したシフトをパソコンに保存する")
    st.caption("ボタンを押すと、今画面に表示されているシフト表をパソコンに保存できます。これで画面を閉じても安心です！")
    
    # 選択された月曜日の日付から「○月」の数字を自動で取得します
    shift_month = start_date.strftime('%m') # 例: 07
    # 先頭の「0」を消して、分かりやすい「7月のシフト.csv」という名前にします
    display_month = int(shift_month) 
    
    csv_data = df.to_csv().encode('utf-8-sig') # 文字化け防止
    
    st.download_button(
        label=f"📥 {display_month}月のシフト表をダウンロードする",
        data=csv_data,
        file_name=f"{display_month}月のシフト.csv",
        mime="text/csv",
        type="secondary"
    )
