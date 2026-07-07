import streamlit as st
import pulp
import pandas as pd
import json
import os
from datetime import datetime

st.set_page_config(page_title="自動シフト作成アプリ (固定日カスタマイズ版)", layout="centered")
st.title("📅 自動シフト作成アプリ (曜日固定機能付き)")
st.write("スタッフから出勤者を選び、4週間分のシフトを公平に振り分けます。曜日ごとの固定勤務・固定休みも設定可能です。")

BALANCE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shift_balance.json")

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

col1, col2 = st.columns(2)
with col1:
    include_weekend = st.checkbox("土日を含める", value=False)
with col2:
    n_staff = st.number_input("スタッフ人数", min_value=2, max_value=20, value=6, step=1)

DAYS_BASE = ["月", "火", "水", "木", "金", "土", "日"] if include_weekend else ["月", "火", "水", "木", "金"]
N_WEEKS = 4
DAYS = [f"{w}週目_{d}" for w in range(1, N_WEEKS + 1) for d in DAYS_BASE]

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

st.caption(
    f"1日あたり: A={n_a}人 / B={n_b}人 / 休み={n_off}人（出勤合計 {n_work}人 / {n_staff}人中）"
)

# --- ④ 端数（均等にならない理由）の説明を復活 ---
total_workdays = n_work * len(DAYS)
base, rem = divmod(total_workdays, n_staff)
if rem == 0:
    st.info(f"💡 この1ヶ月（{len(DAYS)}日換算）では、全員がちょうど **{base}日** ずつ出勤すれば完全に均等になります。")
else:
    st.info(
        f"💡 今回の出勤枠は合計 **{total_workdays}人日**、スタッフは **{n_staff}人** なので、"
        f"{n_staff - rem}人が **{base}日**、{rem}人が **{base + 1}日** の出勤となり、"
        f"今回だけを見れば1日差が生まれます。"
        f"下記の **繰越残高** の仕組みにより、多く出勤した人は次回以降で優先的に少なくなるよう自動調整され、"
        f"複数回を通してみれば実際の出勤日数は均等に近づいていきます。"
        f"（ただし曜日固定ルールで特定の人の出勤日数を縛っている場合、その人だけはこの調整の対象外になる点にご注意ください。）"
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
    dup = sorted({m for m in members if members.count(m) > 1})
    st.error(f"⚠️ 名前が重複しています: {', '.join(dup)}。名前は全員分ユニークにしてください。")
    st.stop()
if any(m == "" for m in members):
    st.error("⚠️ 空欄の名前があります。全員分入力してください。")
    st.stop()

# --- 曜日固定枠の設定 ---
st.subheader("📌 曜日固定の設定 (任意)")
st.caption("毎週特定の曜日に、必ず特定の役割（A固定、B固定、または固定休み）を入れたい場合、ここで曜日ごとに指定できます。")

fixed_rules = {m: {} for m in members}  # { メンバー名: { 曜日: 役割 } }

with st.expander("各スタッフの曜日固定を設定する", expanded=False):
    for m in members:
        st.write(f"**{m} の固定ルール**")
        cols = st.columns(len(DAYS_BASE))
        for d, c in zip(DAYS_BASE, cols):
            with c:
                choice = st.selectbox(
                    f"{d}曜日",
                    options=["指定なし", "🔴 A固定", "🔵 B固定", "休 固定"],
                    key=f"fix_{m}_{d}"
                )
                if choice == "🔴 A固定":
                    fixed_rules[m][d] = "A"
                elif choice == "🔵 B固定":
                    fixed_rules[m][d] = "B"
                elif choice == "休 固定":
                    fixed_rules[m][d] = "Off"

st.subheader("🙅 希望休（任意）")
preferred_off = {m: set() for m in members}
with st.expander("特定の曜日をすべて希望休にする（上記で『休 固定』にした場合はチェック不要です）", expanded=False):
    for m in members:
        st.write(f"**{m}**")
        cols = st.columns(len(DAYS_BASE))
        for d, c in zip(DAYS_BASE, cols):
            with c:
                if st.checkbox(d, key=f"off_{m}_{d}"):
                    for w in range(1, N_WEEKS + 1):
                        preferred_off[m].add(f"{w}週目_{d}")

max_consecutive = st.slider("連続勤務の上限（日）", min_value=1, max_value=7, value=4)

# ------------------------------------------------------------------
# 2.4 連続日ブロックの構築（① 週をまたぐバグの修正）
# ------------------------------------------------------------------
def build_continuity_blocks(days_base, n_weeks, include_weekend):
    """
    連勤チェックに使う『実際に切れ目なく連続している日』のまとまりを作る。
    - 土日を含める場合：カレンダー上ずっと連続しているので、全体を1つの塊として扱う。
    - 土日を含めない場合：金曜→月曜の間には必ず土日の休みが入るため、
      週ごとに独立した塊として扱う（週をまたいで連勤判定をしない）。
    """
    all_days = [f"{w}週目_{d}" for w in range(1, n_weeks + 1) for d in days_base]
    if include_weekend:
        return [all_days]
    else:
        return [all_days[i * len(days_base):(i + 1) * len(days_base)] for i in range(n_weeks)]


blocks = build_continuity_blocks(DAYS_BASE, N_WEEKS, include_weekend)

# ------------------------------------------------------------------
# 2.5 繰越残高の表示
# ------------------------------------------------------------------
st.subheader("📊 繰越残高")
raw_balance, last_updated = load_balance()
balance = {m: float(raw_balance.get(m, 0.0)) for m in members}
bal_df = pd.DataFrame([{"名前": m, "繰越残高（日）": round(balance[m], 2)} for m in members]).set_index("名前")
st.dataframe(bal_df, use_container_width=True)
if last_updated:
    st.caption(f"最終更新: {last_updated}")

if st.button("🗑️ 繰越残高をリセットする"):
    reset_balance()
    st.success("リセットしました。ページを再読み込みしてください。")
    st.stop()

# ------------------------------------------------------------------
# 2.6 事前バリデーション（② 固定ルールの矛盾を、解く前に検知する）
# ------------------------------------------------------------------
def validate_fixed_rules(members, days_base, fixed_rules, n_a, n_b, n_off, max_consecutive, blocks):
    errors = []

    # (a) 曜日ごとに固定人数が枠を超えていないか
    for d in days_base:
        fixed_a = [m for m in members if fixed_rules[m].get(d) == "A"]
        fixed_b = [m for m in members if fixed_rules[m].get(d) == "B"]
        fixed_off = [m for m in members if fixed_rules[m].get(d) == "Off"]

        if len(fixed_a) > n_a:
            errors.append(
                f"「{d}曜日」の🔴A固定が **{len(fixed_a)}人**（{', '.join(fixed_a)}）指定されていますが、"
                f"A枠は **{n_a}人** までです。"
            )
        if len(fixed_b) > n_b:
            errors.append(
                f"「{d}曜日」の🔵B固定が **{len(fixed_b)}人**（{', '.join(fixed_b)}）指定されていますが、"
                f"B枠は **{n_b}人** までです。"
            )
        if len(fixed_off) > n_off:
            errors.append(
                f"「{d}曜日」の休固定が **{len(fixed_off)}人**（{', '.join(fixed_off)}）指定されていますが、"
                f"休み枠は **{n_off}人** までです。"
            )

    # (b) 固定ルールだけで連勤上限を超えていないか（③）
    for m in members:
        for block in blocks:
            run = 0
            run_start = None
            for day in block:
                weekday = day.split("_")[1]
                is_fixed_work = fixed_rules[m].get(weekday) in ("A", "B")
                if is_fixed_work:
                    if run == 0:
                        run_start = day
                    run += 1
                else:
                    run = 0
                if run > max_consecutive:
                    errors.append(
                        f"**{m}** さんは「{run_start}〜{day}」の固定ルールだけで"
                        f"{run}連勤になり、連続勤務の上限（{max_consecutive}日）を超えています。"
                    )
                    break  # このブロックはこれ以上見なくてよい

    return errors


validation_errors = validate_fixed_rules(
    members, DAYS_BASE, fixed_rules, int(n_a), int(n_b), int(n_off), int(max_consecutive), blocks
)

if validation_errors:
    st.subheader("🚫 固定ルールの設定に矛盾があります")
    st.caption("下記を解消してから「シフトを自動作成する」を押してください（この状態ではボタンを表示していません）。")
    for e in validation_errors:
        st.error(e)

# ------------------------------------------------------------------
# 3. ソルバー本体
# ------------------------------------------------------------------
SOLVER_TIME_LIMIT_SECONDS = 20  # 1段階あたりの上限。CBCが応答しなくても最悪ここで打ち切られる


def build_and_solve(days, members, n_a, n_b, n_off, preferred_off, max_consecutive, balance, fixed_rules, blocks,
                     enforce_fair_work=True, enforce_fair_b=True, fair_tol=1,
                     enforce_consecutive=True, enforce_preferred_off=True):
    roles = ["A", "B", "Off"]
    prob = pulp.LpProblem("Shift_Scheduling", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("x", ((d, m, r) for d in days for m in members for r in roles), cat="Binary")

    max_work = pulp.LpVariable("max_work", cat="Continuous")
    min_work = pulp.LpVariable("min_work", cat="Continuous")
    max_b = pulp.LpVariable("max_b", lowBound=0, cat="Integer")
    min_b = pulp.LpVariable("min_b", lowBound=0, cat="Integer")

    prob += (max_work - min_work) * 10 + (max_b - min_b)

    for d in days:
        prob += pulp.lpSum(x[d, m, "A"] for m in members) == n_a
        prob += pulp.lpSum(x[d, m, "B"] for m in members) == n_b
        prob += pulp.lpSum(x[d, m, "Off"] for m in members) == n_off
        for m in members:
            prob += pulp.lpSum(x[d, m, r] for r in roles) == 1

        day_of_week = d.split("_")[1]
        for m in members:
            if day_of_week in fixed_rules[m]:
                target_role = fixed_rules[m][day_of_week]
                prob += x[d, m, target_role] == 1

    for m in members:
        total_work = pulp.lpSum(x[d, m, "A"] + x[d, m, "B"] for d in days)
        total_b = pulp.lpSum(x[d, m, "B"] for d in days)

        adjusted_work = total_work + balance.get(m, 0.0)

        prob += adjusted_work <= max_work
        prob += adjusted_work >= min_work
        prob += total_b <= max_b
        prob += total_b >= min_b

        # --- ① 週ブロックごとに連勤チェック（週をまたがない） ---
        if enforce_consecutive:
            for block in blocks:
                if len(block) >= max_consecutive + 1:
                    for i in range(len(block) - max_consecutive):
                        window = block[i:i + max_consecutive + 1]
                        prob += pulp.lpSum(x[d, m, "A"] + x[d, m, "B"] for d in window) <= max_consecutive

        if enforce_preferred_off:
            for d in preferred_off.get(m, set()):
                day_of_week = d.split("_")[1]
                if fixed_rules[m].get(day_of_week, "Off") != "Off":
                    continue
                prob += x[d, m, "Off"] == 1

    if enforce_fair_work:
        prob += max_work - min_work <= fair_tol
    if enforce_fair_b:
        prob += max_b - min_b <= fair_tol

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=SOLVER_TIME_LIMIT_SECONDS))
    return status, x, roles


def try_solve_with_relaxation(days, members, n_a, n_b, n_off, preferred_off, max_consecutive, balance, fixed_rules, blocks):
    stages = [
        dict(label="① すべての条件（1ヶ月公平性・固定枠・希望休・連勤上限）を満たす解",
             enforce_fair_work=True, enforce_fair_b=True, fair_tol=1, enforce_consecutive=True, enforce_preferred_off=True),
        dict(label="② 公平性の許容差を±2日に緩和した解",
             enforce_fair_work=True, enforce_fair_b=True, fair_tol=2, enforce_consecutive=True, enforce_preferred_off=True),
        dict(label="③ 希望休を一部無視した解（固定枠・公平性を優先）",
             enforce_fair_work=True, enforce_fair_b=True, fair_tol=2, enforce_consecutive=True, enforce_preferred_off=False),
        dict(label="④ 連勤上限を無視した解",
             enforce_fair_work=True, enforce_fair_b=True, fair_tol=2, enforce_consecutive=False, enforce_preferred_off=False),
        dict(label="⑤ 公平性を努力目標に戻した解（最終フォールバック）",
             enforce_fair_work=False, enforce_fair_b=False, fair_tol=999, enforce_consecutive=False, enforce_preferred_off=False),
    ]
    log = []
    for stage in stages:
        label = stage.pop("label")
        status, x, roles = build_and_solve(
            days, members, n_a, n_b, n_off, preferred_off, max_consecutive, balance, fixed_rules, blocks, **stage
        )
        status_str = pulp.LpStatus[status]
        ok = status_str == "Optimal"
        log.append((label, ok, status_str))
        if ok:
            return status, x, roles, log
    return status, x, roles, log


# ------------------------------------------------------------------
# 4. 実行（バリデーションエラーがある間はボタン自体を出さない）
# ------------------------------------------------------------------
if "generated" not in st.session_state:
    st.session_state["generated"] = False

if not validation_errors:
    if st.button("✨ 1ヶ月分のシフトを自動作成する", type="primary"):
        with st.spinner("計算中です。少々お待ちください..."):
            status, x, roles, log = try_solve_with_relaxation(
                DAYS, members, int(n_a), int(n_b), int(n_off), preferred_off, int(max_consecutive), balance, fixed_rules, blocks
            )
        st.session_state["generated"] = True
        st.session_state["status"] = status
        st.session_state["x_values"] = {
            (d, m, r): x[d, m, r].varValue for d in DAYS for m in members for r in ["A", "B", "Off"]
        }
        st.session_state["log"] = log
        # --- この結果を作った時点の設定をまるごと保存する（表示中に設定が変わってもズレないように） ---
        st.session_state["snapshot"] = {
            "DAYS": list(DAYS),
            "DAYS_BASE": list(DAYS_BASE),
            "N_WEEKS": N_WEEKS,
            "members": list(members),
            "n_staff": int(n_staff),
            "total_workdays": total_workdays,
            "balance": dict(balance),
        }

if st.session_state.get("generated"):
    snap = st.session_state["snapshot"]
    DAYS_disp = snap["DAYS"]
    DAYS_BASE_disp = snap["DAYS_BASE"]
    N_WEEKS_disp = snap["N_WEEKS"]
    members_disp = snap["members"]
    n_staff_disp = snap["n_staff"]
    total_workdays_disp = snap["total_workdays"]
    balance_disp = snap["balance"]

    # 表示中の設定（現在の画面の値）と、シフトを作った時点の設定がズレていたら注意喚起
    if DAYS_disp != list(DAYS) or members_disp != list(members):
        st.warning(
            "⚠️ 下に表示されているシフトは、**作成した時点の設定**のままです。"
            "その後スタッフ人数や曜日設定を変更された場合、最新の設定を反映するには"
            "もう一度「シフトを自動作成する」を押してください。"
        )

    log = st.session_state["log"]
    status = st.session_state["status"]
    x_values = st.session_state["x_values"]

    with st.expander("🔍 求解の過程", expanded=False):
        for label, ok, status_str in log:
            suffix = "" if ok else f"（結果: {status_str}）"
            st.write(("✅ " if ok else "❌ ") + label + suffix)

    if pulp.LpStatus[status] == "Optimal":
        succeeded_label = next(label for label, ok, _ in log if ok)
        if succeeded_label.startswith("①"):
            st.success("🎉 曜日固定を反映した1ヶ月分の最適なシフトが完成しました！")
        else:
            st.warning(f"⚠️ 一部条件を緩和して解を求めました。採用: **{succeeded_label}**")

        shift_data = []
        work_days_map = {}
        for m in members_disp:
            row = {"名前": m}
            work_days = 0
            b_days = 0
            for d in DAYS_disp:
                # スナップショットのキーと必ず一致するので通常は問題ないが、
                # 念のための保険として .get(..., 0) にしておく
                is_a = x_values.get((d, m, "A"), 0) == 1
                is_b = x_values.get((d, m, "B"), 0) == 1
                if is_a:
                    row[d] = "🔴 A"
                    work_days += 1
                elif is_b:
                    row[d] = "🔵 B"
                    work_days += 1
                    b_days += 1
                else:
                    row[d] = "休"
            row["出勤日数（合計）"] = work_days
            row["B担当（合計）"] = b_days
            work_days_map[m] = work_days
            shift_data.append(row)

        df = pd.DataFrame(shift_data).set_index("名前")

        for w in range(1, N_WEEKS_disp + 1):
            st.subheader(f"📅 第 {w} 週目 のシフト")
            week_cols = [f"{w}週目_{d}" for d in DAYS_BASE_disp]
            sub_df = df[week_cols + ["出勤日数（合計）", "B担当（合計）"]].copy()
            sub_df.columns = DAYS_BASE_disp + ["出勤日数（合計）", "B担当（合計）"]
            st.dataframe(sub_df, use_container_width=True)

        period_avg = total_workdays_disp / n_staff_disp
        new_balance_preview = {m: round(balance_disp[m] + work_days_map[m] - period_avg, 3) for m in members_disp}

        st.subheader("📈 今回反映後の繰越残高（プレビュー）")
        preview_df = pd.DataFrame(
            [{"名前": m, "現在の残高": round(balance_disp[m], 2), "今回の出勤": work_days_map[m],
              "更新後の残高": new_balance_preview[m]} for m in members_disp]
        ).set_index("名前")
        st.dataframe(preview_df, use_container_width=True)

        if st.button("✅ この結果を確定して繰越残高を保存する"):
            save_balance(new_balance_preview)
            st.success("繰越残高を保存しました！")
    else:
        last_status_str = log[-1][2] if log else pulp.LpStatus[status]
        if last_status_str not in ("Optimal", "Infeasible"):
            st.error(
                f"⏱️ 計算が制限時間（{SOLVER_TIME_LIMIT_SECONDS}秒/段階）以内に終わりませんでした"
                f"（最終ステータス: {last_status_str}）。\n\n"
                "これはスタッフ数や固定ルールが多く、組み合わせが複雑になっている場合に起こります。"
                "人数を減らす、固定ルールを減らす、または`SOLVER_TIME_LIMIT_SECONDS`の値を"
                "コード内で大きくして再度お試しください。"
            )
        else:
            st.error("❌ 指定された固定曜日が多すぎるか、条件が競合してシフトが作れませんでした。設定を見直してください。")
