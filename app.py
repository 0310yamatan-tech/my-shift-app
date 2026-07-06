import streamlit as st
import pulp
import pandas as pd
import json
import os
from datetime import datetime

st.set_page_config(page_title="自動シフト作成アプリ", layout="centered")
st.title("📅 自動シフト作成アプリ")
st.write("スタッフから出勤者を選び、指定した比率で公平に振り分けます。")

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

DAYS_ALL = ["月", "火", "水", "木", "金", "土", "日"]
DAYS = DAYS_ALL if include_weekend else DAYS_ALL[:5]

col3, col4 = st.columns(2)
with col3:
    n_a = st.number_input("A（複数人作業）の人数", min_value=1, max_value=int(n_staff), value=3, step=1)
with col4:
    n_b = st.number_input("B（少人数作業）の人数", min_value=0, max_value=int(n_staff), value=1, step=1)

n_work = n_a + n_b
if n_work > n_staff:
    st.error(f"⚠️ A+B の人数（{n_work}人）がスタッフ総数（{n_staff}人）を超えています。人数を見直してください。")
    st.stop()
n_off = n_staff - n_work

st.caption(
    f"1日あたり: A={n_a}人 / B={n_b}人 / 休み={n_off}人（出勤合計 {n_work}人 / {n_staff}人中）"
)

total_workdays = n_work * len(DAYS)
base, rem = divmod(total_workdays, n_staff)
if rem == 0:
    st.info(f"💡 この設定では、全員がちょうど **{base}日** ずつ出勤すれば完全に均等になります。")
else:
    st.info(
        f"💡 出勤枠は今回合計 **{total_workdays}人日**、スタッフは **{n_staff}人** なので、"
        f"{n_staff - rem}人が **{base}日**、{rem}人が **{base + 1}日** の出勤となり、"
        f"今回だけを見れば1日差が生まれます。"
        f"ただし下記の **繰越残高（バランス）** の仕組みにより、"
        f"多く出勤した人は次回以降で優先的に少なくなるよう自動調整され、"
        f"複数回を通してみれば実際の出勤日数は均等に近づいていきます。"
    )

# ------------------------------------------------------------------
# 2. スタッフ設定（名前・希望休）
# ------------------------------------------------------------------
st.subheader("👥 スタッフの設定")

members = []
name_slots = st.columns(2)
for i in range(int(n_staff)):
    with name_slots[i % 2]:
        name = st.text_input(f"スタッフ {i+1}", value=f"スタッフ{i+1}", key=f"member_{i}")
        members.append(name.strip())

if len(members) != len(set(members)):
    dup = [m for m in set(members) if members.count(m) > 1]
    st.error(f"⚠️ 名前が重複しています: {', '.join(dup)}。名前は全員分ユニークにしてください。")
    st.stop()
if any(m == "" for m in members):
    st.error("⚠️ 空欄の名前があります。全員分入力してください。")
    st.stop()

st.subheader("🙅 希望休（任意）")
st.caption("休みたい曜日にチェックを入れてください。全員が同じ日に希望休を出すと解が見つからない場合がありますが、"
           "その場合はスタッフ間の譲り合いで調整してもらう前提とし、アプリ側では強制解消しません。")

preferred_off = {m: set() for m in members}
with st.expander("希望休を設定する", expanded=False):
    for m in members:
        st.write(f"**{m}**")
        cols = st.columns(len(DAYS))
        for d, c in zip(DAYS, cols):
            with c:
                if st.checkbox(d, key=f"off_{m}_{d}"):
                    preferred_off[m].add(d)

max_consecutive = st.slider("連続勤務の上限（日）", min_value=1, max_value=len(DAYS), value=min(2, len(DAYS)))

# ------------------------------------------------------------------
# 2.5 繰越残高（バランス）の表示
# ------------------------------------------------------------------
st.subheader("📊 繰越残高（前回までの貸し借り）")
st.caption(
    "プラスの人は「これまで多めに出勤してきた」人＝次回以降は少なめに調整されます。"
    "マイナスの人は「これまで少なめだった」人＝次回以降は多めに調整されます。"
)

raw_balance, last_updated = load_balance()
# 現在の名前リストに合わせて整形（未登録スタッフは0扱い、過去スタッフの残高は保持したまま表示しない）
balance = {m: float(raw_balance.get(m, 0.0)) for m in members}

bal_df = pd.DataFrame(
    [{"名前": m, "繰越残高（日）": round(balance[m], 2)} for m in members]
).set_index("名前")
st.dataframe(bal_df, use_container_width=True)
if last_updated:
    st.caption(f"最終更新: {last_updated}")
else:
    st.caption("まだ繰越データはありません（全員0からスタートします）。")

if st.button("🗑️ 繰越残高をリセットする（新年度・新体制の開始時などに）"):
    reset_balance()
    st.success("繰越残高をリセットしました。ページを再読み込みしてください。")
    st.stop()

# ------------------------------------------------------------------
# 3. ソルバー本体
# ------------------------------------------------------------------
def build_and_solve(days, members, n_a, n_b, n_off, preferred_off, max_consecutive, balance,
                     enforce_fair_work=True, enforce_fair_b=True, fair_tol=1,
                     enforce_consecutive=True, enforce_preferred_off=True):
    roles = ["A", "B", "Off"]
    prob = pulp.LpProblem("Shift_Scheduling", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("x", ((d, m, r) for d in days for m in members for r in roles), cat="Binary")

    max_work = pulp.LpVariable("max_work", cat="Continuous")
    min_work = pulp.LpVariable("min_work", cat="Continuous")
    max_b = pulp.LpVariable("max_b", lowBound=0, cat="Integer")
    min_b = pulp.LpVariable("min_b", lowBound=0, cat="Integer")

    # 目的関数：残差があっても最小化を試みる（tie-breaker）
    prob += (max_work - min_work) * 10 + (max_b - min_b)

    for d in days:
        prob += pulp.lpSum(x[d, m, "A"] for m in members) == n_a
        prob += pulp.lpSum(x[d, m, "B"] for m in members) == n_b
        prob += pulp.lpSum(x[d, m, "Off"] for m in members) == n_off
        for m in members:
            prob += pulp.lpSum(x[d, m, r] for r in roles) == 1

    for m in members:
        total_work = pulp.lpSum(x[d, m, "A"] + x[d, m, "B"] for d in days)
        total_b = pulp.lpSum(x[d, m, "B"] for d in days)

        # ここがポイント：今回の出勤日数だけでなく「前回までの繰越残高」を足した値を均等化する
        adjusted_work = total_work + balance.get(m, 0.0)

        prob += adjusted_work <= max_work
        prob += adjusted_work >= min_work
        prob += total_b <= max_b
        prob += total_b >= min_b

        if enforce_consecutive and len(days) >= max_consecutive + 1:
            for i in range(len(days) - max_consecutive):
                window = days[i:i + max_consecutive + 1]
                prob += pulp.lpSum(x[d, m, "A"] + x[d, m, "B"] for d in window) <= max_consecutive

        if enforce_preferred_off:
            for d in preferred_off.get(m, set()):
                prob += x[d, m, "Off"] == 1

    if enforce_fair_work:
        prob += max_work - min_work <= fair_tol
    if enforce_fair_b:
        prob += max_b - min_b <= fair_tol

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    return status, x, roles


def try_solve_with_relaxation(days, members, n_a, n_b, n_off, preferred_off, max_consecutive, balance):
    stages = [
        dict(label="① すべての条件（繰越込み公平性±1日・希望休・連勤上限）を満たす解",
             enforce_fair_work=True, enforce_fair_b=True, fair_tol=1,
             enforce_consecutive=True, enforce_preferred_off=True),
        dict(label="② 公平性の許容差を±2日に緩和した解",
             enforce_fair_work=True, enforce_fair_b=True, fair_tol=2,
             enforce_consecutive=True, enforce_preferred_off=True),
        dict(label="③ 希望休を一部無視した解（希望休より公平性・連勤を優先）",
             enforce_fair_work=True, enforce_fair_b=True, fair_tol=2,
             enforce_consecutive=True, enforce_preferred_off=False),
        dict(label="④ 連勤上限を無視した解",
             enforce_fair_work=True, enforce_fair_b=True, fair_tol=2,
             enforce_consecutive=False, enforce_preferred_off=False),
        dict(label="⑤ 公平性を努力目標に戻した解（最終フォールバック）",
             enforce_fair_work=False, enforce_fair_b=False, fair_tol=999,
             enforce_consecutive=False, enforce_preferred_off=False),
    ]

    log = []
    for stage in stages:
        label = stage.pop("label")
        status, x, roles = build_and_solve(
            days, members, n_a, n_b, n_off, preferred_off, max_consecutive, balance, **stage
        )
        ok = pulp.LpStatus[status] == "Optimal"
        log.append((label, ok))
        if ok:
            return status, x, roles, log
    return status, x, roles, log


# ------------------------------------------------------------------
# 4. 実行
# ------------------------------------------------------------------
if "generated" not in st.session_state:
    st.session_state["generated"] = False

if st.button("✨ シフトを自動作成する", type="primary"):
    status, x, roles, log = try_solve_with_relaxation(
        DAYS, members, int(n_a), int(n_b), int(n_off), preferred_off, int(max_consecutive), balance
    )
    st.session_state["generated"] = True
    st.session_state["status"] = status
    st.session_state["x_values"] = {
        (d, m, r): x[d, m, r].varValue for d in DAYS for m in members for r in ["A", "B", "Off"]
    }
    st.session_state["log"] = log

if st.session_state.get("generated"):
    log = st.session_state["log"]
    status = st.session_state["status"]
    x_values = st.session_state["x_values"]

    with st.expander("🔍 求解の過程（どの条件まで満たせたか）", expanded=False):
        for label, ok in log:
            st.write(("✅ " if ok else "❌ ") + label)

    if pulp.LpStatus[status] == "Optimal":
        succeeded_label = next(label for label, ok in log if ok)
        if succeeded_label.startswith("①"):
            st.success("🎉 すべての条件を満たす最適なシフトが完成しました！")
        else:
            st.warning(
                f"⚠️ 全条件は両立できなかったため、一部を緩和して解を求めました。\n\n"
                f"採用した条件: **{succeeded_label}**\n\n"
                f"詳細は上の「求解の過程」を確認してください。"
            )

        shift_data = []
        work_days_map = {}
        for m in members:
            row = {"名前": m}
            work_days = 0
            b_days = 0
            for d in DAYS:
                if x_values[d, m, "A"] == 1:
                    row[d] = "🔴 A"
                    work_days += 1
                elif x_values[d, m, "B"] == 1:
                    row[d] = "🔵 B"
                    work_days += 1
                    b_days += 1
                else:
                    row[d] = "休"
            row["出勤日数（今回）"] = work_days
            row["B担当日数"] = b_days
            work_days_map[m] = work_days
            shift_data.append(row)

        df = pd.DataFrame(shift_data)
        st.subheader("🗓️ 今回のシフト表")
        st.dataframe(df.set_index("名前"), use_container_width=True)

        work_counts = df["出勤日数（今回）"]
        b_counts = df["B担当日数"]
        st.caption(
            f"出勤日数の範囲: {work_counts.min()}〜{work_counts.max()}日（差 {work_counts.max()-work_counts.min()}日） / "
            f"B担当日数の範囲: {b_counts.min()}〜{b_counts.max()}日（差 {b_counts.max()-b_counts.min()}日）"
        )
        st.info(f"💡 **見方**: 「A」は{n_a}人作業、「B」は{n_b}人作業、「休」は休みです。")

        # 繰越残高の更新プレビュー
        period_avg = total_workdays / n_staff
        new_balance_preview = {
            m: round(balance[m] + work_days_map[m] - period_avg, 3) for m in members
        }
        st.subheader("📈 今回反映後の繰越残高（プレビュー）")
        st.caption(
            "「確定して繰り越す」を押すまでは保存されません。プレビューだけ見て作り直すのもOKです。"
        )
        preview_df = pd.DataFrame(
            [{"名前": m, "現在の残高": round(balance[m], 2),
              "今回の出勤": work_days_map[m],
              "更新後の残高": new_balance_preview[m]} for m in members]
        ).set_index("名前")
        st.dataframe(preview_df, use_container_width=True)

        if st.button("✅ この結果を確定して繰越残高を保存する"):
            save_balance(new_balance_preview)
            st.success("繰越残高を保存しました。次回のシフト作成時に自動的に反映されます。")
    else:
        st.error(
            "❌ すべての段階を試しましたが、条件に合うシフトが見つかりませんでした。\n\n"
            "考えられる原因: A+B人数に対してスタッフ数が少なすぎる、連勤上限が厳しすぎる、"
            "希望休が特定の曜日に集中しすぎている、などです。設定を緩めて再度お試しください。"
        )  