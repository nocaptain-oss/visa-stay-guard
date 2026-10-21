#!/bin/sh
# 全量验证：官方表 E 系列 + 聚合表 A 系列 + 13 场景回归。退出码 0 = 全绿。
cd "$(dirname "$0")" || exit 1
fail=0
for f in verify_fix.py verify_c.py verify_d.py verify_e.py verify_agg.py regress.py; do
  echo "===== $f ====="
  python3 "$f" || fail=1
done
echo
[ $fail -eq 0 ] && echo "ALL GREEN" || echo "SOME FAILED"
exit $fail
