#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""农历 → 公历。纯查表，零依赖，离线可跑。

为什么不用万年历 API：C 端 5 秒红线里不该塞一次网络往返，额度用尽或服务停摆时
「必须能转换」的场景会直接失败。农历是确定性历法，4KB 表覆盖 1900-2100 共 201 年。

数据（逐年月序编码 + 春节公历日）取自 zhdate（MIT，Wang Yi），与公开历书一致。
已核对的关键点：2026 春节 2026-02-17、2026 中秋 2026-09-25、
2033 闰十一月初一 2033-12-22（「2033 年问题」边界）。

超出 1900-2100 或该年无此闰月时返回 None，由调用方追问，不猜。
"""
import re
from datetime import date, timedelta

# 每年一个整数：bit0-3 闰月月份（0 = 无闰月）；bit4-15 十二月大小（1=30 天）；
# bit16 闰月大小（1=30 天）。
_YEAR_CODE = (
    19416, 19168, 42352, 21717, 53856, 55632, 91476, 22176, 39632, 21970,
    19168, 42422, 42192, 53840, 119381, 46400, 54944, 44450, 38320, 84343,
    18800, 42160, 46261, 27216, 27968, 109396, 11104, 38256, 21234, 18800,
    25958, 54432, 59984, 92821, 23248, 11104, 100067, 37600, 116951, 51536,
    54432, 120998, 46416, 22176, 107956, 9680, 37584, 53938, 43344, 46423,
    27808, 46416, 86869, 19872, 42416, 83315, 21168, 43432, 59728, 27296,
    44710, 43856, 19296, 43748, 42352, 21088, 62051, 55632, 23383, 22176,
    38608, 19925, 19152, 42192, 54484, 53840, 54616, 46400, 46752, 103846,
    38320, 18864, 43380, 42160, 45690, 27216, 27968, 44870, 43872, 38256,
    19189, 18800, 25776, 29859, 59984, 27480, 23232, 43872, 38613, 37600,
    51552, 55636, 54432, 55888, 30034, 22176, 43959, 9680, 37584, 51893,
    43344, 46240, 47780, 44368, 21977, 19360, 42416, 86390, 21168, 43312,
    31060, 27296, 44368, 23378, 19296, 42726, 42208, 53856, 60005, 54576,
    23200, 30371, 38608, 19195, 19152, 42192, 118966, 53840, 54560, 56645,
    46496, 22224, 21938, 18864, 42359, 42160, 43600, 111189, 27936, 44448,
    84835, 37744, 18936, 18800, 25776, 92326, 59984, 27296, 108228, 43744,
    37600, 53987, 51552, 54615, 54432, 55888, 23893, 22176, 42704, 21972,
    21200, 43448, 43344, 46240, 46758, 44368, 21920, 43940, 42416, 21168,
    45683, 26928, 29495, 27296, 44368, 84821, 19296, 42352, 21732, 53600,
    59752, 54560, 55968, 92838, 22224, 19168, 43476, 41680, 53584, 62034,
    54560,)

# 每年农历正月初一对应的公历日（YYYYMMDD）。
_NEW_YEAR = (
    '19000131', '19010219', '19020208', '19030129', '19040216', '19050204', '19060125', '19070213', '19080202', '19090122',
    '19100210', '19110130', '19120218', '19130206', '19140126', '19150214', '19160203', '19170123', '19180211', '19190201',
    '19200220', '19210208', '19220128', '19230216', '19240205', '19250124', '19260213', '19270202', '19280123', '19290210',
    '19300130', '19310217', '19320206', '19330126', '19340214', '19350204', '19360124', '19370211', '19380131', '19390219',
    '19400208', '19410127', '19420215', '19430205', '19440125', '19450213', '19460202', '19470122', '19480210', '19490129',
    '19500217', '19510206', '19520127', '19530214', '19540203', '19550124', '19560212', '19570131', '19580218', '19590208',
    '19600128', '19610215', '19620205', '19630125', '19640213', '19650202', '19660121', '19670209', '19680130', '19690217',
    '19700206', '19710127', '19720215', '19730203', '19740123', '19750211', '19760131', '19770218', '19780207', '19790128',
    '19800216', '19810205', '19820125', '19830213', '19840202', '19850220', '19860209', '19870129', '19880217', '19890206',
    '19900127', '19910215', '19920204', '19930123', '19940210', '19950131', '19960219', '19970207', '19980128', '19990216',
    '20000205', '20010124', '20020212', '20030201', '20040122', '20050209', '20060129', '20070218', '20080207', '20090126',
    '20100214', '20110203', '20120123', '20130210', '20140131', '20150219', '20160208', '20170128', '20180216', '20190205',
    '20200125', '20210212', '20220201', '20230122', '20240210', '20250129', '20260217', '20270206', '20280126', '20290213',
    '20300203', '20310123', '20320211', '20330131', '20340219', '20350208', '20360128', '20370215', '20380204', '20390124',
    '20400212', '20410201', '20420122', '20430210', '20440130', '20450217', '20460206', '20470126', '20480214', '20490202',
    '20500123', '20510211', '20520201', '20530219', '20540208', '20550128', '20560215', '20570204', '20580124', '20590212',
    '20600202', '20610121', '20620209', '20630129', '20640217', '20650205', '20660126', '20670214', '20680203', '20690123',
    '20700211', '20710131', '20720219', '20730207', '20740127', '20750215', '20760205', '20770124', '20780212', '20790202',
    '20800122', '20810209', '20820129', '20830217', '20840206', '20850126', '20860214', '20870203', '20880124', '20890210',
    '20900130', '20910218', '20920207', '20930127', '20940215', '20950205', '20960125', '20970212', '20980201', '20990121',
    '21000209',
)

_CN_DIGIT = {
    '零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
    '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
    '正': 1, '冬': 11, '腊': 12,   # 正月/冬月/腊月：农历月份别名，别当公历
}
_CN_RUN = r'[零〇一二三四五六七八九十廿卅正冬腊]+'

_CN_MONTH = ('', '正', '二', '三', '四', '五', '六', '七', '八', '九', '十', '冬', '腊')
_CN_DAY = tuple(
    [''] + '初一 初二 初三 初四 初五 初六 初七 初八 初九 初十'.split()
    + '十一 十二 十三 十四 十五 十六 十七 十八 十九 二十'.split()
    + '廿一 廿二 廿三 廿四 廿五 廿六 廿七 廿八 廿九 三十'.split()
)


def cn_num(s):
    """汉字数字 → int。「十五」15、「廿三」23、「三十」30、「五」5。认不出返回 None。"""
    if not s:
        return None
    s = s.replace('廿', '二十').replace('卅', '三十')
    if s in _CN_DIGIT:
        return _CN_DIGIT[s]
    if '十' in s:
        tens, _, ones = s.partition('十')
        if tens and tens not in _CN_DIGIT:
            return None
        if ones and ones not in _CN_DIGIT:
            return None
        return (tens and _CN_DIGIT[tens] or 1) * 10 + (ones and _CN_DIGIT[ones] or 0)
    return None


def _num(tok):
    """数字 token：阿拉伯数字直取，汉字走 cn_num。"""
    tok = tok.strip().lstrip('初')
    if tok.isdigit():
        return int(tok)
    return cn_num(tok)


def _month_big(code, m):
    """该农历年第 m 个月是否 30 天。"""
    return (code >> (12 - m + 4)) & 1


def leap_month_of(year):
    """该农历年的闰月月份，无闰月返回 0。超出范围返回 None。"""
    if not (1900 <= year <= 2100):
        return None
    return _YEAR_CODE[year - 1900] & 0xf


def lunar_to_solar(year, month, day, leap=False):
    """农历年月日 → date。越界、日期不存在、或无该闰月时返回 None。"""
    if not (1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 30):
        return None
    code = _YEAR_CODE[year - 1900]
    lm = code & 0xf
    if leap and lm != month:
        return None                      # 该年没有这个闰月
    if day == 30:
        big = ((code >> 16) & 1) if leap else _month_big(code, month)
        if not big:
            return None                  # 该月只有 29 天
    days = 0
    for m in range(1, month):
        days += 30 if _month_big(code, m) else 29
        if lm == m:                      # 闰月紧跟在同名月之后
            days += 30 if (code >> 16) & 1 else 29
    if leap:
        days += 30 if _month_big(code, month) else 29
    s = _NEW_YEAR[year - 1900]
    base = date(int(s[:4]), int(s[4:6]), int(s[6:]))
    return base + timedelta(days=days + day - 1)


# 出现这些词就一定是农历：「初五」公历里没有这种说法。「大年」= 春节语境（大年初一到初十
# 指正月、大年二十三~三十指腊月——小年之后、初十之后不再用「大年」）。
_LUNAR_HINT = re.compile(
    r'农历|陰曆|阴历|旧历|舊曆|大年|正[一二三四五六七八九十]?月|冬月|腊月|闰' + _CN_RUN + r'?月|初' + _CN_RUN)

# 「大年 + 日」：初X → 正月；数字日（二十三~三十）→ 腊月
_DANIAN_RE = re.compile(r'大年\s*(初[零〇一二三四五六七八九十]+|[零〇一二三四五六七八九十廿卅]{1,3})')
_LUNAR_RE = re.compile(
    r'(?:农历|陰曆|阴历|旧历|舊曆)?\s*(闰)?\s*('
    + _CN_RUN + r'|\d{1,2})\s*月\s*(初?' + _CN_RUN + r'|\d{1,2})\s*[日号]?')
_YEAR_IN_TEXT = re.compile(r'(\d{4})\s*年?')


def lunar_year_of(d_):
    """公历日期属于哪个农历年：按当年春节划分，春节前属上一个农历年。"""
    y = d_.year
    if 1900 <= y <= 2100:
        s = _NEW_YEAR[y - 1900]
        if d_ < date(int(s[:4]), int(s[4:6]), int(s[6:])):
            return y - 1
    return y


def nearest_occurrence(month, day, leap, near, bias=None):
    """缺年份时的消歧：在 near 所属农历年 ±1 里找该农历日。

    bias=None → 取离 near 最近的一次（春节前说腊月指即将到来、元旦后指刚过去，
    nearest 恰好覆盖两个窗口）；'past' → 只取 ≤near 的最近一次（「去年大年三十」）；
    'future' → 只取 ≥near 的最近一次（「计划大年三十出发」）。"""
    best = None
    base = lunar_year_of(near)
    for y in (base - 1, base, base + 1):
        out = lunar_to_solar(y, month, day, leap=leap)
        if out is None:
            continue
        if bias == 'past' and out > near:
            continue
        if bias == 'future' and out < near:
            continue
        if best is None or abs((out - near).days) < abs((best[0] - near).days):
            best = (out, y)
    return best


def _resolve_danian(text, near, default_year, bias=None):
    """「大年X」→ 正月（初X）或腊月（数字日），除夕小月自动回退廿九。

    「大年初一~初十」= 正月；「大年二十三~三十」= 腊月（小年/除夕语境）。
    显式年份指农历年（「2026年大年三十」= 农历 2026 年腊月三十，落在公历 2027 年初）。
    缺年份走最近出现消歧（与其它农历日一致），返回 inferred=True 供调用方要求确认。"""
    m = _DANIAN_RE.search(text)
    day_tok = m.group(1)
    month = 1 if day_tok.startswith('初') else 12
    day = _num(day_tok)
    if not day or not (1 <= day <= 30):
        return None, 'bad_lunar_number', False
    note = ''
    ym = _YEAR_IN_TEXT.search(text)
    if ym:
        year = int(ym.group(1))
        out = lunar_to_solar(year, month, day)
        if out is None and month == 12 and day == 30:
            out = lunar_to_solar(year, 12, 29)
            day = 29
            note = '（除夕；该年腊月无三十）'
        if out is None:
            return None, 'lunar_out_of_range_or_missing', False
        return out, '%d 年%s月%s%s' % (year, _CN_MONTH[month], _CN_DAY[day], note), False
    ref = near or date.today()
    got = nearest_occurrence(month, day, False, ref, bias)
    if got is None and month == 12 and day == 30:
        got = nearest_occurrence(12, 29, False, ref, bias)
        day = 29
        note = '（除夕；腊月无三十）'
    if got is None:
        return None, 'lunar_out_of_range_or_missing', False
    out, year = got
    return out, '%d 年%s月%s%s' % (year, _CN_MONTH[month], _CN_DAY[day], note), True


def parse_lunar(text, default_year=None, near=None, bias=None):
    """从中文串里抽农历日期。

    返回 (date, 说明, 是否代过年)。只处理「有月份」的写法；
    只说「初五」没有月份时返回 (None, 原因, False)，由调用方追问是哪个月。
    缺年份：near（今天的公历日期）给了就取「离 near 最近的一次该农历日」
    （bias='past'/'future' 时只朝该方向找），否则退回 default_year /
    系统当年（旧语义，兼容旧调用）。"""
    if _DANIAN_RE.search(text):
        return _resolve_danian(text, near, default_year, bias)
    m = _LUNAR_RE.search(text)
    if not m:
        return None, 'no_lunar_date', False
    leap, mo, dy = m.group(1), m.group(2), m.group(3)
    month, day = _num(mo), _num(dy)
    if not month or not day:
        return None, 'bad_lunar_number', False
    inferred = False
    ym = _YEAR_IN_TEXT.search(text)   # 年份可能写在「农历」之前或之后，单独抓
    if ym:
        year = int(ym.group(1))
    elif near is not None:
        got = nearest_occurrence(month, day, bool(leap), near, bias)
        if got is None:
            return None, 'lunar_out_of_range_or_missing', False
        out, year = got
        inferred = True
        tag = '%d 年农历%s%s月%s' % (year, '闰' if leap else '',
                                     _CN_MONTH[month], _CN_DAY[day])
        return out, tag, inferred
    else:
        year = default_year or date.today().year
    out = lunar_to_solar(year, month, day, leap=bool(leap))
    if out is None:
        return None, 'lunar_out_of_range_or_missing', False
    tag = '%d 年农历%s%s月%s' % (year, '闰' if leap else '', _CN_MONTH[month], _CN_DAY[day])
    return out, tag, inferred


def is_lunar(text):
    return bool(_LUNAR_HINT.search(text))


def solar_to_lunar(d_):
    """公历日 → (农历年, 月, 日, 是否闰月)。越界返回 None。

    逐月消耗偏移：正常月之后紧跟闰同名月（若有）。"""
    y = lunar_year_of(d_)
    if not (1900 <= y <= 2100):
        return None
    s = _NEW_YEAR[y - 1900]
    base = date(int(s[:4]), int(s[4:6]), int(s[6:]))
    rem = (d_ - base).days
    if rem < 0:
        return None
    code = _YEAR_CODE[y - 1900]
    lm = code & 0xf
    for m in range(1, 13):
        for is_leap in ((False, True) if lm == m else (False,)):
            length = 30 if ((_month_big(code, m) if not is_leap else (code >> 16) & 1)) else 29
            if rem < length:
                return y, m, rem + 1, is_leap
            rem -= length
    return None


# 纯农历日名：整串只有 初X / 十五 / 廿三 / 三十（可带农历前缀与「日/号」尾）
_DAY_NAME_RE = re.compile(
    r'^(?:农历|陰曆|阴历|旧历|舊曆)?\s*(初?[零〇一二三四五六七八九十廿卅]+)\s*[日号]?$')


def parse_lunar_day(text):
    """纯农历日名 → 日序号（1-30）。「初五」5、「十五」15、「廿三」23、「三十」30。

    只认「整串就是日名」的写法（有月份的走 parse_lunar）；认不出返回 None。"""
    m = _DAY_NAME_RE.match(str(text).strip())
    if not m:
        return None
    n = cn_num(m.group(1).lstrip('初'))
    return n if n is not None and 1 <= n <= 30 else None
