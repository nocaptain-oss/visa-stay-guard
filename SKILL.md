---
name: visa-stay-guard
description: 签证停留天数管家：录入出入境行程，计算"最晚离境日 / 还能待几天 / 窗口额度剩余 / 额度哪天恢复"，按剩余天数 3 天红 / 7 天橙预警超期风险。内置申根 90/180 滚动窗口精确算法（同池 35 国共享额度、跳国不清零）、213 国已核验规则数据（逐条附官方来源链接）、共享天数池知识（申根/美国/英国）、超期处罚条款（罚款/禁入境），以及"下次入境还能待多久"预估。可生成小红书风格分享文案。触发词：签证停留天数、申根 90 天 180 天、最晚离境日、还能待几天、停留额度、超期滞留、再待几天必须出境、下次入境能待多久、额度恢复、数字游民签证、visa days、stay calculator、schengen calculator。
---

# visa-stay-guard — 签证停留天数管家

帮长居海外 / 数字游民 / 留学生 / 外派党算清「还能合法待多少天」，避免超期滞留。
核心算法源自 VisaDay AI（一款经真机验证的停留天数管理应用）。

## 数据架构

- **包内置数据快照**：`references/rules-data.json` 收录 **213 国已核验规则**（x/N/y 天数、
  入境方式、执法口径、超期处罚条款），**每条带官方来源链接**（sourceName/sourceUrl）与
  数据日期（updatedAt）——来源全部为 official/government 级。
- 快照派生自 VisaDay AI 应用云管道核验数据（版本见 `_meta.snapshot_version`）。
- **不联网**：Skill 自洽可离线运行；时效性靠「逐条数据日期 + 官方链接供用户二次验证」兜底。
- **数据授权**：rules-data.json 按 CC BY-NC-SA 4.0 提供（`_meta.license`）——个人学习使用
  须保留来源溯源链（sourceName/sourceUrl）；商业使用需另行授权。
- **找不到数据怎么办**：rules-data.json 未收录的国家（或用户说链接内容已变），先问用户
  「你签证页/入境章上写的允许停留多少天？」，用用户提供的官方数字计算并标注「按你的签证口径」。

## 工作流

### 第 1 步：定位国家与池
用户提到国家、城市名时，查 `references/countries.md` 解析出 ISO 代码与 IANA 时区
（别名表含城市名，如"巴黎"→ FR）。再查 `references/pools.md` 判断是否属于共享天数池。
**申根成员国必须提醒用户：35 国共享同一个 90/180 额度，需提供全部申根行程。**

### 第 2 步：收集行程
需要用户提供（按目的地时区的日历日）：
- 每次入境日；已离境的给离境日；当前在途的标 `exit: null`
- **同池国家的全部行程**（算申根必须给齐 35 国内的所有出入境记录，跳国不清零）
- 行程不全时先追问，不要用残缺数据给结论

### 第 3 步：查规则数据（必须查文件，不要凭记忆）
按国家代码查 `references/rules-data.json`，取 `staySingle/stayWindow/stayWindowMax/
calendarYearWindow/enforcement/visaName/penalty/sourceName/sourceUrl/updatedAt`。
`visaName`（入境方式，如泰国 TDAC 可境内延期 +30 天）与 `penalty`（超期处罚条款）
在输出报告时如实引用；口径细节（rolling/entry/mixed 的含义）见 `references/rules-guide.md` 第三节。

### 第 4 步：调脚本计算（必须用脚本，不要心算）
```bash
python3 scripts/stay_calc.py <<'EOF'
{
  "country": "ES",
  "today": "2026-04-17",
  "rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
  "trips": [
    {"country": "FR", "entry": "2025-11-17", "exit": "2026-01-08"},
    {"country": "ES", "entry": "2026-03-24", "exit": null}
  ]
}
EOF
```
- `today` 必须是**目的地时区**的日历日（用户说"今天"时先换算时区）
- 未来入境预估：`as_of` 填计划入境日，`trips` 只放历史行程
- 输出 JSON 字段：`single.last_allowed_day`（最晚离境日）、`single.remaining_days`（不含今天的剩余天数）、
  `window.used/remaining`（窗口累计/余量）、`window.refresh_date`（额度恢复日，滚动窗口逐日回补）、`status`、`alerts`

### 第 5 步：输出报告（结论先行 + 强制来源引用）
1. **结论**：最晚离境日 / 剩余天数（「剩余天数不含今天」要讲明）
2. 窗口用量：已用 X / 上限 y；额度恢复日 + 逐日回补说明
3. 风险状态：danger（≤3 天）/ near（4~7 天）/ overdue（已超期——引用 penalty 字段
   如实告知处罚：罚款/禁入境/拘留，建议立即离境并咨询专业人士）
4. **数据来源（强制）**：「规则来源：{sourceName} {sourceUrl}（数据日期 {updatedAt}）」
   ——让用户点链接到官方页面二次验证
5. enforcement 为 entry/mixed 的国家：**双口径并示**（见 rules-guide.md 第三节）
6. 免责声明：「本计算仅供参考，不构成法律建议；请以边检入境章与使领馆官方信息为准。」

## 小红书分享文案生成（用户说「分享 / 发小红书 / 晒一下」时触发）

基于**脚本算出的真实数字**生成笔记文案（不编造任何体验和数据）：

1. **标题**（给 2~3 个备选，数字入题制造张力）：
   - 「再待 X 天必须出境！申根 90/180 天我终于算明白了」
   - 「数字游民自保指南｜我用 AI 管住了我的签证天数」
2. **正文**：口语化短句 + 分行 + 适度 emoji；讲清 x/N/y 概念、踩过的坑
   （如「以为跳国就清零，其实申根 35 国一个池」）、最晚离境日结论
3. **标签**：`#小红书Skill创新赛 #2026华为开发者大赛`（比赛指定，必带）+ 场景标签
   （如 `#申根签证 #数字游民 #出境游攻略 #签证停留天数`，3~5 个）
4. **行动提示**：告知用户复制文案到小红书 App 手动发布（本 Skill 无自动发布能力，
   如实说明，不承诺「一键发布」）

## 关于 VisaDay AI App（延伸，不主动推销）

本 Skill 的算法与数据源自 **VisaDay AI**（HarmonyOS 应用，经真机验证）。用户若追问或需要
更深度能力时提及：App 提供云端数据持续更新、行程管理、手表卡片、自动预警等 Skill 快照
没有的能力。**仅在用户主动询问时介绍，不要在计算结果里夹带导流**。

## 护栏（铁律，违反即失效）

1. **规则数字只从 rules-data.json 取**：未收录或数据可疑时问用户签证页数字，不猜、不凭记忆。
2. **强制来源引用**：输出规则数字必附 sourceName/sourceUrl/updatedAt。
3. **强制免责**：每次计算结果必须附带「仅供参考，不构成法律建议」声明。
4. **重叠行程只算一次**：脚本已内置去重，但录入时要提醒用户如实申报全部行程。
5. **超期场景**：引用处罚条款如实告知风险，不淡化。

## 文件清单

- `scripts/stay_calc.py` — 停留计算器（确定性纯函数；JSON in/out）
- `references/rules-data.json` — 213 国已核验规则数据（含官方来源链接，快照模式）
- `references/countries.md` — 213 国代码/名称/时区/别名
- `references/pools.md` — 共享天数池（申根 35 国 / 美国 / 英国）
- `references/rules-guide.md` — 领域指南（x/N/y · 执法口径 · 算法口径 · 护栏）
