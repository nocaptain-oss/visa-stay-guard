#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
visa-stay-guard 停留天数计算器（确定性纯函数，无网络/无时钟依赖）

语义级移植自 VisaDay AI 的 StayCalculator / StayWindowPolicy（ArkTS），
口径与原实现一一对应，但用「自然日集合」替代毫秒区间（数据量小，天然去重、无 off-by-one）。

核心口径（源自原应用多年审计沉淀，勿改）：
 1. 天数一律「含入境当日」：9-01 入住、9-20 离境 = 20 天（离境日计入停留）。
 2. 滚动窗口 = [今天-(N-1)天, 今天]，含今天共 N 天；按目的地时区的日历日。
    （调用方传入的 today/as_of 必须已是目的地时区的日历日——见 SKILL.md）
 3. 在途行程占用 = 入境日..今天（含今天）；已离境 = 入境日..离境日（含两端）。
 4. 同池国家共享额度：申根 35 国共用同一个 180/90 池（跳国不清零）。
 5. 本次有效上限 = min(单次上限, 窗口上限 - 同池其它行程已用)，下限 1 天。
 6. 额度释放（滚动）：窗口内最早占用日滑出窗口之日 = 该日 + N；恢复是逐日 +1，
    不是一次性到账（按整段 M 天展示会误导用户超停）。
 7. 风险分档：剩余 ≤3 天 = danger（红）；4~7 天 = near（橙）；已超期恒 danger。

输入（stdin 或 argv[1] 的 JSON 文件路径）：
{
  "country": "ES",                       // 必填：当前国家/地区 ISO 3166-1 alpha-2
  "today": "2026-10-21",                 // 可选：目的地时区今天的日历日，默认取本机时区今天
  "rules": {                             // 必填：该池的停留规则（数字须有官方来源，未收录勿猜）
    "stay_single": 90,                   // x：单次入境最长停留
    "stay_window": 180,                  // N：累计统计窗口（自然年模式填 0）
    "stay_window_max": 90,               // y：窗口内累计上限
    "calendar_year_window": false        // true = 自然年窗口（每年当地 1月1日 清零）
  },
  "trips": [                             // 同池全部历史行程（含当前在途；其它池的不用传）
    {"country": "FR", "entry": "2026-09-01", "exit": "2026-09-20"},   // 已离境
    {"country": "ES", "entry": "2026-10-10", "exit": null}            // 在途（当前）
  ],
  "as_of": null,                         // 可选：评估日（未来入境场景传计划入境日），默认 today
  "visa_type": "旅游签",                 // 可选：用户签证类型（旅游/商务/探亲/留学/其他）
  "visa_source": "dataset",              // 可选：dataset=快照值 / user=签证页单值 / user_rules=用户结构化整套
                                         //       / extended=扩展表(visa-types-extended.json)命中 / web=联网检索
  "visa_days": null,                     // 可选：用户签证页上的停留天数（覆盖 rules.stay_single）
  "reported_days": null,                 // 可选：用户口述的"已待总天数"（用于与行程日期交叉校验）
  "rules_source": null,                  // 可选：规则来源声明（user_rules/web），缺省随 visa_source 推断
  "web_source": null                     // 可选：联网检索来源 {name,url,date}，visa_source=web 时必填
}

输出（stdout JSON）：含 `bottom_line`（一句话结论，AI 必须直接引用此字段向用户播报，
禁止自行从 effective_days/remaining_days 挑数字——两字段语义相近极易拿错）；
含 `confidence` 字段，取值：
- "high"：用户提供了签证页数字（visa_source="user"）
- "medium"：用户确认签证类型与 dataset 匹配
- "low"：用户说不清签证类型，用 dataset 值
- "none"：用户签证类型与 dataset 不匹配

"""
import json
import sys
from datetime import date, timedelta

DANGER_DAYS = 3   # 剩余 ≤3 天 ⇒ danger（红）—— 全应用唯一来源，勿另写魔法数字
NEAR_DAYS = 7     # 剩余 4~7 天 ⇒ near（橙）

# 共享天数池（与 references/pools.md 一致）：成员国的行程合并进同一池计算
POOLS = {
    'SCHENGEN': {
        'members': {'AT','BE','BG','CH','CZ','DE','DK','EE','ES','FI','FO','FR','GI','GL','GR','HR','HU',
                    'IS','IT','LI','LT','LU','LV','MC','MT','NL','NO','PL','PT','RO','SE','SI','SK','SM','VA'},
    },
    'US': {'members': {'US','PR','GU','VI','MP'}},
    'UK': {'members': {'GB','XB'}},
}


def d(s):
    return date.fromisoformat(s) if s else None


def pool_of(code):
    for pid, cfg in POOLS.items():
        if code in cfg['members']:
            return pid
    return None


def same_pool(country, pool, tc):
    """行程 tc 是否计入 country 的池：有池→池内成员；无池→仅该国自身（各国各自计数）。
    空/缺失国家字段的行程一律不计（无归属数据不可信）。"""
    if not tc:
        return False
    if pool:
        return tc in POOLS[pool]['members']
    return tc == country


def occupied_days(trip, today):
    """一条行程占用的自然日集合（含入境当日；在途截至今天，离境日计入停留）。"""
    entry = d(trip.get('entry'))
    if entry is None:
        return set()
    exit_ = d(trip.get('exit'))
    end = exit_ if exit_ is not None else today
    if end < entry:
        return set()
    return {entry + timedelta(days=i) for i in range((end - entry).days + 1)}


def window_dates(as_of, window_days, calendar_year):
    """窗口自然日集合：滚动 = [as_of-(N-1), as_of]；自然年 = 当年1月1日..as_of。"""
    if calendar_year:
        start = date(as_of.year, 1, 1)
    else:
        start = as_of - timedelta(days=window_days - 1)
    return {start + timedelta(days=i) for i in range((as_of - start).days + 1)}


def validate_rules(single, window_n, window_max, cal_year, source):
    """规则四元组合理性校验（结构化录入防呆）：返回警告列表。

    source='dataset' 时只报硬错误（数据已核验过）；'user_rules'/'user' 时加
    录入疑点提示（用户手抄官方页最容易录反/录漏）。"""
    warns = []
    if single <= 0 and window_max <= 0:
        warns.append('❌ 单次上限与窗口上限均为 0——没有可计算的停留上限，'
                     '请提供签证页上的停留天数（结构化录入见 SKILL.md 第 3 步）')
        return warns
    if window_max > 0 and single > window_max and source in ('user_rules', 'user'):
        warns.append(f'⚠️ 录入疑点：单次上限 {single} 天 > 窗口累计上限 {window_max} 天'
                     f'——单次额度实际会被窗口封顶为 {window_max} 天。'
                     f'常见录入错误是把「x/N/y」顺序弄反（如申根应为 90/180/90），请复核')
    if window_n > 0 and window_max <= 0:
        warns.append(f'⚠️ 录入疑点：给了统计窗口 {window_n} 天但没有窗口累计上限——'
                     f'窗口尺无法计算，请补「窗口内最多累计 y 天」')
    if window_max > 0 and window_n <= 0 and not cal_year:
        warns.append('⚠️ 录入疑点：有窗口累计上限但没有窗口跨度（也非自然年模式）——'
                     '滚动窗口需要 N 天跨度，如申根 180')
    if cal_year and window_n > 0:
        warns.append('⚠️ 录入疑点：自然年模式下 stay_window 应为 0（每年 1 月 1 日清零，'
                     '无滚动跨度概念）')
    if window_n > 0 and window_n > 400 and source in ('user_rules', 'user'):
        warns.append(f'⚠️ 录入疑点：窗口跨度 {window_n} 天超过 1 年——罕见口径，请复核'
                     f'（是否把天数录成了月数？如 10 个月应为 300 天左右）')
    return warns


def validate_trips(trips, today):
    """行程日期防呆：倒置 / 未来日期 / 缺字段。返回警告列表。"""
    warns = []
    for i, t in enumerate(trips, 1):
        tc = (t.get('country') or '??').strip().upper()
        entry = d(t.get('entry'))
        exit_ = d(t.get('exit'))
        if entry is None:
            warns.append(f'⚠️ 行程#{i}({tc}) 缺入境日期——该段无法计入，请补齐')
            continue
        if exit_ is not None and exit_ < entry:
            warns.append(f'⚠️ 行程#{i}({tc}) 离境日早于入境日（{exit_} < {entry}）——'
                         f'日期倒置，该段未计入，请复核')
        if entry > today:
            warns.append(f'⚠️ 行程#{i}({tc}) 入境日 {entry} 在今天之后——未来行程应走'
                         f'「未来入境预估」（as_of=计划入境日），已离境行程不应填未来日期')
        if exit_ is not None and exit_ > today:
            warns.append(f'⚠️ 行程#{i}({tc}) 离境日 {exit_} 在今天之后但未标在途——'
                         f'请确认该行程是否已结束（在途应省略 exit）')
    return warns


def run(payload):
    country = payload['country'].strip().upper()
    rules = payload.get('rules') or {}
    single = int(rules.get('stay_single') or 0)
    window_n = int(rules.get('stay_window') or 0)
    window_max = int(rules.get('stay_window_max') or 0)
    cal_year = bool(rules.get('calendar_year_window'))

    today = d(payload.get('today')) or date.today()
    as_of = d(payload.get('as_of')) or today

    pool = pool_of(country)
    trips = payload.get('trips') or []

    # 签证来源与置信度
    # visa_source: dataset=快照值 / user=签证页单值 / user_rules=用户结构化整套
    #              / extended=扩展表命中 / web=联网检索
    visa_source = payload.get('visa_source', 'dataset')
    visa_days = payload.get('visa_days')  # 用户签证页数字（覆盖 rules.stay_single）
    visa_type = payload.get('visa_type', '')  # 用户签证类型（用于置信度判断）
    # rules 来源：结构化录入/扩展表/联网检索时，调用方（模型）在此声明，输出如实透传
    rules_source = payload.get('rules_source') or (
        'user_rules' if visa_source == 'user_rules'
        else ('extended' if visa_source == 'extended'
              else ('web' if visa_source == 'web' else 'dataset')))

    # 用户签证页数字覆盖 dataset 值
    if visa_days and visa_days > 0:
        single = int(visa_days)
        if visa_source not in ('user_rules', 'extended', 'web'):
            visa_source = 'user'

    # 置信度判定
    if visa_source in ('user', 'user_rules', 'extended'):
        confidence = 'high'
    elif visa_source == 'web':
        confidence = 'medium'  # 联网检索到官方页面也不及用户签证页可靠，恒 medium
    elif visa_type and visa_type.strip():
        confidence = 'medium'
    elif payload.get('as_of') is not None:
        confidence = 'medium'  # 未来入境场景默认 medium
    else:
        confidence = 'low'  # 用户没说签证类型，用 dataset 值

    # 全部行程占用（日集合自动去重：重叠段只算一次）
    all_days = set()
    current = None
    for t in trips:
        tc = (t.get('country') or '').strip().upper()
        if not same_pool(country, pool, tc):
            continue  # 非同池/无归属行程不计入（无池国家仅统计本国行程）
        all_days |= occupied_days(t, today)
        if tc == country and not t.get('exit'):
            current = t

    win = window_dates(as_of, window_n, cal_year)
    used = len(all_days & win)

    out = {
        'country': country,
        'pool': pool,
        'today': today.isoformat(),
        'as_of': as_of.isoformat(),
        'rules_effective': {'stay_single': single, 'stay_window': window_n,
                            'stay_window_max': window_max,
                            'calendar_year_window': cal_year,
                            'source': rules_source},
        'visa_info': {
            'visa_type': visa_type or None,
            'visa_source': visa_source,
            'confidence': confidence,
            'web_source': payload.get('web_source') or None,  # {name,url,date} 联网检索来源
        },
        'window': None,
        'single': None,
        'bottom_line': None,
        'status': 'unknown',
        'alerts': [],
    }

    # 规则四元组合理性 + 行程日期防呆（结构化录入的机器校验层）
    out['alerts'].extend(validate_rules(single, window_n, window_max, cal_year, rules_source))
    out['alerts'].extend(validate_trips(trips, today))

    # 用户口述总天数交叉校验（实战教训：用户说 60 天、日期算出 82 天，必须指出差异）
    reported = payload.get('reported_days')
    if reported:
        try:
            reported = int(reported)
        except (TypeError, ValueError):
            reported = None
    if reported and reported > 0:
        actual = used if window_max > 0 else len(all_days)
        if abs(actual - reported) >= 1:
            scope = (f'{window_n} 天窗口内' if window_max > 0 and not cal_year
                     else ('本年度' if cal_year else '全部行程'))
            out['alerts'].append(
                f'⚠️ 口径差异：您说自己待了 {reported} 天，但按行程日期（{scope}，含入境当日）'
                f'算出 {actual} 天。数字对不上说明日期或记忆有误，请核对出入境日期后再下结论。')

    # ── 窗口尺 ──
    if window_max > 0:
        remaining = max(window_max - used, 0)
        win_info = {'used': used, 'remaining': remaining,
                    'window_days': (0 if cal_year else window_n),
                    'mode': ('calendar_year' if cal_year else 'rolling')}
        if cal_year:
            refresh = date(as_of.year + 1, 1, 1)
            win_info['refresh_date'] = refresh.isoformat()
            win_info['refresh_days'] = used          # 自然年：次年 1/1 一次性清零
            win_info['refresh_note'] = '次年 1 月 1 日（目的地时区）年度额度全部恢复'
        else:
            in_win = sorted(all_days & win)
            if in_win:
                earliest = in_win[0]
                refresh = earliest + timedelta(days=window_n)
                win_info['refresh_date'] = refresh.isoformat()
                win_info['refresh_days'] = 1          # 滚动：逐日回补，每天 +1
                win_info['refresh_note'] = (
                    f'额度逐日回补：{refresh.isoformat()} 起每天恢复 1 天'
                    f'（{earliest.isoformat()} 的占用滑出窗口）；不是一次性到账')
            else:
                win_info['refresh_date'] = None
                win_info['refresh_note'] = '窗口内无占用'
        out['window'] = win_info
        if remaining == 0:
            out['alerts'].append(f'窗口额度已用尽（{used}/{window_max}）——再入境前须等额度释放')
        elif remaining <= NEAR_DAYS:
            out['alerts'].append(f'窗口剩余仅 {remaining} 天（上限 {window_max}）')

    # ── 单次尺 ──
    if current is not None and single > 0:
        entry = d(current['entry'])
        # 同池其它行程已用（剔除当前这条：按国家粒度剔除等价于按记录剔除）
        others = set()
        for t in trips:
            tc = (t.get('country') or '').strip().upper()
            if tc == country and not t.get('exit'):
                continue
            if not same_pool(country, pool, tc):
                continue
            others |= occupied_days(t, today)
        others_used = len(others & win)

        if window_max > 0:
            eff = max(min(single, window_max - others_used), 1)
        else:
            eff = single
        last_allowed = entry + timedelta(days=eff - 1)
        remaining = (last_allowed - today).days
        remaining = remaining if remaining > 0 else 0
        overdue = today > last_allowed

        if overdue:
            status = 'overdue'
        elif remaining <= DANGER_DAYS:
            status = 'danger'
        elif remaining <= NEAR_DAYS:
            status = 'near'
        else:
            status = 'normal'

        out['single'] = {
            'entry': entry.isoformat(),
            'effective_days': eff,
            'last_allowed_day': last_allowed.isoformat(),
            'remaining_days': remaining,      # 不含今天的剩余可待天数
            'overdue': overdue,
        }
        out['status'] = status
        if overdue:
            over_by = (today - last_allowed).days
            out['alerts'].append(f'⚠️ 已超期 {over_by} 天（最晚离境日 {last_allowed.isoformat()}）——请立即离境或咨询移民律师')
        elif remaining <= DANGER_DAYS:
            out['alerts'].append(f'🔴 危险：只剩 {remaining} 天（最晚离境日 {last_allowed.isoformat()}）')
        elif remaining <= NEAR_DAYS:
            out['alerts'].append(f'🟠 临近：剩 {remaining} 天（最晚离境日 {last_allowed.isoformat()}）')
    elif single > 0 and not current:
        # 未来入境场景：as_of = 计划入境日
        eff = max(min(single, window_max - used), 1) if window_max > 0 else single
        out['single'] = {
            'entry': as_of.isoformat(),
            'effective_days': eff,
            'last_allowed_day': (as_of + timedelta(days=eff - 1)).isoformat(),
            'remaining_days': eff,
            'overdue': False,
            'note': '未来入境预估（按入境日窗口余量封顶）',
        }
        out['status'] = 'planning'

    # ── 一句话结论（AI 播报必须直接引用此字段；杜绝误把 effective_days 当剩余天数） ──
    s = out['single']
    if s is not None and out['status'] != 'planning':
        if s['overdue']:
            over = (today - d(s['last_allowed_day'])).days
            out['bottom_line'] = (f'⚠️ 已超期 {over} 天（最晚离境日 {s["last_allowed_day"]}）'
                                  f'——请立即离境或咨询移民律师')
        else:
            out['bottom_line'] = (f'最晚离境日 {s["last_allowed_day"]}，'
                                  f'还能再待 {s["remaining_days"]} 天（不含今天）')
    elif s is not None:  # planning（未来入境预估）
        capped = '（按入境日窗口余量封顶）' if window_max > 0 else ''
        out['bottom_line'] = (f'计划 {s["entry"]} 入境，本次最多可待 {s["effective_days"]} 天，'
                              f'最晚 {s["last_allowed_day"]} 离境{capped}')
    elif out['window'] is not None:
        w = out['window']
        scope = '本年度' if w['mode'] == 'calendar_year' else f'{w["window_days"]} 天窗口内'
        out['bottom_line'] = (f'当前不在 {country}：{scope}已用 {w["used"]} 天、剩余 {w["remaining"]} 天')
    else:
        out['bottom_line'] = '规则数据不含停留上限，无法计算——请提供签证页数字'

    if not out['alerts']:
        out['alerts'].append('✅ 状态正常')
    return out


def main():
    if len(sys.argv) > 1:
        payload = json.load(open(sys.argv[1], encoding='utf-8'))
    else:
        payload = json.load(sys.stdin)
    print(json.dumps(run(payload), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
