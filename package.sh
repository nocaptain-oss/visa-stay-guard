#!/bin/sh
# 提交包白名单打包：固化「提交清单」，防止 references/_src/ 明文源、tests/、
# COMPETITION-NOTES.md 开发日志混入提交包（手工 zip -r 整目录必然翻车）。
# 用法: sh package.sh [输出.zip]   默认 ../visa-stay-guard-submit.zip
set -e
cd "$(dirname "$0")"

FILES="
SKILL.md
REVIEW-BRIEF.md
scripts/visa_lookup.py
scripts/stay_calc.py
scripts/_lunar.py
scripts/_seal.py
references/countries.md
references/pools.md
references/rules-guide.md
references/visa-types.md
references/rules-data.bin
references/visa-types-aggregated.bin
references/visa-types-extended.bin
"

n=0
for f in $FILES; do
  [ -f "$f" ] || { echo "❌ 缺失: $f"; exit 1; }
  n=$((n + 1))
done

OUT="${1:-../visa-stay-guard-submit.zip}"
rm -f "$OUT"
# shellcheck disable=SC2086
zip -q "$OUT" $FILES

# 校验 1：包内只允许白名单路径（不允许任何白名单之外的文件/目录层级）
BAD=$(unzip -Z1 "$OUT" | grep -vE '^(SKILL\.md|REVIEW-BRIEF\.md|scripts/[^/]+\.py|references/[^/]+\.(md|bin))$' || true)
[ -z "$BAD" ] || { echo "❌ 包内出现白名单外文件:"; echo "$BAD"; rm -f "$OUT"; exit 1; }

# 校验 2：禁项零容忍（明文源 / 测试 / 开发日志 / git 元数据）
FORBIDDEN=$(unzip -Z1 "$OUT" | grep -E '_src|tests|COMPETITION|\.git|\.DS_Store|\.zip$' || true)
[ -z "$FORBIDDEN" ] || { echo "❌ 包内混入禁项:"; echo "$FORBIDDEN"; rm -f "$OUT"; exit 1; }

# 校验 3：文件数与白名单一致（防 zip 展开意外）
GOT=$(unzip -Z1 "$OUT" | wc -l | tr -d ' ')
[ "$GOT" = "$n" ] || { echo "❌ 文件数 $GOT ≠ 白名单 $n"; rm -f "$OUT"; exit 1; }

echo "✅ 提交包: $OUT"
echo "   文件数: $n / 体积: $(du -h "$OUT" | cut -f1)"
