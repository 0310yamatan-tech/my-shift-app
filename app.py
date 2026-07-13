# ------------------------------------------------------------------
# 最適化計算エンジン（修正版：今月での割り切りを強制しない）
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
    allow_imbalance = params["allow_imbalance"]  # ◀ 新設：今月の割り切れなさを許容するフラグ

    roles = ["A", "B", "Off"]
    prob = pulp.LpProblem("Shift_Scheduling", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("x", ((d, m, r) for d in days for m in members for r in roles), cat="Binary")

    max_adj = pulp.LpVariable("max_adj")
    min_adj = pulp.LpVariable("min_adj")
    
    # 目的関数：基本はスタッフ間の不公平を減らす。ただしフラグONの時はペナルティを極限まで下げる
    if allow_imbalance:
        prob += (max_adj - min_adj) * 0.01  # 連勤や希望休のパズルを解くことを最優先にする
    else:
        prob += (max_adj - min_adj)

    # 毎日の必要人数を満たす制約
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

    # 過去の残高を加味した出勤バランスの計算
    avg_work = total_workdays / len(members) if len(members) > 0 else 0
    for m in members:
        work = pulp.lpSum(x[d, m, "A"] + x[d, m, "B"] for d in days if d not in holidays)
        adj_work = work + balance.get(m, 0.0) - avg_work
        prob += adj_work <= max_adj
        prob += adj_work >= min_adj

    # 今月の割り切れなさを許容する場合、ガチガチの「全員同じ出勤数」という縛りを外す
    if not allow_imbalance:
        # 厳密な公平性を求める（従来の縛り）
        prob += max_adj - min_adj <= params.get("fair_tol", 1.0)

    # 連勤制約（週ごとのブロックで判定）
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

    # 希望休は絶対に死守する
    for m in members:
        for d in p_off.get(m, set()):
            if d in days and d not in holidays:
                d_name = d.split("_")[1].split("(")[0]
                if d_name not in fixed_rules[m]:
                    prob += x[d, m, "Off"] == 1

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=15))
    return status, x

def try_solve():
    # ◀ ステージ設定を根本から見直し
    stages = [
        # まずは「4連勤上限・希望休」を100%守りつつ、繰越残高による自然なズレを認めて解く（これが本命）
        {"label": "① 完全条件（連勤・希望休死守＋残高調整モード）", "enforce_consecutive": True, "allow_imbalance": True},
        # 予備ステージ（これらは通常通り過ぎます）
        {"label": "② 連勤制限緩和", "enforce_consecutive": False, "allow_imbalance": True},
        {"label": "③ 条件緩和（人数優先・ランダム作成）", "enforce_consecutive": False, "allow_imbalance": True},
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
