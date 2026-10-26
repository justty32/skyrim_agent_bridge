#!/usr/bin/env bash
# wf-lint-checks.sh — wf-lint.sh 的檢查函式；由 wf-lint.sh source，不單獨執行。
# 用到呼叫端已設好的 $tools／$have_py／$quiet／$strict／checked_broken、total_* 計數與 run_local_hook()。

extract_links() {
  awk '/^[[:space:]]*```/{c=!c; next} !c' "$1" \
    | sed -E 's/`[^`]*`//g' \
    | grep -oE '\]\([^)]+\)' \
    | sed -E 's/^\]\(//; s/\)$//; s/ "[^"]*"$//'
}

# 目標含 percent-encoding 時，解碼後再判存在。
link_exists() {
  local target=$1 decoded esc='\x'
  [[ -e $target ]] && return 0
  [[ $target == *%* ]] || return 1
  decoded=$(printf '%b' "${target//%/$esc}" 2>/dev/null) || return 1
  [[ -n $decoded && -e $decoded ]]
}

check_links() {
  local prefix=${1%/} f d l broken=0
  while IFS= read -r f; do
    d=$(dirname "$f")
    while IFS= read -r l; do
      [[ -z $l ]] && continue
      case $l in http://*|https://*|mailto:*|'#'*|'<'*) continue ;; esac
      l=${l%%#*}
      [[ -z $l ]] && continue
      if ! link_exists "$d/$l"; then
        echo "BROKEN ${f#$prefix/} -> $l"
        broken=$((broken + 1))
      fi
    done < <(extract_links "$f")
  done
  checked_broken=$broken
}

# archive/reference/vendor（含依協議分層的 done/superseded，如 dispatch 的 handoff）、放信的 inbox/（寄件原文不改；
# workflows/inbox/ 是工作流文件，照掃）與 .gitmodules 宣告的 submodule 不下鑽。
list_owned_files() {
  local root=${1%/} _ rel
  shift
  local -a prunes=(
    -path '*/.git' -o -path '*/node_modules' -o -path '*/.venv' -o -path '*/venv' -o -path '*/__pycache__'
    -o -path '*/archive' -o -path '*/reference' -o -path '*/references' -o -path '*/vendor'
    -o -path '*/done' -o -path '*/superseded'
    -o \( -path '*/inbox' ! -path '*/workflows/inbox' \)
  )
  # .gitmodules 在 git 頂層，$root 可能是子目錄（例 skills/）；宣告過的 submodule 路徑不論落在哪一層都不下鑽。
  local top; top=$(git -C "$root" rev-parse --show-toplevel 2>/dev/null) || top=""
  while read -r _ rel; do
    [[ -n $rel ]] && prunes+=( -o -path "$root/${rel%/}" -o -path "*/${rel%/}" )
  done < <(git -C "${top:-$root}" config --file "${top:-$root}/.gitmodules" --get-regexp '^submodule\..*\.path$' 2>/dev/null || true)
  find "$root" \( -type d \( "${prunes[@]}" \) \) -prune \
    -o -type f \( "$@" \) -print | drop_ignored "$root" | sort
}

# git repo 內丟掉 .gitignore 命中的檔（他人 clone、產物）；tracked 檔不算 ignored。非 repo、或 $root 本身被 ignore（明指要掃）時照舊。
drop_ignored() {
  local root=$1 f
  if ! git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1 || git -C "$root" check-ignore -q . 2>/dev/null; then
    cat; return
  fi
  local -a files=(); local -A ign=()
  mapfile -t files
  [[ ${#files[@]} -eq 0 ]] && return
  while IFS= read -r -d '' f; do ign[$f]=1; done \
    < <(printf '%s\0' "${files[@]#"$root"/}" | git -C "$root" check-ignore -z --stdin 2>/dev/null)
  for f in "${files[@]}"; do [[ -n ${ign[${f#"$root"/}]:-} ]] || printf '%s\n' "$f"; done
}

list_md() { list_owned_files "$1" -name '*.md'; }
list_data() { list_owned_files "$1" -name '*.json' -o -name '*.csv'; }

# 全 repo 超標掃描：排除 .git／__pycache__、.venv／venv（Python 虛擬環境）、agent 工具的
# worktree 暫存區（repo 的巢狀副本）與 .gitmodules 宣告的 submodule（上游 vendor 碼不受 8 KB 限制）。
list_oversize_files() {
  local root=${1%/} _ rel
  local -a prunes=( -path '*/.git' -o -path '*/.venv' -o -path '*/venv' -o -path '*/__pycache__' -o -path '*/.claude/worktrees' )
  while read -r _ rel; do
    [[ -n $rel ]] && prunes+=( -o -path "$root/${rel%/}" )
  done < <(git -C "$root" config --file .gitmodules --get-regexp '^submodule\..*\.path$' 2>/dev/null || true)
  find "$root" \( -type d \( "${prunes[@]}" \) \) -prune -o -type f -size +8192c -print | sort
}

count_literal() {
  local needle=$1
  shift
  [[ $# -eq 0 ]] && { echo 0; return; }
  grep -hoF -- "$needle" "$@" 2>/dev/null | wc -l | tr -d ' '
}

lint_dir() {
  local root=${1%/} label=${2:-$1}
  local broken=0 oversize=0 ph=0 note=0 judge=0 inbox=0 biglist=0 bl_links=0 querycmd=0 lcl=0
  local out df n rel
  local -a md_files=()

  [[ $quiet -eq 1 ]] || echo "== $label"
  mapfile -t md_files < <(list_md "$root")
  check_links "$root" < <(printf '%s\n' "${md_files[@]}")
  broken=$checked_broken

  if [[ $have_py -eq 1 ]]; then
    out=$(python3 "$tools/check_anchors.py" "$root" 2>/dev/null)
    if [[ -n $out ]]; then
      printf '%s\n' "$out"
      broken=$((broken + $(printf '%s\n' "$out" | grep -c '^BROKEN-ANCHOR ')))
    fi
  fi

  for f in "${md_files[@]}"; do
    case "$f" in */workflows/*) ;; *) continue ;; esac
    n=$(wc -c <"$f")
    [[ $n -le 8192 ]] && continue
    echo "OVERSIZE ${f#$root/} ($n bytes > 8192)"
    oversize=$((oversize + 1))
  done

  if [[ $have_py -eq 1 ]]; then
    local -a bl_files=()
    for f in "${md_files[@]}"; do
      case "$f" in */skills/*) continue ;; esac
      bl_files+=("$f")
    done
    if [[ ${#bl_files[@]} -gt 0 ]]; then
      out=$(python3 "$tools/find_big_lists.py" --min 1024 \
              --exempt-file AGENTS.md --exempt-file WORKFLOWS.md --exempt-file INDEX.md \
              "${bl_files[@]}" 2>/dev/null \
            | awk -F'\t' -v p="$root/" '{
                loc=$2; if (substr(loc,1,length(p))==p) loc=substr(loc,length(p)+1)
                sub(/^links=/, "", $5)
                printf "%s %s (%s bytes, %s, %s rows, %s links)\n",
                       ($6=="linked=all" ? "BIGLIST-LINKS" : "BIGLIST"), loc, $1, $3, $4, $5 }')
      if [[ -n $out ]]; then
        echo "$out"
        biglist=$(printf '%s\n' "$out" | grep -c '^BIGLIST ')
        bl_links=$(printf '%s\n' "$out" | grep -c '^BIGLIST-LINKS ')
      fi
    fi

    while IFS= read -r df; do
      [[ -z $df ]] && continue
      case "$df" in *.json) grep -q '"contract": "wf-table/' "$df" || continue ;; esac
      out=$(python3 "$tools/tabledb.py" check "$df" 2>/dev/null)
      n=$(printf '%s\n' "$out" | grep -c '"target"')
      [[ $n -eq 0 ]] && continue
      rel=${df#$root/}
      printf '%s\n' "$out" | awk -F'"' -v f="$rel" '
        /"index":/  { s=$3; gsub(/[^0-9]/, "", s); idx=s }
        /"column":/ { col=$4 }
        /"target":/ { print "BROKEN " f "[" idx "." col "] -> " $4 }'
      broken=$((broken + n))
    done < <(list_data "$root")
  fi

  for f in "${md_files[@]}"; do
    rel=${f#$root/}
    case "$f" in */archive/*|*/wf/*|*/workflows/tidy/*|*/skills/*) continue ;; esac
    case "${f##*/}" in AGENTS.md|data-files.md|data-files-fmt.md|tidy.md) continue ;; esac
    while IFS= read -r ln; do
      echo "QUERYCMD $rel:$ln"; querycmd=$((querycmd + 1))
    done < <(grep -nE 'tabledb\.py' "$f" | grep -E 'python3 |tools/tabledb\.py' | cut -d: -f1)
  done

  run_local_hook "$root" || lcl=1

  ph=$(count_literal '{{' "${md_files[@]}")
  note=$(count_literal '〔模板說明〕' "${md_files[@]}")
  judge=$(count_literal '〔導入判斷〕' "${md_files[@]}")
  if [[ -d "$root/inbox" ]]; then
    inbox=$(find "$root/inbox" -maxdepth 1 -name '*.md' | wc -l | tr -d ' ')
  fi
  local residue=$((ph + note + judge))

  if [[ $quiet -eq 0 || $broken -gt 0 ]]; then
    echo "SUMMARY $label: broken=$broken oversize=$oversize biglist=$biglist biglist_links=$bl_links querycmd=$querycmd local=$lcl residue={{=$ph 模板說明=$note 導入判斷=$judge inbox_pending=$inbox"
  fi
  total_broken=$((total_broken + broken))
  if [[ $strict -eq 1 ]]; then
    total_residue=$((total_residue + residue))
    total_oversize=$((total_oversize + oversize))
    total_biglist=$((total_biglist + biglist))
    total_querycmd=$((total_querycmd + querycmd))
    total_local=$((total_local + lcl))
  fi
  return 0
}
