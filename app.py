import streamlit as st
import pulp
import pandas as pd
import json
import os
import jpholiday
from datetime import datetime, timedelta
import tempfile

st.set_page_config(page_title="自動シフト作成アプリ", layout="wide")
st.title("📅 自動シフト作成アプリ")
st.write("スタッフ全員の4週間分のシフトをボタン一つで自動作成します。祝日は自動的に全員『お休み』になります。出来上がったシフトを後から手動で入れ替えることも可能です。")

# ------------------------------------------------------------------
# 0. サーバーの一時フォルダを使ってデータを保存する仕組み
# ------------------------------------------------------------------
BALANCE_FILE = os.path.join(tempfile.gettempdir(), "shift_balance_v16.json")
TABLE_FILE = os.path.join(tempfile.gettempdir(), "shift_table_v16.json")

def load_data_from_server(file_path):
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_data_to_server(file_path, data_dict):
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data_dict, f, ensure_ascii=False, indent=2)
    except Exception:
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

sorted_holidays = sorted(HOLIDAYS, key=lambda k: DAY_TO_DATE[k])
st.info(f"💡 選択された期間の中にある祝日（全員自動でお休みになります）: {', '.join(sorted_holidays) if sorted_holidays else 'なし'}")

st.markdown("##### 👥 通常の日の配置人数")
col3, col4 = st.columns(2)
with col3:
    n_a = st.number_input("🔴 通常の日の 洗浄エリアに必要な人数", min_value=1, max_value=int(n_staff), value=3, step=1)
with col4:
    n_b = st.number_input("🔵 通常の日の クリーンエリアに必要な人数", min_value=0, max_value=int(n_staff), value=2, step=1)

normal_off_count = int(n_staff) - (int(n_a) + int(n_b))
if normal_off_count < 0:
    st.error(f"⚠️ A+Bの人数（{int(n_a)+int(n_b)}人）がスタッフ総数（{int(n_staff)}人）を超えています。")
    st.stop()

# 📉 【日付限定】ピンポイントで仕事が少ない設定
st.markdown("##### 📉 【日付限定】ピンポイントで仕事が少ない（休みが1人増える）日の設定")
if "two_off_dates" not in st.session_state:
    st.session_state["two_off_dates"] = []

with st.expander("ここをクリックして、仕事が少なく【休みを1人増やしたい】日付を登録する", expanded=False):
    st.caption(f"通常の日（休み{normal_off_count}人）より出勤人数が1人減って、休みが{normal_off_count + 1}人になります。")
    date_options = {DAY_TO_DATE[d]: d for d in DAYS if d not in HOLIDAYS}
    sorted_dates = sorted(list(date_options.keys()))

    if sorted_dates:
        selected_target_date = st.selectbox("休みを1人増やしたい日を選択", options=sorted_dates, format_func=lambda x: x.strftime('%m/%d'), key="two_off_select")
        if st.button("➕ この日付の休みを1人増やす"):
            day_key_str = date_options[selected_target_date]
            if day_key_str not in st.session_state["two_off_dates"]:
                st.session_state["two_off_dates"].append(day_key_str)
                st.toast(f"📢 {selected_target_date.strftime('%m/%d')} の休みを1人増やす指定をしました！")

    if st.session_state["two_off_dates"]:
        st.write("**現在指定されている日付一覧:**")
        for d_str in list(st.session_state["two_off_dates"]):
            if d_str not in DAYS:
                st.session_state["two_off_dates"].remove(d_str)
                continue
            col_d_text, col_d_del = st.columns([4, 1])
            with col_d_text:
                st.write(f"・{d_str}")
            with col_d_del:
                if st.button("❌ 削除", key=f"del_date_{d_str}"):
                    st.session_state["two_off_dates"].remove(d_str)
                    st.rerun()

# 各日程の必要人数を計算（★ Off人数のハードコード('2'固定)バグを再修正）
day_requirements = {}
total_workdays = 0
for d_key in DAYS:
    if d_key in HOLIDAYS:
        day_requirements[d_key] = {"A": 0, "B": 0, "Off": int(n_staff)}
    elif d_key in st.session_state["two_off_dates"]:
        target_a = int(n_a) - 1 if int(n_a) > 1 else int(n_a)
        target_b = int(n_b) if int(n_a) > 1 else max(0, int(n_b) - 1)
        day_requirements[d_key] = {"A": target_a, "B": target_b, "Off": int(n_staff) - (target_a + target_b)}
    else:
        day_requirements[d_key] = {"A": int(n_a), "B": int(n_b), "Off": normal_off_count}

    if day_requirements[d_key]["A"] + day_requirements[d_key]["B"] > n_staff:
        st.error(f"⚠️ エラー：{d_key} の必要人数がスタッフ全体の人数（{n_staff}人）を超えています。")
        st.stop()
    total_workdays += (day_requirements[d_key]["A"] + day_requirements[d_key]["B"])

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
        name = st.text_input(f"スタッフ {i+1} の名前", value=default_val, key=f"member_{i}")
        members.append(name.strip())

if len(members) != len(set(members)):
    dup = sorted({m for m in members if members.count(m) > 1})
    st.error(f"⚠️ エラー：名前が同じになっている人がいます: {', '.join(dup)}")
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
                if choice == "🔴 洗浄エリア":
                    fixed_rules[m][d] = "A"
                elif choice == "🔵 クリーンエリア":
                    fixed_rules[m][d] = "B"
                elif choice == "休 固定":
                    fixed_rules[m][d] = "Off"

# --- 🙅 希望休の設定 ---
st.subheader("🙅 4. 希望休の設定")

if "perfect_preferred_offs" not in st.session_state:
    st.session_state["perfect_preferred_offs"] = {}
st.session_state["perfect_preferred_offs"] = {m: st.session_state["perfect_preferred_offs"].get(m, []) for m in members}

with st.expander("ここをクリックして【希望休（特定の日付）】を登録する", expanded=True):
    st.caption("各スタッフがお休みしたい日付をすべて選択してください。選んだ瞬間にデータが安全に保持されます。"
               "※曜日固定ルールと同じ日を指定した場合は、固定ルールが優先され希望休は適用されません。"
               "※希望休同士が重なるなどして全員分は叶えられない場合、できるだけ多くの希望を叶えるように自動調整されます"
               "（誰の希望が叶わなかったかは、結果画面で必ず表示されます。無言で全員分キャンセルされることはありません）。")
    available_days = [d for d in DAYS if d not in HOLIDAYS]

    for m in members:
        default_selected = [d for d in st.session_state["perfect_preferred_offs"][m] if d in available_days]
        selected_days = st.multiselect(
            f"👤 【{m}】さんの希望休を選択",
            options=available_days,
            default=default_selected,
            key=f"ms_perfect_off_{m}"
        )
        st.session_state["perfect_preferred_offs"][m] = selected_days

final_preferred_off = {m: set(st.session_state["perfect_preferred_offs"][m]) for m in members}

preferred_vs_fixed_conflicts = []
for m in members:
    for d in final_preferred_off.get(m, set()):
        day_name = d.split("_")[1].split("(")[0]
        if fixed_rules[m].get(day_name) in ("A", "B"):
            preferred_vs_fixed_conflicts.append(
                f"**{m}** さんは「{d}」を希望休にしていますが、"
                f"「{day_name}曜日」は曜日固定で🔴/🔵が設定されています。固定ルールが優先され、この日の希望休は適用されません。"
            )
if preferred_vs_fixed_conflicts:
    with st.expander("⚠️ 希望休と曜日固定がぶつかっている箇所があります（クリックで詳細）", expanded=True):
        for msg in preferred_vs_fixed_conflicts:
            st.warning(msg)

max_consecutive = st.slider("連続で勤務してよい上限日数", min_value=1, max_value=7, value=4)

# ★ include_weekend を無視して常に週分割していたバグも修正
if include_weekend:
    blocks = [DAYS]
else:
    days_per_week = len(DAYS_BASE_WEEK)
    blocks = [DAYS[i * days_per_week:(i + 1) * days_per_week] for i in range(N_WEEKS)]

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
        st.session_state["shift_dict"] = None
        st.success("画面のシフト表を消去しました。新しい設定で作り直せます。")
        st.rerun()
with r_col2:
    if st.button("⚠️ 過去の出勤ズレ（繰越残高）もすべて含めて完全リセットする", use_container_width=True):
        save_data_to_server(BALANCE_FILE, {})
        save_data_to_server(TABLE_FILE, {})
        st.session_state["shift_dict"] = None
        st.session_state["two_off_dates"] = []
        st.session_state["perfect_preferred_offs"] = {}
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
                errors.append(f"「{d}曜日」に『🔵クリーンエリア』が {len(fixed_b)}人 指定されていますが、最大枠は {req['B']}人 です。")
            if len(fixed_off) > req["Off"]:
                errors.append(f"「{d}曜日」に『休 固定』が {len(fixed_off)}人 指定されていますが、上限は {req['Off']}人 です。")

    for m in members:
        for block in blocks:
            run = 0
            run_start = None
            for day in block:
                weekday = day.split("_")[1].split("(")[0]
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
                    break
    return errors

validation_errors = validate_fixed_rules(members, DAYS_BASE_WEEK, fixed_rules, day_requirements, int(max_consecutive), blocks)
if validation_errors:
    st.subheader("🚫 入力内容に矛盾があります")
    for e in validation_errors:
        st.error(e)

# ------------------------------------------------------------------
# 3. 計算エンジン（希望休は「できるだけ叶えるソフト制約」）
# ------------------------------------------------------------------
PREFERRED_OFF_PENALTY = 1000  # 希望休1件を破るコスト。公平性の微調整よりずっと優先されるよう大きくしてある


def build_and_solve(days, holidays, members, day_reqs, p_off, max_consecutive, balance, fixed_rules, blocks,
                     enforce_fair_work=True, fair_tol=1, enforce_consecutive=True):
    roles = ["A", "B", "Off"]
    prob = pulp.LpProblem("Shift_Scheduling", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("x", ((d, m, r) for d in days for m in members for r in roles), cat="Binary")

    max_work = pulp.LpVariable("max_work", cat="Continuous")
    min_work = pulp.LpVariable("min_work", cat="Continuous")

    # 希望休はハード制約にせず、破った件数を最小化するソフト制約にする。
    # これにより (a) 希望休同士が衝突しても「全員分丸ごと無視」にならず、
    # (b) 衝突があっても①〜⑤全滅でシフトが1つも作れない、という事態を避けられる。
    viol_vars = []
    for m in members:
        for d in p_off.get(m, set()):
            if d not in days:
                continue
            day_name = d.split("_")[1].split("(")[0]
            if fixed_rules[m].get(day_name) in ("A", "B"):
                continue
            v = pulp.LpVariable(f"viol_{m}_{d}", cat="Binary")
            prob += x[d, m, "Off"] + v >= 1
            viol_vars.append(v)

    prob += PREFERRED_OFF_PENALTY * pulp.lpSum(viol_vars) + (max_work - min_work)

    for d in days:
        req = day_reqs[d]
        prob += pulp.lpSum(x[d, m, "A"] for m in members) == req["A"]
        prob += pulp.lpSum(x[d, m, "B"] for m in members) == req["B"]
        prob += pulp.lpSum(x[d, m, "Off"] for m in members) == req["Off"]

        if d not in holidays:
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
            for block in blocks:
                if len(block) >= max_consecutive + 1:
                    for i in range(len(block) - max_consecutive):
                        window = block[i:i + max_consecutive + 1]
                        prob += pulp.lpSum(x[d, m, "A"] + x[d, m, "B"] for d in window) <= max_consecutive

    if enforce_fair_work:
        prob += max_work - min_work <= fair_tol

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=15))
    return status, x


def try_solve_with_relaxation(days, holidays, members, day_reqs, p_off, max_consecutive, balance, fixed_rules, blocks):
    stages = [
        dict(label="① すべての条件を完璧に満たすシフト", enforce_fair_work=True, fair_tol=1, enforce_consecutive=True),
        dict(label="② 全員の出勤数の差を2日以内に広げたシフト", enforce_fair_work=True, fair_tol=2, enforce_consecutive=True),
        dict(label="③ 全員の出勤数の差を3日以内に広げたシフト", enforce_fair_work=True, fair_tol=3, enforce_consecutive=True),
        dict(label="④ 連続勤務の上限日数を一時的に無視して作ったシフト", enforce_fair_work=True, fair_tol=2, enforce_consecutive=False),
        dict(label="⑤ 出勤数の公平さを努力目標に落としたシフト", enforce_fair_work=False, fair_tol=999, enforce_consecutive=False),
    ]
    log = []
    for stage in stages:
        label = stage.pop("label")
        status, x = build_and_solve(days, holidays, members, day_reqs, p_off, max_consecutive, balance, fixed_rules, blocks, **stage)
        status_str = pulp.LpStatus[status]
        ok = status_str == "Optimal"
        log.append((label, ok, status_str))
        if ok:
            return status, x, log
    return status, None, log

# ------------------------------------------------------------------
# 4. 実行 & 表示 & 手動入れ替え
# ------------------------------------------------------------------
if "shift_dict" not in st.session_state:
    st.session_state["shift_dict"] = None
if "snapshot_members" not in st.session_state:
    st.session_state["snapshot_members"] = []
if "solve_log" not in st.session_state:
    st.session_state["solve_log"] = []
if "off_report" not in st.session_state:
    st.session_state["off_report"] = {}

if st.session_state["shift_dict"] is None:
    saved_table = load_data_from_server(TABLE_FILE)
    if saved_table:
        st.session_state["shift_dict"] = saved_table
        st.session_state["snapshot_members"] = list(members)

if not validation_errors:
    if st.button("✨ 1ヶ月分のシフトを自動作成する", type="primary"):
        with st.spinner("コンピューターが一番良いシフトを計算しています..."):
            status, x, log = try_solve_with_relaxation(
                DAYS, HOLIDAYS, members, day_requirements, final_preferred_off, int(max_consecutive), balance, fixed_rules, blocks
            )
            st.session_state["solve_log"] = log

            if x is not None:
                st.session_state["snapshot_members"] = list(members)
                s_dict = {}
                for d in DAYS:
                    s_dict[d] = {}
                    for m in members:
                        if d in HOLIDAYS:
                            s_dict[d][m] = "休(祝)"
                        elif x[d, m, "A"].varValue == 1:
                            s_dict[d][m] = "🔴 洗浄エリア"
                        elif x[d, m, "B"].varValue == 1:
                            s_dict[d][m] = "🔵 クリーンエリア"
                        else:
                            s_dict[d][m] = "休"
                st.session_state["shift_dict"] = s_dict
                save_data_to_server(TABLE_FILE, s_dict)

                off_report = {}
                for m in members:
                    missed = []
                    for d in final_preferred_off.get(m, set()):
                        if d not in s_dict:
                            continue
                        if s_dict[d][m] not in ("休", "休(祝)"):
                            missed.append(d)
                    if missed:
                        off_report[m] = sorted(missed, key=lambda k: DAY_TO_DATE.get(k, k))
                st.session_state["off_report"] = off_report

                succeeded_label = next(label for label, ok, _ in log if ok)
                if succeeded_label.startswith("①"):
                    st.success("🎉 すべての条件を満たすシフト表が完成しました！")
                else:
                    st.warning(f"⚠️ 一部の条件（公平性や連勤上限）を緩和して作成しました。採用: **{succeeded_label}**")
            else:
                # ★最重要修正：失敗したら、古い（前回成功時の）シフト表を必ず消す。
                #   これを消さないと「新しい希望休が反映されない」ように見える表示バグの原因になる。
                st.session_state["shift_dict"] = None
                st.session_state["off_report"] = {}
                save_data_to_server(TABLE_FILE, {})
                st.error(
                    "❌ 条件が厳しすぎるためシフトを作れませんでした。曜日固定・配置人数・連勤上限のいずれかに"
                    "無理がある可能性が高いです（希望休は自動調整されるため、希望休だけが原因になることはありません）。"
                    "下の「求解の過程」で、どの段階まで試して失敗したか確認できます。"
                )

    if st.session_state["solve_log"]:
        with st.expander("🔍 求解の過程を見る", expanded=False):
            for label, ok, status_str in st.session_state["solve_log"]:
                suffix = "" if ok else f"（結果: {status_str}）"
                st.write(("✅ " if ok else "❌ ") + label + suffix)

    if st.session_state["shift_dict"] is not None:
        if st.session_state["off_report"]:
            with st.expander("⚠️ 希望休のうち、反映されなかった日があります（クリックで詳細）", expanded=True):
                st.caption("希望が重なるなどして全員分は叶えられなかったため、できるだけ多くの人数のバランスを取りつつ一部だけ出勤に調整されています。")
                for m, missed_days in st.session_state["off_report"].items():
                    st.warning(f"**{m}** さん: {', '.join(missed_days)} は希望休を出しましたが、出勤に調整されています。")
        else:
            st.caption("✅ 希望休はすべて反映されています。")

# シフト表示パネル
if st.session_state["shift_dict"] is not None:
    s_dict = st.session_state["shift_dict"]
    current_members = st.session_state["snapshot_members"]

    st.markdown("---")
    st.subheader("🔄 シフトの手動入れ替え・微調整")
    swap_col1, swap_col2, swap_col3, swap_btn = st.columns([2, 2, 2, 1])
    with swap_col1:
        valid_days = [d for d in DAYS if d in s_dict]
        if not valid_days:
            valid_days = list(s_dict.keys())
        target_day = st.selectbox("入れ替えたい日を選択してください", options=valid_days)
    with swap_col2:
        staff_1 = st.selectbox("入れ替えるスタッフ ①", options=current_members, key="s1")
    with swap_col3:
        staff_2 = st.selectbox("入れ替えるスタッフ ②", options=current_members, key="s2")
    with swap_btn:
        st.write("")
        if st.button("🔄 この2人を入れ替える", use_container_width=True):
            if staff_1 != staff_2:
                s_dict[target_day][staff_1], s_dict[target_day][staff_2] = s_dict[target_day][staff_2], s_dict[target_day][staff_1]
                st.session_state["shift_dict"] = s_dict
                save_data_to_server(TABLE_FILE, s_dict)
                st.toast("📢 入れ替えを保存しました！")

    shift_data = []
    work_days_map = {m: 0 for m in current_members}
    for m in current_members:
        row = {"名前": m}
        for d in DAYS:
            val = s_dict.get(d, {}).get(m, "休")
            row[d] = val
            if val in ("🔴 洗浄エリア", "🔵 クリーンエリア"):
                work_days_map[m] += 1
        row["実際の出勤日数"] = work_days_map[m]
        shift_data.append(row)
    df = pd.DataFrame(shift_data).set_index("名前")

    st.markdown("---")
    st.subheader("📋 完成したシフト表")
    for w in range(1, N_WEEKS + 1):
        st.markdown(f"#### 📅 第 {w} 週目")
        week_cols = [d for d in DAYS if d.startswith(f"{w}週目_") and d in df.columns]
        st.dataframe(df[week_cols + ["実際の出勤日数"]], use_container_width=True)

    st.markdown("---")
    st.subheader("📈 更新後の『出勤ズレ残高』")
    period_avg = total_workdays / len(current_members) if len(current_members) > 0 else 0
    new_balance_preview = {m: round(balance.get(m, 0.0) + work_days_map[m] - period_avg, 3) for m in current_members}
    preview_rows = [{"名前": m, "これまでのズレ": round(balance.get(m, 0.0), 2), "今回の合計出勤数": work_days_map[m], "更新後の新しいズレ": new_balance_preview[m]} for m in current_members]
    st.dataframe(pd.DataFrame(preview_rows).set_index("名前"), use_container_width=True)

    if st.button("✅ このシフト結果で確定して、出勤数のズレを次回に引き継ぐ"):
        save_data_to_server(BALANCE_FILE, new_balance_preview)
        save_data_to_server(TABLE_FILE, s_dict)
        st.success("確定データを保存しました！")

    st.markdown("---")
    st.subheader("💾 5. 完成したシフトをパソコンに保存する")
    target_month = start_date.strftime('%Y年%m月度')
    csv_data = df.to_csv().encode('utf-8-sig')
    st.download_button(label=f"📥 {target_month}のシフト表をダウンロードする", data=csv_data, file_name=f"{target_month}_シフト表.csv", mime="text/csv")
