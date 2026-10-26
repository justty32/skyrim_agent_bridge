#!/usr/bin/env bash
# wf-lint.sh — 工作流文檔的機械檢查
# 用法：wf-lint.sh [--strict] [--quiet] <dir>...；模板 repo 用 --self。
# 檢查連結／錨點、大小、殘留、BIGLIST、資料檔、QUERYCMD、inbox 與專案自訂檢查。
# BROKEN > 0 結束碼為 1；--strict 另把 residue/oversize/biglist/querycmd/local 算失敗。
set -u

strict=0 self=0 quiet=0
dirs=()
for a in "$@"; do
  case "$a" in
    --strict) strict=1 ;;
    --self) self=1 ;;
    --quiet|-q) quiet=1 ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) dirs+=("$a") ;;
  esac
done

tools=$(cd "$(dirname "$0")" && pwd)
have_py=1
command -v python3 >/dev/null 2>&1 || { have_py=0; echo "WARN python3 not found; BIGLIST/data-file checks skipped"; }

total_broken=0 total_residue=0 total_oversize=0 total_biglist=0 total_querycmd=0 total_local=0
checked_broken=0

# 檢查函式在同目錄的 wf-lint-checks.sh（母檔已到 8 KB 上限）。
. "$tools/wf-lint-checks.sh" || { echo "FATAL missing $tools/wf-lint-checks.sh" >&2; exit 2; }

# project-local hook：掃描目錄下有 <dir>/wf/tools/wf-lint-local.sh 就跑它，傳 <dir> 與 strict 旗標。
# 那支是 project-owned（不隨 tools/ 整包覆蓋被清掉），自己印 finding 行；非 0 結束碼計入 local=N，
# 比照 biglist 只在 --strict 才算失敗。--self（模板 repo 自檢）不跑。
use_local_hook=0
run_local_hook() {
  local hook="${1%/}/wf/tools/wf-lint-local.sh"
  [[ $use_local_hook -eq 1 && -f $hook ]] || return 0
  bash "$hook" "${1%/}" "$strict"
}

if [[ $self -eq 1 ]]; then
  repo=$(cd "$(dirname "$0")/.." && pwd)
  cd "$repo" || exit 2

  echo "== repo root files"
  check_links "$repo" < <({ printf '%s\n' README.md AGENTS.md CLAUDE.md IMPORT.md CHANGELOG.md; ls docs/*.md 2>/dev/null; } \
    | while read -r f; do [[ -f $f ]] && echo "$repo/$f"; done)
  b=$checked_broken; total_broken=$((total_broken + b)); echo "SUMMARY root: broken=$b"

  while IFS= read -r f; do echo "OVERSIZE ${f#$repo/} ($(wc -c <"$f") bytes > 8192)"; total_broken=$((total_broken + 1)); done \
    < <(list_oversize_files "$repo")

  lint_dir "$repo/template" template
  [[ -d "$repo/skills" ]] && lint_dir "$repo/skills" skills

  for ex in "$repo"/examples/*/; do
    [[ -d $ex ]] || continue
    saved=$strict; strict=1; before=$total_residue
    lint_dir "$ex" "examples/$(basename "$ex")"
    strict=$saved
    [[ $total_residue -gt $before ]] && { echo "RESIDUE examples/$(basename "$ex") must have 0 residue"; total_broken=$((total_broken + 1)); }
  done

  flavors=()
  for fl in "$repo"/flavors/*/; do flavors+=("$(basename "$fl")"); done
  all=$(IFS=,; echo "${flavors[*]}")
  for combo in "${flavors[@]}" "$all"; do
    for mode in std wf; do
      tmp=$(mktemp -d)
      if [[ $mode == std ]]; then
        "$repo/tools/wf-init.sh" --target "$tmp" --flavor "$combo" --quiet >/dev/null || echo "wf-init failed: $combo ($mode)"
      else
        "$repo/tools/wf-init.sh" --target "$tmp" --flavor "$combo" --non-invasive wf --quiet >/dev/null || echo "wf-init failed: $combo ($mode)"
      fi
      lint_dir "$tmp" "merged[$combo,$mode]"
      rm -rf "$tmp"
    done
  done
else
  [[ ${#dirs[@]} -eq 0 ]] && dirs=(.)
  use_local_hook=1
  for d in "${dirs[@]}"; do lint_dir "$d" "$d"; done
fi

echo "TOTAL broken=$total_broken$( [[ $strict -eq 1 ]] && echo " residue=$total_residue oversize=$total_oversize biglist=$total_biglist querycmd=$total_querycmd local=$total_local" )"
[[ $total_broken -gt 0 ]] && exit 1
[[ $strict -eq 1 && $((total_residue + total_oversize + total_biglist + total_querycmd + total_local)) -gt 0 ]] && exit 1
exit 0
