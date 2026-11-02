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
                                         //       / temporary=会话级临时模型 / web=联网检索
  "visa_days": null,                     // 可选：用户签证页上的停留天数（覆盖 rules.stay_single）
  "visa_class": null,                     // 可选：short_stay / long_stay / student / work / resident / unknown
  "entry_basis": null,                    // 短期访客凭证：visa_free / visa / entry_permit / unknown
  "visa_valid_until": null,               // 可选：签证页有效期最后一天（用户实例字段，不入云库）
  "visa_expiry_semantics": null,          // 可选：leave_by=到期即最晚离境；entry_by=到期只是最晚入境
  "admission_until": null,                // entry_by 当前在途：I-94/入境章/Visitor Record/Landing Permission 的截止日
  "temporary_profile": null,              // 可选：会话级临时签证模型（规则、来源、确认、隐私门禁；永不入云库）
  "reported_days": null,                 // 可选：用户口述的"已待总天数"（用于与行程日期交叉校验）
  "rules_source": null,                  // 可选：规则来源声明（user_rules/temporary/web），缺省随 visa_source 推断
  "web_source": null                     // 可选：联网检索来源 {name,url,date}，visa_source=web 时必填
}

输出（stdout JSON）：`status` 为 `needs_confirmation` 或 `blocked` 时，`bottom_line` 必为 null；
用户可见结论只能来自非空 `bottom_line`。`needs_confirmation` 时先问清 `questions`，
并用 `clarifications` 说明 why（为何影响结果）与 where（去哪里查看）；
不得从 `candidate` 播报数字。`blocked` 表示窗口余量为 0，不能说“还能待 1 天”。
`explanation` 给出下一步与计算依据：澄清阶段不输出候选日期；计算后列出停留规则、
签证有效期、入境许可等边界及最终 `binding_constraint`。
含 `confidence` 字段，取值：
- "high"：用户提供了签证页数字（visa_source="user"）或命中原生扩展表（extended）
- "medium"：用户确认签证类型与 dataset 匹配 / 联网检索到官方页面 /
  用户逐项确认但未进入公共库的 temporary profile
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
VALIDITY_SEMANTICS = {
    'leave_by': 'leave_by', 'departure': 'leave_by', 'latest_exit': 'leave_by',
    '最晚离境': 'leave_by', '最晚离境日': 'leave_by', '申根类': 'leave_by',
    'entry_by': 'entry_by', 'arrival': 'entry_by', 'latest_entry': 'entry_by',
    '最晚入境': 'entry_by', '最晚入境日': 'entry_by', '美签类': 'entry_by',
}
VISA_CLASSES = {
    'short_stay': 'short_stay', 'short-stay': 'short_stay', 'visitor': 'short_stay',
    '短期访客': 'short_stay', '短期停留': 'short_stay', '旅游': 'short_stay', '商务': 'short_stay',
    'long_stay': 'long_stay', 'long-stay': 'long_stay', 'long term': 'long_stay',
    '长期': 'long_stay', '长期签证': 'long_stay',
    'student': 'student', '学生': 'student', '留学': 'student',
    'work': 'work', 'worker': 'work', '工作': 'work', '务工': 'work',
    'resident': 'resident', 'residence': 'resident', '居留': 'resident', '定居': 'resident',
    'unknown': 'unknown', '不确定': 'unknown',
}
ENTRY_BASES = {
    'visa_free': 'visa_free', 'visa-free': 'visa_free', '免签': 'visa_free',
    'visa': 'visa', '签证': 'visa', 'evisa': 'visa', '电子签': 'visa', '落地签': 'visa',
    'entry_permit': 'entry_permit', 'permit': 'entry_permit',
    'eta': 'entry_permit', '入境许可': 'entry_permit',
    'unknown': 'unknown', '不确定': 'unknown',
}
SHORT_STAY_EXCLUDED_MARKERS = (
    'd类', 'd签证', 'dvisa', 'typed', '类型d', 'schengend', '长期', '学生', '留学',
    'student', '工作签', '务工签', 'workvisa', 'employmentvisa', '居留', 'residence',
    'permit',
)
PROFILE_ORIGINS = {'conversation', 'photo'}
PROFILE_SOURCE_KINDS = {'user_statement', 'photo', 'official'}
PROFILE_PRIVACY_INPUT_MODES = {
    'manual_text', 'pasted_ocr', 'local_path', 'session_attachment', 'local_ocr',
}
PROFILE_CLOUD_TRANSFER_MODES = {'none', 'model_input', 'third_party_ocr', 'unknown'}
PROFILE_MERGE_FIELDS = (
    'country', 'visa_type', 'visa_class', 'entry_basis', 'rules',
    'visa_valid_until', 'visa_expiry_semantics', 'admission_until',
)
CLARIFICATION_META = {
    'passport_nationality': (
        'identity', '决定能否使用内置国家规则，而不是只影响显示。',
        '请确认护照国籍；它决定我使用哪套免签或签证规则。',
    ),
    'passport_type': (
        'identity', '普通、外交和公务护照可能适用不同入境规则。',
        '请确认护照类型；确认后我才能判断默认规则是否适用。',
    ),
    'residence_status': (
        'identity', '决定能否使用短期访客规则，或必须改走临时规则。',
        '请确认当前身份；确认后我才能选择短期或长期规则。',
    ),
    'rules_profile': (
        'identity', '决定是否允许套用内置 dataset。',
        '请确认护照与居留身份；确认后我才能决定是否改用你的规则。',
    ),
    'visa_class': (
        'visa', '决定使用短期 90/180、长期许可还是停止计算。',
        '请确认签证 TYPE/CATEGORY；确认后我才能选定签证算法。',
    ),
    'entry_basis': (
        'visa', '决定要核对签证有效期、免签规则还是入境许可。',
        '请确认入境依据；确认后我才能确定该检查哪些日期。',
    ),
    'visa_expiry_semantics': (
        'validity', '同一个 UNTIL 日期可能限制离境，也可能只限制入境。',
        '请确认 UNTIL 的含义；确认后我才能决定它是否封顶离境日。',
    ),
    'visa_valid_until': (
        'validity', '签证到期日可能比停留额度更早，成为真正离境边界。',
        '请补充有效期末日；补齐后我才能把停留边界与签证到期日比较。',
    ),
    'visa_validity': (
        'validity', '有效期日期与语义缺一项就无法得到真正最晚离境日。',
        '请补充有效期日期和语义；补齐后我才能算最终边界。',
    ),
    'admission_until': (
        'admission', '入境后离境边界由 I-94/入境许可决定，不由签证 UNTIL 代替。',
        '请补充入境许可截止日；补齐后我才能用实际获准停留日封顶。',
    ),
    'temporary_profile.user_confirmed': (
        'confirmation', '对话或照片草案未经确认不能当作事实。',
        '请核对临时草案；确认后我才把字段作为计算证据。',
    ),
    'temporary_profile.rules': (
        'rule_evidence', '没有停留上限就无法判断合法停留边界。',
        '请补充单次和累计上限；补齐后我才能进入日期模拟。',
    ),
    'temporary_profile.privacy': (
        'privacy', '未知图片来源可能造成无意上传或图片残留。',
        '请说明图片/OCR 来源与是否离机；确认后我才能读取草案。',
    ),
    'temporary_profile.privacy.cloud_transfer': (
        'privacy', '本地代理不等于本机推理，传输路径会影响隐私风险。',
        '请确认内容是否离开本机；确认后我才能判断能否使用图片草案。',
    ),
    'temporary_profile.privacy.user_consented': (
        'privacy', '签证图片含身份信息，离机处理必须由用户单独同意。',
        '请明确是否同意本次图片处理；不同意就改用手动或粘贴 OCR。',
    ),
    'temporary_profile.privacy.raw_image_retained': (
        'privacy', '保留原图或裁切缓存会扩大身份信息暴露面。',
        '请先清理原图与缓存并确认；确认后我才能继续。',
    ),
    'confirmation': (
        'evidence', '事实或口径冲突会让确定答案建立在错误输入上。',
        '请核对冲突项并确认以哪份为准；确认后我才能继续计算。',
    ),
}

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
    if window_max > 0 and single > window_max and source in ('user_rules', 'user', 'temporary'):
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
    if window_n > 0 and window_n > 400 and source in ('user_rules', 'user', 'temporary'):
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


def _enrich_clarification(item):
    """为追问补齐用户可复述的影响与下一步。"""
    item = {
        **item,
        'category': CLARIFICATION_META.get(
            item.get('field'), ('general', '这个问题会影响当前结论。', '请先确认。'))[0],
        'affects': CLARIFICATION_META.get(
            item.get('field'), ('general', '这个问题会影响当前结论。', '请先确认。'))[1],
        'recap': CLARIFICATION_META.get(
            item.get('field'), ('general', '这个问题会影响当前结论。', '请先确认。'))[2],
        'priority': 'blocking',
    }
    return item


def _ask(questions, clarifications, field, question, why, where, options=None):
    """追加兼容用的纯文本问题，同时输出可解释的结构化追问。"""
    questions.append(question)
    item = _enrich_clarification({
        'field': field,
        'question': question,
        'why': why,
        'where': where,
    })
    if options:
        item['options'] = list(options)
    clarifications.append(item)


def _calculation_basis(out):
    """说明最终日期由哪些约束共同决定，以及是哪一道先封顶。"""
    if out.get('status') in (None, 'unknown', 'needs_confirmation'):
        return None
    rules = out.get('rules_effective') or {}
    single = out.get('single') or {}
    validity = out.get('visa_validity') or {}
    binding = validity.get('binding_constraint')
    constraints = [{
        'name': 'stay_rules',
        'description': (
            f"单次上限 {rules.get('stay_single')} 天；"
            f"{rules.get('stay_window')} 天窗口内最多 "
            f"{rules.get('stay_window_max')} 天"
        ),
        'result': single.get('stay_limit_last_allowed_day'),
    }]
    if validity.get('entry_basis') == 'visa_free':
        constraints.append({
            'name': 'visa_validity',
            'description': '免签路径不要求签证 UNTIL，签证有效期不参与封顶',
            'result': None,
        })
    elif validity.get('valid_until'):
        constraints.append({
            'name': 'visa_validity',
            'description': (
                f"签证/许可有效期至 {validity.get('valid_until')}；"
                f"语义为 {validity.get('expiry_semantics')}"
            ),
            'result': validity.get('valid_until'),
        })
    if validity.get('admission_until'):
        constraints.append({
            'name': 'admission_limit',
            'description': f"入境许可截止到 {validity.get('admission_until')}",
            'result': validity.get('admission_until'),
        })
    reasons = {
        'visa_expiry': '签证/许可有效期早于停留规则边界，因此由有效期封顶。',
        'admission_limit': '入境许可截止日早于其他边界，因此由 I-94/许可封顶。',
        'stay_rules': '停留规则边界早于签证/许可限制，因此由停留规则封顶。',
        'not_required': '本次是免签路径，不需要用签证有效期封顶。',
        'unchecked': '签证有效期尚未校验，当前结论不能当作最终边界。',
        'admission_invalid': '入境许可日期早于入境日，记录存在冲突，不能计算。',
        'unsupported': '当前规则包含无法形式化的约束，因此停止数值计算。',
    }
    return {
        'rule_source': rules.get('source'),
        'visa_class': validity.get('visa_class'),
        'entry_basis': validity.get('entry_basis'),
        'constraints': constraints,
        'binding_constraint': binding,
        'actual_last_allowed_day': single.get('last_allowed_day'),
        'why': reasons.get(binding, '当前没有可用的封顶依据。'),
    }


def _finalize_explanation(out):
    clarifications = out.get('clarifications') or []
    blocking = [item for item in clarifications if item.get('priority') == 'blocking']
    if out.get('status') in (None, 'unknown'):
        phase = 'gathering'
    elif out.get('status') == 'needs_confirmation':
        phase = 'clarification'
    elif out.get('status') == 'unsupported':
        phase = 'unsupported'
    else:
        phase = 'calculation'
    next_step = None
    if out.get('status') == 'unsupported':
        next_step = '当前规则无法完整形式化；请改用手动核对或咨询官方口径。'
    elif blocking:
        next_step = blocking[0].get('recap')
    elif out.get('status') not in ('unknown', 'needs_confirmation'):
        next_step = '可按 calculation_basis 逐项复述结论；无需再补阻塞字段。'
    out['explanation'] = {
        'phase': phase,
        'clarification_count': len(clarifications),
        'blocking_fields': [item.get('field') for item in blocking],
        'next_step': next_step,
        'calculation_basis': _calculation_basis(out),
    }


def _prepare_temporary_profile(payload):
    """校验并合并会话级临时签证模型；不持久化任何字段。"""
    raw = payload.get('temporary_profile')
    if raw is None:
        return payload, None, []
    if not isinstance(raw, dict):
        raise InputError(
            'temporary_profile 必须是 JSON 对象',
            '把本次对话确认的签证规则放在 temporary_profile 对象里；不要传字符串。',
        )

    origin = str(raw.get('origin') or 'conversation').strip().lower()
    if origin not in PROFILE_ORIGINS:
        raise InputError(
            f'未知 temporary_profile.origin: {json.dumps(origin, ensure_ascii=False)}',
            'origin 只能是 conversation（对话整理）或 photo（照片/OCR 草稿）。',
        )
    confirmed = raw.get('user_confirmed', False)
    if not isinstance(confirmed, bool):
        raise InputError(
            'temporary_profile.user_confirmed 必须是 JSON true 或 false',
            '用户还没有逐项核对时传 false；确认后才传 true。',
        )
    conditions = raw.get('conditions') or []
    if not isinstance(conditions, list) or not all(isinstance(x, str) for x in conditions):
        raise InputError(
            'temporary_profile.conditions 必须是字符串数组',
            '把无法落入单次/窗口/有效期/I-94 模型的附加条件逐条放进数组；没有就传 []。',
        )
    if 'rules' in raw and not isinstance(raw['rules'], dict):
        raise InputError(
            'temporary_profile.rules 必须是 JSON 对象',
            'rules 需要包含 stay_single/stay_window/stay_window_max/calendar_year_window。',
        )

    source = raw.get('source')
    if source is None:
        source = {'kind': 'photo' if origin == 'photo' else 'user_statement'}
    if not isinstance(source, dict):
        raise InputError(
            'temporary_profile.source 必须是 JSON 对象',
            'source 至少写 kind（user_statement/photo/official），可附 name/url/date。',
        )
    source = dict(source)
    source_kind = str(source.get('kind') or '').strip().lower()
    if source_kind not in PROFILE_SOURCE_KINDS:
        raise InputError(
            f'未知 temporary_profile.source.kind: {json.dumps(source_kind, ensure_ascii=False)}',
            'source.kind 只能是 user_statement/photo/official。',
        )
    for key in ('name', 'url', 'date'):
        if key in source and source[key] is not None and not isinstance(source[key], str):
            raise InputError(
                f'temporary_profile.source.{key} 必须是字符串',
                'name/url/date 只传文本；没有就省略。',
            )

    privacy = raw.get('privacy')
    privacy_issues = []
    if privacy is None and origin == 'conversation':
        privacy = {
            'input_mode': 'manual_text',
            'cloud_transfer': 'none',
            'user_consented': True,
            'raw_image_retained': False,
        }
    elif not isinstance(privacy, dict):
        privacy = {
            'input_mode': 'unknown',
            'cloud_transfer': 'unknown',
            'user_consented': False,
            'raw_image_retained': None,
        }
        privacy_issues.append({
            'field': 'temporary_profile.privacy',
            'question': (
                '这份图片/识别草案来自哪里：本机路径、当前会话附件、粘贴的 OCR 文字，'
                '还是本机 OCR？图片是否离开设备，你是否同意？原图是否已删除？'
            ),
            'why': '不确认图片处理路径，就无法避免图片被无意上传或在会话外继续保留。',
            'where': (
                '看会话附件方式与当前模型服务；不调用上层 App 摄像头。'
                '默认改用文字/粘贴 OCR，只有明确同意后才使用会话图片。'
            ),
            'options': ['改用文字/OCR', '会话图片并同意', '取消'],
        })
    else:
        privacy = dict(privacy)
        input_mode = str(privacy.get('input_mode') or '').strip().lower()
        if input_mode in ('camera', 'device_camera'):
            raise InputError(
                '本 Skill 不直接调用设备摄像头',
                '请由用户主动在当前会话上传图片或提供本机路径；优先使用手动文字或粘贴 OCR。',
            )
        if input_mode not in PROFILE_PRIVACY_INPUT_MODES:
            raise InputError(
                f'未知 temporary_profile.privacy.input_mode: {json.dumps(input_mode, ensure_ascii=False)}',
                'input_mode 只能是 manual_text/pasted_ocr/local_path/session_attachment/local_ocr。',
            )
        cloud_transfer = str(privacy.get('cloud_transfer') or 'unknown').strip().lower()
        if cloud_transfer not in PROFILE_CLOUD_TRANSFER_MODES:
            raise InputError(
                f'未知 temporary_profile.privacy.cloud_transfer: '
                f'{json.dumps(cloud_transfer, ensure_ascii=False)}',
                'cloud_transfer 只能是 none/model_input/third_party_ocr/unknown。',
            )
        consented = privacy.get('user_consented', False)
        if not isinstance(consented, bool):
            raise InputError(
                'temporary_profile.privacy.user_consented 必须是 JSON true 或 false',
                '用户还没有明确同意时传 false。',
            )
        retained = privacy.get('raw_image_retained', False)
        if not isinstance(retained, bool):
            raise InputError(
                'temporary_profile.privacy.raw_image_retained 必须是 JSON true 或 false',
                '原图是否仍保留必须明确；默认应在识别后删除。',
            )
        privacy = {
            'input_mode': input_mode,
            'cloud_transfer': cloud_transfer,
            'user_consented': consented,
            'raw_image_retained': retained,
        }
        if cloud_transfer == 'unknown':
            privacy_issues.append({
                'field': 'temporary_profile.privacy.cloud_transfer',
                'question': '这张图片或 OCR 内容会离开本机吗？当前处理路径是纯本机，还是会进入模型/第三方服务？',
                'why': '本地代理不代表本机推理；不能把未知传输路径说成“图片不上云”。',
                'where': '查看当前模型服务配置和图片处理说明；无法确认时改用文字/粘贴 OCR。',
            })
        if cloud_transfer in ('model_input', 'third_party_ocr') and not consented:
            privacy_issues.append({
                'field': 'temporary_profile.privacy.user_consented',
                'question': '这张签证图片可能发送给模型或第三方 OCR 服务处理，你是否明确同意？',
                'why': '签证页包含身份信息；是否上传必须由用户单独决定。',
                'where': '只在当前会话确认；不同意就改用手动字段或粘贴 OCR 文字。',
                'options': ['同意本次图片处理', '改用文字/OCR'],
            })
        if retained:
            privacy_issues.append({
                'field': 'temporary_profile.privacy.raw_image_retained',
                'question': '原图仍被保留。请先删除原图/裁切缓存，再确认继续。',
                'why': '本流程默认不保存签证图片和 OCR 原文。',
                'where': '检查相册临时文件、会话附件缓存和宿主的图片处理缓存。',
            })

    merged = dict(payload)
    for field in PROFILE_MERGE_FIELDS:
        if field not in raw or raw[field] in (None, '', {}):
            continue
        current = merged.get(field)
        if current not in (None, '', {}) and current != raw[field]:
            raise InputError(
                f'temporary_profile.{field} 与顶层 {field} 冲突',
                '同一字段只保留一个版本；先让用户确认以哪份为准，再重传。',
            )
        merged[field] = raw[field]

    requested_source = merged.get('visa_source')
    if requested_source not in (None, 'dataset', 'temporary'):
        raise InputError(
            f'temporary_profile 不能与 visa_source={requested_source} 同时使用',
            '临时模型使用 visa_source=temporary；先移除冲突的来源声明。',
        )
    merged['visa_source'] = 'temporary'
    requested_rules_source = merged.get('rules_source')
    if requested_rules_source not in (None, 'temporary'):
        raise InputError(
            f'temporary_profile 不能与 rules_source={requested_rules_source} 同时使用',
            '临时模型的 rules_source 由脚本自动设为 temporary。',
        )
    merged['rules_source'] = 'temporary'

    profile = {
        'origin': origin,
        'user_confirmed': confirmed,
        'source': source,
        'privacy': privacy,
        'privacy_ready': (
            not privacy_issues
            and privacy.get('cloud_transfer') != 'unknown'
            and privacy.get('raw_image_retained') is False
            and (
                privacy.get('cloud_transfer') == 'none'
                or privacy.get('user_consented') is True
            )
        ),
        'conditions': conditions,
        'notes': str(raw.get('notes') or '').strip() or None,
    }
    issues = list(privacy_issues)
    if not confirmed:
        issues.append({
            'field': 'temporary_profile.user_confirmed',
            'question': (
                '请先核对这份临时签证规则草案：类别、入境依据、允许停留天数、'
                '有效期和语义是否都与签证页/官方说明一致？确认前不计算。'
            ),
            'why': '对话或照片提取可能看错 TYPE、UNTIL 或停留天数；未经确认的草案不能作为计算依据。',
            'where': (
                '逐项对照签证页 TYPE/CATEGORY、VALID FROM/UNTIL、DURATION OF STAY；'
                '电子签打开原始 PDF，I-94 查看 Admit Until Date。'
            ),
            'options': ['确认草案', '修改草案'],
        })
    if not merged.get('rules'):
        issues.append({
            'field': 'temporary_profile.rules',
            'question': '这份临时签证规则还缺可计算的停留上限，请补充单次天数，以及是否有窗口/年度累计上限。',
            'why': '没有单次或累计上限时只能做日期统计，不能判断合法停留边界。',
            'where': '看签证页 DURATION OF STAY；若没有累计规则，明确说“没有”。',
        })
    return merged, profile, issues


def run(payload):
    global _INFERRED_DATES
    _INFERRED_DATES = {}

    payload, temporary_profile, profile_issues = _prepare_temporary_profile(payload)
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

    raw_confirmed = payload.get('confirmed', payload.get('user_confirmed', False))
    if not isinstance(raw_confirmed, bool):
        raise InputError(
            'confirmed 必须是 JSON true 或 false',
            f'收到的是 {json.dumps(raw_confirmed, ensure_ascii=False)}；不要在用户未确认时传 true。',
        )

    pool = pool_of(country)
    trips = payload.get('trips') or []

    # 签证来源与置信度
    # visa_source: dataset=快照值 / user=签证页单值 / user_rules=用户结构化整套
    #              / extended=扩展表命中 / aggregated=聚合表命中(confidence=low)
    #              / temporary=会话级临时规则草案(不持久化) / web=联网检索
    visa_source = payload.get('visa_source') or 'dataset'
    allowed_sources = {
        'dataset', 'user', 'user_rules', 'extended', 'aggregated', 'temporary', 'web',
    }
    if visa_source not in allowed_sources:
        raise InputError(
            f'未知 visa_source: {json.dumps(visa_source, ensure_ascii=False)}',
            'visa_source 只能是 dataset/user/user_rules/extended/aggregated/temporary/web。',
        )
    if visa_source == 'temporary' and temporary_profile is None:
        raise InputError(
            'visa_source=temporary 必须同时提供 temporary_profile',
            '临时来源不能被当成普通 user_rules 绕过身份确认；请提供完整草案对象。',
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
    raw_visa_class = payload.get('visa_class')
    if raw_visa_class is not None and str(raw_visa_class).strip() == '':
        raise InputError(
            'visa_class 不能是空字符串',
            '未提供签证身份类别时请省略该字段或传 null。',
        )
    visa_class = (
        VISA_CLASSES.get(str(raw_visa_class).strip().lower())
        if raw_visa_class is not None else None
    )
    if raw_visa_class is not None and visa_class is None:
        raise InputError(
            f'未知 visa_class: {json.dumps(raw_visa_class, ensure_ascii=False)}',
            'visa_class 只能是 short_stay/long_stay/student/work/resident/unknown。',
        )
    raw_entry_basis = payload.get('entry_basis')
    if raw_entry_basis is not None and str(raw_entry_basis).strip() == '':
        raise InputError(
            'entry_basis 不能是空字符串',
            '未提供入境依据时请省略该字段或传 null。',
        )
    entry_basis = (
        ENTRY_BASES.get(str(raw_entry_basis).strip().lower())
        if raw_entry_basis is not None else None
    )
    if raw_entry_basis is not None and entry_basis is None:
        raise InputError(
            f'未知 entry_basis: {json.dumps(raw_entry_basis, ensure_ascii=False)}',
            'entry_basis 只能是 visa_free/visa/entry_permit/unknown。',
        )
    visa_type_text = re.sub(r'\s+', '', str(visa_type or '').lower())
    visa_type_looks_non_short = any(
        marker in visa_type_text
        for marker in SHORT_STAY_EXCLUDED_MARKERS
    )
    visa_class_conflict = visa_class == 'short_stay' and visa_type_looks_non_short
    # 签证有效期是用户实例字段：只进入本次计算，不写入国家/签证类型云库。
    raw_valid_until = payload.get('visa_valid_until')
    if raw_valid_until is not None and str(raw_valid_until).strip() == '':
        raise InputError(
            'visa_valid_until 不能是空字符串',
            '未提供签证有效期时请省略该字段或传 null；已提供时传具体日期（如 2026-06-12）。',
        )
    visa_valid_until = (
        d(raw_valid_until, near=today, bias=bias)
        if raw_valid_until is not None else None
    )
    raw_semantics = payload.get('visa_expiry_semantics')
    if raw_semantics is not None and str(raw_semantics).strip() == '':
        raise InputError(
            'visa_expiry_semantics 不能是空字符串',
            '未提供有效期语义时请省略该字段或传 null。',
        )
    visa_expiry_semantics = (
        VALIDITY_SEMANTICS.get(str(raw_semantics).strip().lower())
        if raw_semantics is not None else None
    )
    if raw_semantics is not None and visa_expiry_semantics is None:
        raise InputError(
            f'未知 visa_expiry_semantics: {json.dumps(raw_semantics, ensure_ascii=False)}',
            '只能传 leave_by（申根类：到期即最晚离境）或 entry_by（美签类：到期只是最晚入境）。',
        )
    raw_admission_until = payload.get('admission_until')
    if raw_admission_until is not None and str(raw_admission_until).strip() == '':
        raise InputError(
            'admission_until 不能是空字符串',
            '没有入境许可截止日时请省略该字段或传 null。',
        )
    admission_until = (
        d(raw_admission_until, near=today, bias=bias)
        if raw_admission_until is not None else None
    )
    # rules 来源：结构化录入/扩展表/联网检索时，调用方（模型）在此声明，输出如实透传
    inferred_rules_source = (
        'user_rules' if visa_source == 'user_rules'
        else ('aggregated' if visa_source == 'aggregated'
              else ('extended' if visa_source == 'extended'
                    else ('temporary' if visa_source == 'temporary'
                          else ('web' if visa_source == 'web' else 'dataset')))))
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
    clarifications = [
        _enrich_clarification(item)
        for item in profile_issues
        if isinstance(item, dict)
    ]
    for item in clarifications:
        questions.append(item['question'])

    if rules_source not in ('user_rules', 'temporary'):
        if not passport_nationality:
            _ask(
                questions, clarifications,
                'passport_nationality',
                '你持哪国护照？',
                '护照国籍决定适用哪套免签、签证和停留规则。',
                '看护照资料页的 Nationality / Code；不要用当前所在地或 IP 推断。',
            )
        if not passport_type:
            _ask(
                questions, clarifications,
                'passport_type',
                '你的护照类型是普通护照，还是外交/公务等其他护照？',
                '普通、外交和公务护照适用的入境规则可能不同。',
                '看护照资料页的 Type / 类型。',
                ['普通', '外交', '公务', '其他'],
            )
        if not residence_status:
            _ask(
                questions, clarifications,
                'residence_status',
                '你目前在当地是短期访客、学生、工作还是长期居留身份？',
                '身份决定能否使用短期访客的 90/180 或普通旅游签规则。',
                '看签证 TYPE/CATEGORY、居留卡或许可证；不确定时不要选。',
                ['短期访客', '学生', '工作', '长期居留', '其他'],
            )
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
            _ask(
                questions, clarifications,
                'rules_profile',
                '当前内置规则只适用于中国普通护照的短期访客。请提供签证页上的允许停留天数，'
                '或确认完整规则后按 user_rules 重算。',
                '把其他护照或身份套进默认短期规则，会直接算错。',
                '看护照资料页、签证 TYPE 与居留/学生/工作许可。',
            )

    requires_visa_instance = (
        entry_basis in ('visa', 'entry_permit')
    )

    if visa_class is None:
        _ask(
            questions, clarifications,
            'visa_class',
            '先看签证页 TYPE/CATEGORY 或电子签标题：这次是短期访客（旅游/商务/探亲，'
            '申根通常为 C），还是长期/学生/工作/居留（申根 D 等）？'
            'C 和 D 的规则不同，确认前不计算。',
            '签证类别决定能不能使用短期 90/180，不能按国家默认值猜。',
            '看签证页 TYPE/CATEGORY；电子签看标题与签发说明。',
            ['短期访客', '长期', '学生', '工作', '居留', '不确定'],
        )
    elif visa_class == 'unknown':
        _ask(
            questions, clarifications,
            'visa_class',
            '签证类别还不确定：请查看签证页 TYPE/CATEGORY 或电子签标题，'
            '确认短期访客、长期、学生、工作还是居留；类别决定能否使用 90/180，'
            '不确定时先停算。',
            '不同签证产品的停留算法不同，猜错会给出错误的最晚离境日。',
            '签证页 TYPE/CATEGORY、电子签 PDF 标题、居留卡正面。',
        )
    else:
        if entry_basis is None:
            _ask(
                questions, clarifications,
                'entry_basis',
                '这次凭哪种资格入境：A 免签，B 签证/电子签/落地签，'
                'C ETA/I-94/其他入境许可？看签证页、入境章或官方许可。'
                '它决定是否需要核对签证 UNTIL 与入境许可截止日，不能用国家默认值代替。',
                '入境依据决定该核对签证有效期、I-94，还是只需核对免签规则。',
                '签证贴纸、电子签、入境章、I-94、ETA 或居留许可。',
                ['免签', '签证/电子签/落地签', '入境许可', '不确定'],
            )
        elif entry_basis == 'unknown':
            _ask(
                questions, clarifications,
                'entry_basis',
                '入境依据还不确定：请查看签证页、入境章、I-94 或电子许可，'
                '确认是免签、签证，还是入境许可；确认前无法判断该核对哪些日期。',
                '缺少入境依据时会漏掉签证到期日或 I-94 截止日。',
                '签证页、入境章、I-94、ETA 或其他官方许可。',
            )
    if visa_class_conflict:
        _ask(
            questions, clarifications,
            'visa_class',
            '签证名称里出现 D/长期/学生/工作或居留标记，但类别选了短期访客；'
            '请看签证页 TYPE/CATEGORY 复核，避免把长期签证套进申根 C 类 90/180。',
            '申根 D 与 C 使用不同规则，类型冲突时继续算会产生错误结论。',
            '签证页 TYPE/CATEGORY；申根短期签通常标 C，长期国别签通常标 D。',
        )
    if requires_visa_instance:
        if visa_valid_until is not None and visa_expiry_semantics is None:
            _ask(
                questions, clarifications,
                'visa_expiry_semantics',
                '这个 UNTIL 日期代表哪种限制：A 到期即最晚离境（申根 C 等）；'
                'B 只是最晚入境（美国 B1/B2 等，离境看 I-94/入境章）？'
                '看签证类型和官方说明确认，选错会多算停留天数。',
                '同一个 UNTIL 日期可能是离境边界，也可能只限制最晚入境。',
                '签证页 UNTIL 与对应签证类型的官方说明。',
                ['到期即最晚离境', '仅限制最晚入境', '不确定'],
            )
        elif visa_expiry_semantics is not None and visa_valid_until is None:
            _ask(
                questions, clarifications,
                'visa_valid_until',
                '请看签证页 FROM/UNTIL：有效期末日是哪天？'
                '它可能直接限制离境日；注意 VALID FOR 通常是地域范围，不是日期。',
                '签证有效期末日可能比停留天数更早，是实际最晚离境边界。',
                '签证页 UNTIL 字段；电子签 PDF 的 Valid Until。',
            )
        elif visa_valid_until is None and visa_expiry_semantics is None:
            _ask(
                questions, clarifications,
                'visa_validity',
                '请看签证页 FROM/UNTIL，补充有效期最后一天，并确认它是'
                '“到期即最晚离境”还是“仅限制最晚入境”；这两者会算出不同的最晚离境日。',
                '日期与语义缺一项，算法就无法确定真正的最晚离境日。',
                '签证页 FROM/UNTIL；美国签证再看 I-94 Admit Until Date。',
            )

    # 用户签证页数字覆盖 dataset 值
    if visa_days and visa_days > 0:
        single = int(visa_days)
        if visa_source not in ('user_rules', 'extended', 'aggregated', 'temporary', 'web'):
            visa_source = 'user'

    # 置信度判定
    if visa_source in ('user', 'user_rules', 'extended'):
        confidence = 'high'
    elif visa_source == 'temporary':
        confidence = 'medium'
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
    temporary_profile_usable = bool(
        temporary_profile
        and temporary_profile.get('user_confirmed')
        and temporary_profile.get('privacy_ready')
        and not temporary_profile.get('conditions')
        and (single > 0 or window_max > 0)
    )

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
        'temporary_profile': (
            {
                **temporary_profile,
                'used': temporary_profile_usable,
                'persistence': 'session_only',
            }
            if temporary_profile else None
        ),
        'visa_validity': {
            'visa_class': visa_class or 'unknown',
            'entry_basis': entry_basis or 'unknown',
            'valid_until': visa_valid_until.isoformat() if visa_valid_until else None,
            'expiry_semantics': visa_expiry_semantics or 'unknown',
            'admission_until': admission_until.isoformat() if admission_until else None,
            'checked': (
                entry_basis == 'visa_free'
                or bool(visa_valid_until and visa_expiry_semantics)
            ),
            'binding_constraint': (
                'not_required' if entry_basis == 'visa_free'
                else ('unchecked' if requires_visa_instance else None)
            ),
        },
        'window': None,
        'single': None,
        'bottom_line': None,
        'status': 'unknown',
        'alerts': [],
        'questions': questions,
        'clarifications': clarifications,
        'confirmation_reasons': [],
        'explanation': None,
    }

    if (
        current is not None
        and visa_class == 'short_stay'
        and visa_expiry_semantics == 'entry_by'
        and admission_until is None
    ):
        _ask(
            questions, clarifications,
            'admission_until',
            '美签类签证到期日只管入境。请查看 I-94 Admit Until Date、护照入境章、'
            'Visitor Record 或 Landing Permission：本次获准停留到最后哪一天？',
            '入境后离境边界由入境许可决定，不能用签证 UNTIL 代替。',
            '美国看 I-94 Admit Until Date；英国看入境章/Visitor Record；'
            '加拿大看 Landing Permission。',
        )

    if visa_class in ('long_stay', 'student', 'work', 'resident'):
        if temporary_profile is not None and temporary_profile.get('conditions'):
            out['status'] = 'unsupported'
            out['visa_validity']['binding_constraint'] = 'unsupported'
            out['alerts'].append(
                '临时签证模型包含当前算法无法表达的附加条件；不能只算天数后忽略这些条件。'
            )
            out['alerts'].extend(
                f'未建模条件：{condition}'
                for condition in temporary_profile['conditions']
            )
            _finalize_explanation(out)
            return out
        if temporary_profile is not None:
            # 明确确认且可由单次/窗口/有效期/I-94 表达的临时模型允许继续。
            pass
        else:
            out['status'] = 'unsupported'
            out['visa_validity']['binding_constraint'] = 'unsupported'
            out['alerts'].append(
                '当前内置计算器只处理短期访客停留规则；长期、学生、工作或居留类签证'
                '不能套用申根 C 类/普通旅游签模板，请按签证或许可上的停留条件核对。'
            )
            _finalize_explanation(out)
            return out

    # 规则四元组合理性 + 行程日期防呆（结构化录入的机器校验层）
    rule_alerts = validate_rules(single, window_n, window_max, cal_year, rules_source)
    trip_alerts = validate_trips(trips, today, as_of, bias)
    out['alerts'].extend(rule_alerts)
    out['alerts'].extend(trip_alerts)
    if requires_visa_instance and visa_valid_until is None and visa_expiry_semantics is None:
        out['alerts'].append(
            '⚠️ 未提供签证有效期末日与语义：本次结果只按停留规则计算，'
            '尚未校验签证到期日。须确认是“到期即最晚离境”还是“到期只是最晚入境”。'
        )
    out['confirmation_reasons'].extend(
        alert for alert in rule_alerts + trip_alerts
        if alert.startswith(('⚠️', '❌'))
    )
    out['confirmation_reasons'].extend(state_reasons)

    # 用户口述总天数交叉校验（实战教训：用户说 60 天、日期算出 82 天，必须指出差异）
    soft_reasons = []  # 用户一句话即可确认的项；confirmed=true 时放行，硬冲突（as_of/bias/身份）不放行
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
            reason = (f'口径差异：用户口述 {reported} 天，行程日期实算 {actual} 天。'
                      f'请确认以哪个为准。')
            out['confirmation_reasons'].append(reason)
            soft_reasons.append(reason)

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
    validity_not_required = entry_basis == 'visa_free'
    if current is not None and single > 0:
        entry = continuous_start
        base_occupied = all_days - continuous_days
        last_allowed, stay_days, _ = simulate_stay(
            entry, single, window_n, window_max, cal_year, base_occupied)
        stay_limit_last_allowed = last_allowed
        validity_blocked = False
        admission_blocked = False
        validity_checked = validity_not_required or bool(
            visa_valid_until and visa_expiry_semantics)
        binding_constraint = (
            'not_required' if validity_not_required
            else ('stay_rules' if validity_checked else 'unchecked')
        )
        if visa_valid_until is not None and visa_expiry_semantics is not None:
            if entry > visa_valid_until:
                validity_blocked = True
                last_allowed = None
                stay_days = 0
            elif (
                visa_expiry_semantics == 'leave_by'
                and last_allowed is not None
                and visa_valid_until < last_allowed
            ):
                last_allowed = visa_valid_until
                binding_constraint = 'visa_expiry'
            elif visa_expiry_semantics == 'entry_by' and admission_until is not None:
                if admission_until < entry:
                    admission_blocked = True
                    last_allowed = None
                    stay_days = 0
                elif last_allowed is None or admission_until < last_allowed:
                    last_allowed = admission_until
                    binding_constraint = 'admission_limit'
        if last_allowed is not None:
            stay_days = max((last_allowed - entry).days + 1, 0)
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
            'stay_limit_last_allowed_day': (
                stay_limit_last_allowed.isoformat() if stay_limit_last_allowed else None),
            'remaining_days': remaining,      # 不含今天的剩余可待天数
            'overdue': overdue,
            'blocked': blocked,
        }
        out['visa_validity']['binding_constraint'] = (
            'visa_expiry' if validity_blocked
            else ('admission_invalid' if admission_blocked else binding_constraint))
        out['status'] = status
        if blocked:
            if admission_blocked:
                out['alerts'].append(
                    f'⚠️ 入境许可截止日 {admission_until.isoformat()} 早于本次入境日 '
                    f'{entry.isoformat()}，日期或记录有误；不能据此计算合法停留期。'
                )
            elif validity_blocked:
                out['alerts'].append(
                    f'⚠️ 当前行程入境日 {entry.isoformat()} 晚于签证有效期 '
                    f'{visa_valid_until.isoformat()}；该签证不能作为本次停留依据。'
                )
            else:
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
        stay_limit_last_allowed = last_allowed
        validity_blocked = bool(
            visa_valid_until is not None
            and visa_expiry_semantics is not None
            and as_of > visa_valid_until
        )
        admission_blocked = bool(
            visa_expiry_semantics == 'entry_by'
            and admission_until is not None
            and admission_until < as_of
        )
        if validity_blocked or admission_blocked:
            last_allowed = None
            stay_days = 0
        elif (
            visa_expiry_semantics == 'leave_by'
            and visa_valid_until is not None
            and last_allowed is not None
            and visa_valid_until < last_allowed
        ):
            last_allowed = visa_valid_until
            out['visa_validity']['binding_constraint'] = 'visa_expiry'
        elif (
            visa_expiry_semantics == 'entry_by'
            and admission_until is not None
            and last_allowed is not None
            and admission_until < last_allowed
        ):
            last_allowed = admission_until
            out['visa_validity']['binding_constraint'] = 'admission_limit'
        elif validity_not_required:
            out['visa_validity']['binding_constraint'] = 'not_required'
        elif visa_expiry_semantics is not None and visa_valid_until is not None:
            out['visa_validity']['binding_constraint'] = 'stay_rules'
        if last_allowed is not None:
            stay_days = max((last_allowed - as_of).days + 1, 0)
        blocked = stay_days == 0
        out['single'] = {
            'entry': as_of.isoformat(),
            'effective_days': stay_days,
            'last_allowed_day': last_allowed.isoformat() if last_allowed else None,
            'stay_limit_last_allowed_day': (
                stay_limit_last_allowed.isoformat() if stay_limit_last_allowed else None),
            'remaining_days': stay_days,
            'overdue': False,
            'blocked': blocked,
            'note': '未来入境逐日预估',
        }
        if blocked:
            out['status'] = 'blocked'
            next_entry = None if (validity_blocked or admission_blocked) else earliest_entry(
                as_of, single, window_n, window_max, cal_year, all_days)
            out['single']['earliest_allowed_date'] = (
                next_entry.isoformat() if next_entry else None)
            if admission_blocked:
                out['visa_validity']['binding_constraint'] = 'admission_invalid'
                out['alerts'].append(
                    f'⚠️ 入境许可截止日 {admission_until.isoformat()} 早于计划入境日 '
                    f'{as_of.isoformat()}，日期或记录有误。'
                )
            elif validity_blocked:
                out['visa_validity']['binding_constraint'] = 'visa_expiry'
                out['alerts'].append(
                    f'⚠️ 计划入境日 {as_of.isoformat()} 晚于签证有效期 '
                    f'{visa_valid_until.isoformat()}，不能凭该签证入境；需要新签证或延期。'
                )
            else:
                out['alerts'].append(
                    f'⚠️ 计划入境日 {as_of.isoformat()} 窗口额度不足，不能入境；'
                    f'最早可再入境日 {next_entry.isoformat() if next_entry else "待核算"}')
        else:
            out['status'] = 'planning'
            if visa_expiry_semantics == 'entry_by':
                out['alerts'].append(
                    'ℹ️ 未来尚未入境：这里只推演签证允许的最长停留上限；'
                    '实际可停到哪天，以入境后签发的 I-94/入境许可为准。'
                )

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
        _finalize_explanation(out)
        return out

    # ── 一句话结论（AI 播报必须直接引用此字段；杜绝误把 effective_days 当剩余天数） ──
    s = out['single']
    if out['status'] == 'blocked':
        out['bottom_line'] = None
    elif s is not None and out['status'] != 'planning':
        if s['overdue']:
            over = (today - d(s['last_allowed_day'])).days
            binding = out['visa_validity'].get('binding_constraint')
            visa_limit = (
                '，受签证有效期限制' if binding == 'visa_expiry'
                else ('，受入境许可截止日限制' if binding == 'admission_limit' else '')
            )
            out['bottom_line'] = (f'⚠️ 已超期 {over} 天（最晚离境日 {s["last_allowed_day"]}'
                                  f'{visa_limit}）'
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
            if out['visa_validity'].get('binding_constraint') == 'visa_expiry':
                lead += '签证有效期先到；'
            elif out['visa_validity'].get('binding_constraint') == 'admission_limit':
                lead += '入境许可截止日先到；'
            out['bottom_line'] = (f'{lead}最晚离境日 {s["last_allowed_day"]}，'
                                  f'还能再待 {s["remaining_days"]} 天（不含今天）')
    elif s is not None:  # planning（未来入境预估）
        if out['visa_validity'].get('binding_constraint') == 'visa_expiry':
            capped = '（按签证有效期封顶）'
        elif out['visa_validity'].get('binding_constraint') == 'admission_limit':
            capped = '（按入境许可截止日封顶）'
        elif out['visa_validity'].get('binding_constraint') == 'not_required':
            capped = '（按入境日窗口余量封顶）' if window_max > 0 else ''
        else:
            capped = '（按入境日窗口余量封顶）' if window_max > 0 else ''
        # as_of 缺省等于 today 时不能称「计划今天入境」——已离境用户问「下次能待多久」会误读
        if as_of > today:
            lead = f'计划 {s["entry"]} 入境'
        elif as_of == today:
            lead = f'若今天（{s["entry"]}）入境'
        else:
            lead = f'若 {s["entry"]} 入境'
        admission_note = (
            '；这是入境前上限推演，入境后实际停留以 I-94/入境许可为准'
            if visa_expiry_semantics == 'entry_by' else ''
        )
        out['bottom_line'] = (f'{lead}，本次最多可待 {s["effective_days"]} 天，'
                              f'最晚 {s["last_allowed_day"]} 离境{capped}{admission_note}')
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
        reason = f'日期「{raw}」被推定为 {got.isoformat()}，请向用户确认。'
        out['confirmation_reasons'].append(reason)
        soft_reasons.append(reason)

    # ── date_bias 非法值告警（防 typo 静默降级：'futrue'/数组/大小写都会被 == 比较无声吞掉） ──
    if bias is not None and bias not in ('past', 'future'):
        out['alerts'].insert(
            0, f'⚠️ date_bias「{json.dumps(bias, ensure_ascii=False)}」不是 "past"/"future"，'
               f'本次已按无方向处理（纯日号默认过去、农历代年取最近）——疑似拼写或格式错误，请核对重传')
        out['confirmation_reasons'].append(
            f'date_bias 值 {json.dumps(bias, ensure_ascii=False)} 非法，需确认日期方向。'
        )

    # 用户已明确确认软性疑点（推定日期/口径差异）→ 放行；硬冲突（as_of、bias、身份）不放行
    if raw_confirmed and soft_reasons:
        out['confirmation_reasons'] = [
            r for r in out['confirmation_reasons'] if r not in soft_reasons]
        out['alerts'].append('✅ 已按用户确认的口径继续计算（confirmed: true）')

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
        for reason in out['confirmation_reasons']:
            if not any(item.get('question') == reason for item in out['clarifications']):
                out['clarifications'].append(_enrich_clarification({
                    'field': 'confirmation',
                    'question': reason,
                    'why': '事实或录入口径存在冲突时，继续计算会把不确定性包装成确定答案。',
                    'where': '回到行程日期、签证页或规则来源逐项核对，再决定以哪份为准。',
                }))
        _finalize_explanation(out)
        return out

    if not out['alerts']:
        out['alerts'].append('✅ 状态正常')
    _finalize_explanation(out)
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
