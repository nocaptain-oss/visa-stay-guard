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
 5. 当前/未来停留从起点逐日模拟：每天同时检查单次上限与滚动窗口占用；
    窗口释放会在停留期间逐日生效，不能用入境日余量一次性封顶。
 6. 额度释放（滚动）：窗口内最早占用日滑出窗口之日 = 该日 + N；恢复是逐日 +1，
    不是一次性到账（按整段 M 天展示会误导用户超停）。
 7. 风险分档：剩余 ≤3 天 = danger（红）；4~7 天 = near（橙）；已超期恒 danger。

输入（stdin 或 argv[1] 的 JSON 文件路径）：
{
  "country": "ES",                       // 必填：当前国家/地区 ISO 3166-1 alpha-2
  "today": "2026-10-21",                 // 必填：目的地时区今天的日历日，禁止回退本机日期
  "passport_nationality": "CN",          // 内置规则自动计算仅支持 CN
  "passport_type": "ordinary",           // ordinary / diplomatic / service / other
  "residence_status": "visitor",         // visitor / student / worker / resident / other
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
                                         //       / extended=扩展表(visa-types-extended.txt)命中
                                         //       / aggregated=聚合表(visa-types-aggregated.txt)命中，恒 low
                                         //       / web=联网检索
  "visa_days": null,                     // 可选：用户签证页上的停留天数（覆盖 rules.stay_single）
  "reported_days": null,                 // 可选：用户口述的"已待总天数"（用于与行程日期交叉校验）
  "rules_source": null,                  // 可选：规则来源声明（user_rules/web），缺省随 visa_source 推断
  "web_source": null                     // 可选：联网检索来源 {name,url,date}，visa_source=web 时必填
}

输出（stdout JSON）：`status` 为 `needs_confirmation` 或 `blocked` 时，`bottom_line` 必为 null；
用户可见结论只能来自非空 `bottom_line`。`needs_confirmation` 时先问清 `questions`，
不得从 `candidate` 播报数字。`blocked` 表示窗口余量为 0，不能说“还能待 1 天”。
含 `confidence` 字段，取值：
- "high"：用户提供了签证页数字（visa_source="user"）或命中原生扩展表（extended）
- "medium"：用户确认签证类型与 dataset 匹配 / 联网检索到官方页面
- "low"：用户说不清签证类型用 dataset 值 / 命中聚合表（aggregated，未逐条官方核验）
- "none"：用户签证类型与 dataset 不匹配

"""
import json
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lunar import cn_num, is_lunar, parse_lunar, parse_lunar_day, solar_to_lunar  # noqa: E402  农历换算（纯查表，离线）

DANGER_DAYS = 3   # 剩余 ≤3 天 ⇒ danger（红）—— 全应用唯一来源，勿另写魔法数字
NEAR_DAYS = 7     # 剩余 4~7 天 ⇒ near（橙）

SUPPORTED_PASSPORT_NATIONALITY = 'CN'
SUPPORTED_PASSPORT_TYPE = 'ordinary'
SUPPORTED_RESIDENCE_STATUS = 'visitor'
RESIDENCE_ALIASES = {
    'visitor': 'visitor', 'tourist': 'visitor', 'short_term': 'visitor',
    '旅游': 'visitor', '短期访客': 'visitor',
    'student': 'student', '学生': 'student', '留学': 'student',
    'worker': 'worker', 'work': 'worker', '工作': 'worker',
    'resident': 'resident', 'residence': 'resident', '居留': 'resident', '定居': 'resident',
}

# 共享天数池（与 references/pools.md 一致）：成员国的行程合并进同一池计算
POOLS = {
    'SCHENGEN': {
        'members': {'AT','BE','BG','CH','CZ','DE','DK','EE','ES','FI','FO','FR','GI','GL','GR','HR','HU',
                    'IS','IT','LI','LT','LU','LV','MC','MT','NL','NO','PL','PT','RO','SE','SI','SK','SM','VA'},
    },
    'US': {'members': {'US','PR','GU','VI','MP'}},
    'UK': {'members': {'GB','XB'}},
}


class InputError(Exception):
    """带用户可读 hint 的输入错误：main 原样转成 JSON，模型照着 hint 重试。"""

    def __init__(self, msg, hint=None):
        super().__init__(msg)
        self.hint = hint


_CN_DATE_RUN = re.compile(r'[零〇一二三四五六七八九十廿卅]{1,3}')

# 汉字月日且日不带「号/日」后缀（可带四位年份）：「五月十八」「2026年八月十五」。
# 「初X」与农历前缀已被 is_lunar 拦走，落到这里的都是无标记汉字日——历法不明。
_AMBIG_CN_DAY = re.compile(
    r'^(?:\d{4}\s*年)?\s*[零〇一二三四五六七八九十廿卅]{1,3}\s*月\s*'
    r'[零〇一二三四五六七八九十廿卅]{1,3}\s*$')


def _cn_repl(m):
    v = cn_num(m.group())
    return str(v) if v is not None else m.group()


_INFERRED_DATES = {}   # 原文 → (公历日, 推定说明)：一切「缺月/缺年靠上下文推定」的日期，输出必须经用户二次确认


def _guess_day_only(n, near, bias, lunar):
    """纯日号（无月无年）→ 30 天窗口内最近匹配日。past=从今天往回，future=从今天往前。

    公历按 cand.day == n 匹配；农历按 solar_to_lunar(cand) 的农历日匹配。"""
    step = 1 if bias == 'future' else -1
    for k in range(0, 31):
        cand = near + timedelta(days=step * k)
        if lunar:
            got = solar_to_lunar(cand)
            if got and got[2] == n:
                return cand
        elif cand.day == n:
            return cand
    return None


def d(s, near=None, bias=None):
    """宽容的日期解析。

    公历：`2026-03-01` / `2026/3/1` / `2026.3.1` / `2026年3月1日` / `20260301`，
    也认汉字数字（`2026年八月十五` = 8 月 15 日）。
    农历：带「农历/阴历」前缀、含「闰X月」、或含「初X」的一律按农历换算——
    公历没有「初五」这种说法，看到就只可能指农历，不去猜。
    缺年份：near（今天）给了就取离 near 最近的一次该农历日（春节前说「腊月初五」
    指刚过去/即将到来的那次，公历年直代会错一年），换算记入 _INFERRED_DATES，
    输出必须先经用户确认，确认前不下最终结论。
    缺月份的纯日号（`18号` 公历 / `初五` 农历）：near 给了就在 30 天窗口内按
    bias 方向取最近匹配日（对话带「计划/打算」等未来语时调用方传 bias='future'），
    同样记入 _INFERRED_DATES 须确认；near 没给则抛错。
    缺年份且带月份的公历写法（`3月1日`）仍抛错，由 main 兜成 JSON。"""
    if not s:
        return None
    if isinstance(s, date):
        return s
    t = str(s).strip()
    if is_lunar(t):
        out, why, inferred = parse_lunar(t, near=near, bias=bias)
        if out is None:
            dn = parse_lunar_day(t)
            if dn is not None and near is not None:
                got = _guess_day_only(dn, near, bias, lunar=True)
                if got is not None:
                    _INFERRED_DATES[t] = (
                        got, '「%s」缺月份，按农历%s 30 天内最近推定' % (t, '未来' if bias == 'future' else '过去'))
                    return got
            raise InputError(
                '农历日期「%s」换算不了（%s）' % (t, why),
                '农历要写到月和日，如「农历八月十五」「五月初五」；只说「初五」定不了'
                '是哪个月。也可以直接给公历日期。')
        if inferred:
            _INFERRED_DATES[t] = (out, '%s，缺年份按最近出现代年' % why)
        return out
    # 「X月十几/二十几」：汉字月日、无「初X」无前缀、无「号/日」公历后缀——历法不明，先追问
    if _AMBIG_CN_DAY.match(t):
        raise InputError(
            '日期「%s」历法不明（汉字日未带「号/日」公历后缀）' % t,
            '「五月十八」这种写法公历农历都有可能——先问用户是公历还是农历，'
            '按用户明确的口径重传：公历补全年份按 2026-05-18 传；农历传'
            '「2026年农历五月十八」原文由脚本换算。')
    t = _CN_DATE_RUN.sub(_cn_repl, t)      # 先归一汉字数字，再换分隔符
    t = t.replace('号', '日')
    m_day = re.match(r'^(\d{1,2})[日]?$', t.strip())
    if m_day and near is not None:
        dn = int(m_day.group(1))
        if 1 <= dn <= 31:
            got = _guess_day_only(dn, near, bias, lunar=False)
            if got is not None:
                _INFERRED_DATES[str(s).strip()] = (
                    got, '「%s」缺月份，按公历%s 30 天内最近推定' % (str(s).strip(), '未来' if bias == 'future' else '过去'))
                return got
    for sep in ('年', '月', '日'):
        t = t.replace(sep, '-')
    t = t.replace('/', '-').replace('.', '-').rstrip('-')
    if len(t) == 8 and t.isdigit():
        return date(int(t[:4]), int(t[4:6]), int(t[6:]))
    parts = [p for p in t.split('-') if p]
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return date(*(int(p) for p in parts))
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        raise InputError(
            '日期「%s」缺年份' % s,
            '「5月18日」这种只到月日的写法定不了年份，先从对话里补全年份'
            '（如 2026-05-18）再传；不要自己猜年份。')
    return date.fromisoformat(t)


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


def occupied_days(trip, today, bias=None):
    """一条行程占用的自然日集合（含入境当日；在途截至今天，离境日计入停留）。"""
    entry = d(trip.get('entry'), near=today, bias=bias)
    if entry is None:
        return set()
    exit_ = d(trip.get('exit'), near=today, bias=bias)
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


def simulate_stay(start, single, window_n, window_max, cal_year, base_occupied):
    """从 start 起逐日模拟，返回 (last_allowed_day, stay_days, first_blocked_day)。"""
    if single <= 0:
        return None, 0, start
    last = None
    candidate_days = set()
    for offset in range(single):
        candidate = start + timedelta(days=offset)
        candidate_days.add(candidate)
        if window_max > 0:
            used = len((base_occupied | candidate_days) & window_dates(candidate, window_n, cal_year))
            if used > window_max:
                return last, offset, candidate
        last = candidate
    return last, single, start + timedelta(days=single)


def earliest_entry(start, single, window_n, window_max, cal_year, base_occupied):
    """未来计划在窗口耗尽时，找到最早可合法入境一天的公历日。"""
    if single <= 0 or window_max <= 0:
        return None
    for offset in range(max(window_n, 366) + 1):
        candidate = start + timedelta(days=offset)
        if len(base_occupied & window_dates(candidate, window_n, cal_year)) < window_max:
            return candidate
    return None


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
    if cal_year and window_n not in (0, 365):
        # 365 是「日历年」的等价写法（聚合表数据就是这么存的），不冲突
        warns.append('⚠️ 录入疑点：自然年模式下 stay_window 应为 0（每年 1 月 1 日清零，'
                     '无滚动跨度概念）；填 365 也可接受，其它值请复核')
    if cal_year and window_max <= 0:
        warns.append('⚠️ 录入疑点：标了自然年模式但窗口累计上限是 0——年度额度限制不生效，'
                     '结果会把年度上限漏掉（如印度 e-旅游单次 90 天、日历年累计 180 天，'
                     '应填 stay_window_max=180）')
    if window_n > 0 and window_n > 400 and source in ('user_rules', 'user'):
        warns.append(f'⚠️ 录入疑点：窗口跨度 {window_n} 天超过 1 年——罕见口径，请复核'
                     f'（是否把天数录成了月数？如 10 个月应为 300 天左右）')
    return warns


def validate_trips(trips, today, as_of=None, bias=None):
    """行程日期防呆：倒置 / 未来日期 / 缺字段。返回警告列表。"""
    warns = []
    if as_of is not None and as_of < today:
        warns.append(f'⚠️ 评估日 as_of={as_of} 早于今天 today={today}——「未来入境预估」'
                     f'必须填计划入境日，不要把历史入境日填进 as_of；本次结果的时间基准可能错位')
    for i, t in enumerate(trips, 1):
        tc = (t.get('country') or '??').strip().upper()
        entry = d(t.get('entry'), near=today, bias=bias)
        exit_ = d(t.get('exit'), near=today, bias=bias)
        if entry is None:
            warns.append(f'⚠️ 行程#{i}({tc}) 缺入境日期——该段无法计入，请补齐')
            continue
        if exit_ is not None and exit_ < entry:
            warns.append(f'⚠️ 行程#{i}({tc}) 离境日早于入境日（{exit_} < {entry}）——'
                         f'日期倒置，该段未计入，请复核')
        if entry > today and t.get('exit'):
            warns.append(f'⚠️ 行程#{i}({tc}) 入境日 {entry} 在今天之后——未来行程应走'
                         f'「未来入境预估」（as_of=计划入境日），已离境行程不应填未来日期')
        if exit_ is not None and exit_ > today:
            warns.append(f'⚠️ 行程#{i}({tc}) 离境日 {exit_} 在今天之后但未标在途——'
                         f'请确认该行程是否已结束（在途应省略 exit）')
    return warns


def run(payload):
    global _INFERRED_DATES
    _INFERRED_DATES = {}

    country = payload['country'].strip().upper()
    rules = payload.get('rules') or {}
    single = int(rules.get('stay_single') or 0)
    window_n = int(rules.get('stay_window') or 0)
    window_max = int(rules.get('stay_window_max') or 0)
    raw_cal_year = rules.get('calendar_year_window', False)
    if not isinstance(raw_cal_year, bool):
        raise InputError(
            'calendar_year_window 必须是 JSON true 或 false',
            f'收到的是 {json.dumps(raw_cal_year, ensure_ascii=False)}；不要传字符串 "false"。',
        )
    cal_year = raw_cal_year

    raw_today = payload.get('today')
    if raw_today is None or str(raw_today).strip() == '':
        raise InputError(
            '缺少 today（目的地时区的今天）',
            '计算停留天数必须显式传入目的地时区的 today；不要使用运行机器的本地日期。',
        )
    today = d(raw_today)
    bias = payload.get('date_bias')
    if 'as_of' in payload:
        raw_as_of = payload.get('as_of')
        if raw_as_of is not None and str(raw_as_of).strip() == '':
            raise InputError(
                'as_of 不能是空字符串',
                '不需要评估日时请省略 as_of 或传 null；计划未来入境时传具体日期。',
            )
        as_of = d(raw_as_of, near=today, bias=bias) if raw_as_of is not None else today
    else:
        as_of = today

    pool = pool_of(country)
    trips = payload.get('trips') or []

    # 签证来源与置信度
    # visa_source: dataset=快照值 / user=签证页单值 / user_rules=用户结构化整套
    #              / extended=扩展表命中 / aggregated=聚合表命中(confidence=low) / web=联网检索
    visa_source = payload.get('visa_source') or 'dataset'
    allowed_sources = {'dataset', 'user', 'user_rules', 'extended', 'aggregated', 'web'}
    if visa_source not in allowed_sources:
        raise InputError(
            f'未知 visa_source: {json.dumps(visa_source, ensure_ascii=False)}',
            'visa_source 只能是 dataset/user/user_rules/extended/aggregated/web。',
        )
    web_source = payload.get('web_source')
    if visa_source == 'web':
        if (
            not isinstance(web_source, dict)
            or not web_source.get('name')
            or not web_source.get('url')
            or not web_source.get('date')
        ):
            raise InputError(
                'visa_source=web 时必须提供完整 web_source',
                'web_source 需要 name、url、date 三个字段。',
            )
    visa_days = payload.get('visa_days')  # 用户签证页数字（覆盖 rules.stay_single）
    visa_type = payload.get('visa_type', '')  # 用户签证类型（用于置信度判断）
    # rules 来源：结构化录入/扩展表/联网检索时，调用方（模型）在此声明，输出如实透传
    inferred_rules_source = (
        'user_rules' if visa_source == 'user_rules'
        else ('aggregated' if visa_source == 'aggregated'
              else ('extended' if visa_source == 'extended'
                    else ('web' if visa_source == 'web' else 'dataset'))))
    explicit_rules_source = payload.get('rules_source')
    if explicit_rules_source and explicit_rules_source != inferred_rules_source:
        raise InputError(
            f'rules_source={explicit_rules_source!r} 与 visa_source={visa_source!r} 冲突',
            'rules_source 可省略；如显式传入，必须与 visa_source 对应的规则来源一致。',
        )
    rules_source = explicit_rules_source or inferred_rules_source

    passport_nationality = str(payload.get('passport_nationality') or '').strip().upper()
    passport_type_raw = str(payload.get('passport_type') or '').strip().lower()
    passport_type = ('ordinary' if passport_type_raw in ('ordinary', '普通', '普通护照')
                     else passport_type_raw)
    residence_raw = str(payload.get('residence_status') or '').strip().lower()
    residence_status = RESIDENCE_ALIASES.get(residence_raw, residence_raw)

    questions = []
    if rules_source != 'user_rules':
        if not passport_nationality:
            questions.append('你持哪国护照？')
        if not passport_type:
            questions.append('你的护照类型是普通护照，还是外交/公务等其他护照？')
        if not residence_status:
            questions.append('你目前在当地是短期访客、学生、工作还是长期居留身份？')
        if (
            passport_nationality
            and passport_type
            and residence_status
            and (
                passport_nationality != SUPPORTED_PASSPORT_NATIONALITY
                or passport_type != SUPPORTED_PASSPORT_TYPE
                or residence_status != SUPPORTED_RESIDENCE_STATUS
            )
        ):
            questions.append(
                '当前内置规则只适用于中国普通护照的短期访客。请提供签证页上的允许停留天数，'
                '或确认完整规则后按 user_rules 重算。'
            )

    # 用户签证页数字覆盖 dataset 值
    if visa_days and visa_days > 0:
        single = int(visa_days)
        if visa_source not in ('user_rules', 'extended', 'aggregated', 'web'):
            visa_source = 'user'

    # 置信度判定
    if visa_source in ('user', 'user_rules', 'extended'):
        confidence = 'high'
    elif visa_source == 'aggregated':
        confidence = 'low'  # 聚合表未逐条官方核验，恒 low（见 visa-types-aggregated._meta）
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
    relevant_trips = []
    open_trips = []
    future_open_trips = []
    for t in trips:
        tc = (t.get('country') or '').strip().upper()
        if not same_pool(country, pool, tc):
            continue  # 非同池/无归属行程不计入（无池国家仅统计本国行程）
        entry_date = d(t.get('entry'), near=today, bias=bias)
        exit_date = d(t.get('exit'), near=today, bias=bias)
        normalized = {
            **t,
            'country': tc,
            'entry_date': entry_date,
            'exit_date': exit_date,
        }
        relevant_trips.append(normalized)
        all_days |= occupied_days(t, today, bias)
        if entry_date is not None and entry_date <= today and not t.get('exit'):
            open_trips.append(normalized)
        elif entry_date is not None and entry_date > today and not t.get('exit'):
            future_open_trips.append(normalized)

    current = None
    continuous_start = None
    continuous_days = set()
    state_reasons = []
    if len(open_trips) > 1:
        state_reasons.append('同一共享停留池存在多个已开始的未离境行程，请确认哪些是真实当前行程。')
    if len(future_open_trips) > 1:
        state_reasons.append('同一共享停留池存在多个未来未离境计划，请确认要计算哪一次入境。')
    if open_trips:
        current = max(open_trips, key=lambda t: t['entry_date'])
        continuous_start = current['entry_date']
        relevant_trips.sort(key=lambda t: t['entry_date'] or today)
        current_index = next(
            i for i, t in enumerate(relevant_trips)
            if t['country'] == current['country']
            and t['entry_date'] == current['entry_date']
            and not t.get('exit')
        )
        for previous in reversed(relevant_trips[:current_index]):
            if (
                previous['exit_date'] is not None
                and previous['entry_date'] is not None
                and previous['exit_date'] + timedelta(days=1) >= continuous_start
            ):
                continuous_start = min(continuous_start, previous['entry_date'])
            else:
                break
        continuous_days = {
            day for t in relevant_trips
            if t['entry_date'] is not None
            and t['entry_date'] >= continuous_start
            for day in occupied_days(t, today, bias)
        }
    if current and future_open_trips:
        state_reasons.append('当前已有在途行程，同时提供了未来入境计划；请拆成两个场景分别确认。')
    if current and as_of > today:
        state_reasons.append('当前已有在途行程，as_of 却是未来日期；请先确认当前行程还是未来计划。')
    if not current and len(future_open_trips) == 1:
        planned_entry = future_open_trips[0]['entry_date']
        if payload.get('as_of') is None:
            as_of = planned_entry
        elif as_of != planned_entry:
            state_reasons.append(
                f'未来行程入境日是 {planned_entry.isoformat()}，但 as_of 是 {as_of.isoformat()}；'
                f'请确认按哪一天计算。'
            )

    win = window_dates(as_of, window_n, cal_year)
    used = len(all_days & win)

    out = {
        'country': country,
        'pool': pool,
        'today': today.isoformat(),
        'as_of': as_of.isoformat(),
        'current_country': current['country'] if current else None,
        'current_pool': pool if current else None,
        'current_pool_entry': continuous_start.isoformat() if continuous_start else None,
        'identity': {
            'passport_nationality': passport_nationality or None,
            'passport_type': passport_type or None,
            'residence_status': residence_status or None,
            'supported_profile': (
                passport_nationality == SUPPORTED_PASSPORT_NATIONALITY
                and passport_type == SUPPORTED_PASSPORT_TYPE
                and residence_status == SUPPORTED_RESIDENCE_STATUS
            ),
        },
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
        'questions': questions,
        'confirmation_reasons': [],
    }

    # 规则四元组合理性 + 行程日期防呆（结构化录入的机器校验层）
    rule_alerts = validate_rules(single, window_n, window_max, cal_year, rules_source)
    trip_alerts = validate_trips(trips, today, as_of, bias)
    out['alerts'].extend(rule_alerts)
    out['alerts'].extend(trip_alerts)
    out['confirmation_reasons'].extend(
        alert for alert in rule_alerts + trip_alerts
        if alert.startswith(('⚠️', '❌'))
    )
    out['confirmation_reasons'].extend(state_reasons)

    # 用户口述总天数交叉校验（实战教训：用户说 60 天、日期算出 82 天，必须指出差异）
    reported = payload.get('reported_days')
    if reported not in (None, ''):
        raw_reported = reported.strip() if isinstance(reported, str) else reported
        if (
            isinstance(raw_reported, bool)
            or not isinstance(raw_reported, (int, str))
            or (isinstance(raw_reported, str) and not re.fullmatch(r'\d+', raw_reported))
        ):
            raise InputError(
                'reported_days 必须是正整数',
                f'收到 {json.dumps(payload.get("reported_days"), ensure_ascii=False)}；'
                f'请传用户口述的整数天数，或省略该字段。',
            )
        reported = int(raw_reported)
        if reported <= 0:
            raise InputError(
                'reported_days 必须是正整数',
                '已待 0 天请省略 reported_days；不要把非正数当口述天数。',
            )
    if reported and reported > 0:
        actual = used if window_max > 0 else len(all_days)
        if abs(actual - reported) >= 1:
            scope = (f'{window_n} 天窗口内' if window_max > 0 and not cal_year
                     else ('本年度' if cal_year else '全部行程'))
            out['alerts'].append(
                f'⚠️ 口径差异：您说自己待了 {reported} 天，但按行程日期（{scope}，含入境当日）'
                f'算出 {actual} 天。数字对不上说明日期或记忆有误，请核对出入境日期后再下结论。')
            out['confirmation_reasons'].append(
                f'口径差异：用户口述 {reported} 天，行程日期实算 {actual} 天。'
                f'请确认以哪个为准。'
            )

    # ── 窗口尺 ──
    if window_max > 0:
        remaining = max(window_max - used, 0)
        win_info = {'used': used, 'remaining': remaining, 'cap': window_max,
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
        entry = continuous_start
        base_occupied = all_days - continuous_days
        last_allowed, stay_days, _ = simulate_stay(
            entry, single, window_n, window_max, cal_year, base_occupied)
        blocked = stay_days == 0
        overdue = bool(last_allowed and today > last_allowed)
        remaining = max((last_allowed - today).days, 0) if last_allowed else 0

        if blocked:
            status = 'blocked'
        elif overdue:
            status = 'overdue'
        elif remaining <= DANGER_DAYS:
            status = 'danger'
        elif remaining <= NEAR_DAYS:
            status = 'near'
        else:
            status = 'normal'

        out['single'] = {
            'entry': entry.isoformat(),
            'effective_days': stay_days,
            'last_allowed_day': last_allowed.isoformat() if last_allowed else None,
            'remaining_days': remaining,      # 不含今天的剩余可待天数
            'overdue': overdue,
            'blocked': blocked,
        }
        out['status'] = status
        if blocked:
            out['alerts'].append(
                f'⚠️ 当前停留起点 {entry.isoformat()} 已无可用窗口额度；'
                f'不能给出合法剩余天数，请立即核对停留依据并咨询专业人士')
        elif overdue:
            over_by = (today - last_allowed).days
            out['alerts'].append(f'⚠️ 已超期 {over_by} 天（最晚离境日 {last_allowed.isoformat()}）——请立即离境或咨询移民律师')
        elif remaining <= DANGER_DAYS:
            out['alerts'].append(f'🔴 危险：只剩 {remaining} 天（最晚离境日 {last_allowed.isoformat()}）')
        elif remaining <= NEAR_DAYS:
            out['alerts'].append(f'🟠 临近：剩 {remaining} 天（最晚离境日 {last_allowed.isoformat()}）')
    elif single > 0 and not current:
        # 未来入境场景：as_of = 计划入境日
        last_allowed, stay_days, _ = simulate_stay(
            as_of, single, window_n, window_max, cal_year, all_days)
        blocked = stay_days == 0
        out['single'] = {
            'entry': as_of.isoformat(),
            'effective_days': stay_days,
            'last_allowed_day': last_allowed.isoformat() if last_allowed else None,
            'remaining_days': stay_days,
            'overdue': False,
            'blocked': blocked,
            'note': '未来入境逐日预估',
        }
        if blocked:
            out['status'] = 'blocked'
            next_entry = earliest_entry(
                as_of, single, window_n, window_max, cal_year, all_days)
            out['single']['earliest_allowed_date'] = (
                next_entry.isoformat() if next_entry else None)
            out['alerts'].append(
                f'⚠️ 计划入境日 {as_of.isoformat()} 窗口额度不足，不能入境；'
                f'最早可再入境日 {next_entry.isoformat() if next_entry else "待核算"}')
        else:
            out['status'] = 'planning'

    # ── 身份适用范围门禁：内置规则不适用于非中国普通护照/非短期访客 ──
    if questions:
        out['candidate'] = {
            'window': out['window'],
            'single': out['single'],
            'bottom_line': None,
        }
        out['window'] = None
        out['single'] = None
        out['status'] = 'needs_confirmation'
        return out

    # ── 一句话结论（AI 播报必须直接引用此字段；杜绝误把 effective_days 当剩余天数） ──
    s = out['single']
    if out['status'] == 'blocked':
        out['bottom_line'] = None
    elif s is not None and out['status'] != 'planning':
        if s['overdue']:
            over = (today - d(s['last_allowed_day'])).days
            out['bottom_line'] = (f'⚠️ 已超期 {over} 天（最晚离境日 {s["last_allowed_day"]}）'
                                  f'——请立即离境或咨询移民律师')
        else:
            if current and current['country'] != country:
                lead = (
                    f'当前已在 {pool or current["country"]} 停留'
                    f'（连续停留自 {s["entry"]} 起算）；'
                )
            elif current and current['entry_date'] != continuous_start:
                lead = f'当前连续停留自 {s["entry"]} 起算；'
            else:
                lead = ''
            out['bottom_line'] = (f'{lead}最晚离境日 {s["last_allowed_day"]}，'
                                  f'还能再待 {s["remaining_days"]} 天（不含今天）')
    elif s is not None:  # planning（未来入境预估）
        capped = '（按入境日窗口余量封顶）' if window_max > 0 else ''
        # as_of 缺省等于 today 时不能称「计划今天入境」——已离境用户问「下次能待多久」会误读
        if as_of > today:
            lead = f'计划 {s["entry"]} 入境'
        elif as_of == today:
            lead = f'若今天（{s["entry"]}）入境'
        else:
            lead = f'若 {s["entry"]} 入境'
        out['bottom_line'] = (f'{lead}，本次最多可待 {s["effective_days"]} 天，'
                              f'最晚 {s["last_allowed_day"]} 离境{capped}')
    elif out['window'] is not None:
        w = out['window']
        scope = '本年度' if w['mode'] == 'calendar_year' else f'{w["window_days"]} 天窗口内'
        out['bottom_line'] = (f'当前不在 {country}：{scope}已用 {w["used"]} 天、剩余 {w["remaining"]} 天')
    else:
        out['bottom_line'] = '规则数据不含停留上限，无法计算——请提供签证页数字'

    # ── 推定日期（缺月/缺年靠上下文）：结果是推测，必须经用户二次确认（与口径差异同级，确认前不下结论） ──
    for raw, (got, note) in _INFERRED_DATES.items():
        out['alerts'].insert(
            0, f'⚠️ 日期「{raw}」不完整，已推定为 {got.isoformat()}（{note}）'
               f'——请先向用户确认这个公历日期，确认前不下最终结论')
        out['confirmation_reasons'].append(
            f'日期「{raw}」被推定为 {got.isoformat()}，请向用户确认。'
        )

    # ── date_bias 非法值告警（防 typo 静默降级：'futrue'/数组/大小写都会被 == 比较无声吞掉） ──
    if bias is not None and bias not in ('past', 'future'):
        out['alerts'].insert(
            0, f'⚠️ date_bias「{json.dumps(bias, ensure_ascii=False)}」不是 "past"/"future"，'
               f'本次已按无方向处理（纯日号默认过去、农历代年取最近）——疑似拼写或格式错误，请核对重传')
        out['confirmation_reasons'].append(
            f'date_bias 值 {json.dumps(bias, ensure_ascii=False)} 非法，需确认日期方向。'
        )

    if out['confirmation_reasons']:
        out['candidate'] = {
            'window': out['window'],
            'single': out['single'],
            'bottom_line': out['bottom_line'],
        }
        out['window'] = None
        out['single'] = None
        out['bottom_line'] = None
        out['status'] = 'needs_confirmation'
        out['questions'].extend(out['confirmation_reasons'])
        return out

    if not out['alerts']:
        out['alerts'].append('✅ 状态正常')
    return out


def main():
    try:
        if len(sys.argv) > 1:
            payload = json.load(open(sys.argv[1], encoding='utf-8'))
        else:
            payload = json.load(sys.stdin)
        result = run(payload)
    except Exception as e:  # noqa: BLE001 — 任何输入错误都要变成模型能读的 JSON
        result = {
            'status': 'error',
            'bottom_line': '输入有误，无法计算——请按 SKILL.md 第 5 步的字段格式重传',
            'alerts': [f'❌ {type(e).__name__}: {e}'],
            'hint': getattr(e, 'hint', None)
                    or ('日期必须能解析成具体某一天（如 2026-03-01）。「3月1日」缺年份、'
                        '「下个月」这类相对说法无法计算，先向用户确认年份与具体日期。'),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
