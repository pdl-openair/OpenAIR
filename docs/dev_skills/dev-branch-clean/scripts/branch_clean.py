#!/usr/bin/env python3
"""dev-branch-clean: git 分支分级清理工具。

对本地和远端分支按「合并状态(含 patch 等价)/ 保护名单 / 独有提交」分级:
  - PROTECTED  命中保护名单(集成分支、release/keep/archive/backup 前缀、open PR、
               任一 worktree 正在 checkout 的分支),永不删除
  - DELETE     已完全合入 base(祖先合并,或全部独有 commit 与 base patch 等价
               即 squash-merged),可安全删除
  - REVIEW     存在 base 没有的真实提交,只进报告,绝不自动删

用法:
  python branch_clean.py report [--base dev] [--stale-days 21] [--out-dir DIR]
                                [--no-fetch] [--extra-keep PATTERN ...]
  python branch_clean.py execute --plan <plan.json> --yes

report 产出 plan-<ts>.json(机器可执行清单)与 report-<ts>.md(人读报告),
默认写入 .git/branch-clean/(不会被 git 跟踪)。
execute 会逐条重新校验(分支仍存在、合并状态未变),校验失败即跳过并记录。
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---- 配置 ----

PROTECTED_NAMES = {"dev", "main", "master", "test"}
PROTECTED_PATTERNS = ["release/*", "keep/*", "archive/*", "backup/*"]
WORKTREE_ALLOWED_ROOTS = [".nightly_worktrees", ".claude/worktrees"]  # 只允许移除这些根下的 worktree
PUSH_DELETE_CHUNK = 40  # 每次批量 push --delete 的分支数
REVIEW_SAMPLE_COMMITS = 8


def run(cmd, cwd=None, check=True):
    """运行子进程,utf-8 解码避免中文 commit subject 在 gbk 控制台下炸掉。"""
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}"
        )
    return proc


def git(args, check=True):
    return run(["git"] + args, check=check)


def git_out(args, check=True):
    return git(args, check=check).stdout.strip()


# ---- 数据采集 ----

def repo_root():
    return Path(git_out(["rev-parse", "--show-toplevel"])).resolve()


def fetch_prune():
    print("git fetch --prune origin ...")
    proc = git(["fetch", "--prune", "origin"], check=False)
    if proc.returncode != 0:
        print(f"warning: fetch 失败(离线?),继续用本地远端引用分析\n{proc.stderr.strip()}")
    else:
        print("fetch done")


def list_branches():
    """返回 [(kind, name, sha, ts, upstream, track)]。name 为展示名(不含 refs 前缀、不含 origin/)。"""
    fmt = "%(refname)%00%(objectname)%00%(committerdate:unix)%00%(upstream:short)%00%(upstream:track)"
    out = git_out(["for-each-ref", f"--format={fmt}", "refs/heads", "refs/remotes/origin"])
    rows = []
    for line in out.splitlines():
        if not line:
            continue
        ref, sha, ts, upstream, track = (line.split("\x00") + ["", ""])[:5]
        if ref.startswith("refs/heads/"):
            kind, name = "local", ref[len("refs/heads/"):]
        elif ref.startswith("refs/remotes/origin/"):
            kind, name = "remote", ref[len("refs/remotes/origin/"):]
            if name == "HEAD":
                continue
        else:
            continue
        rows.append((kind, name, sha, int(ts) if ts else 0, upstream, track))
    return rows


def list_worktrees():
    """返回 [{path, branch}]。branch 为 refs/heads/ 短名;detached/主 worktree branch=None。"""
    out = git_out(["worktree", "list", "--porcelain"])
    worktrees, cur = [], {}
    for line in out.splitlines():
        if not line:
            if cur:
                worktrees.append(cur)
                cur = {}
            continue
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree "):], "branch": None}
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            cur["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
    if cur:
        worktrees.append(cur)
    return worktrees


def list_open_pr_heads():
    """gh 可用则返回 open PR 的 head 分支名集合;失败返回 None(调用方降级告警)。"""
    proc = run(["gh", "pr", "list", "--state", "open", "--limit", "300",
                "--json", "headRefName"], check=False)
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout or "[]")
        return {item["headRefName"] for item in data if item.get("headRefName")}
    except json.JSONDecodeError:
        return None


# ---- 判定 ----

def is_ancestor(sha, base_sha):
    return git(["merge-base", "--is-ancestor", sha, base_sha], check=False).returncode == 0


def count(a, b):
    return int(git_out(["rev-list", "--count", f"{a}..{b}"]) or 0)


def cherry_plus(base_sha, sha):
    """base...sha 中,sha 侧未被 base patch 等价覆盖的 commit 数(即真实独有)。"""
    out = git_out(["rev-list", "--left-right", "--cherry-pick", "--count", f"{base_sha}...{sha}"])
    left, right = out.split()
    return int(right)


def merge_contained(base_sha, base_tree, sha):
    """squash-merge 检测:branch 并入 base 若为 no-op(结果树==base 树),内容已全在 base。

    cherry-pick 等价认不出"N 个 commit 压成 1 个"的 squash;merge-tree 看的是最终内容。
    冲突/旧版 git 不支持则返回 False(保守落入 REVIEW)。
    """
    proc = git(["merge-tree", "--write-tree", base_sha, sha], check=False)
    if proc.returncode != 0:
        return False
    tree = proc.stdout.splitlines()[0].strip() if proc.stdout else ""
    return bool(tree) and tree == base_tree


def sample_subjects(base_sha, sha, n=REVIEW_SAMPLE_COMMITS):
    out = git_out(["log", f"-{n}", "--format=%s", f"{base_sha}..{sha}"], check=False)
    return [s for s in out.splitlines() if s.strip()]


def match_protection(name, kind, extra_keeps, worktree_branches, open_prs):
    """返回保护原因字符串;未命中返回 None。"""
    if name in PROTECTED_NAMES:
        return "integration branch"
    for pat in PROTECTED_PATTERNS + list(extra_keeps):
        if fnmatch.fnmatch(name, pat):
            return f"pattern {pat}"
    if open_prs and name in open_prs:
        return "open PR"
    if kind == "local" and name in worktree_branches:
        return "checked out in worktree"
    return None


def analyze(base, extra_keeps, stale_days, no_gh=False):
    base_ref = f"origin/{base}"
    base_sha = git_out(["rev-parse", base_ref])

    worktrees = list_worktrees()
    main_wt = str(repo_root())
    wt_by_branch = {w["branch"]: w["path"] for w in worktrees if w.get("branch")}

    open_prs = None if no_gh else list_open_pr_heads()
    if open_prs is None:
        print("warning: gh 不可用,open-PR 保护未生效(报告会标注)")
        open_prs = set()

    branches = []
    base_tree = git_out(["rev-parse", f"{base_sha}^{{tree}}"])
    now = time.time()
    for kind, name, sha, ts, upstream, track in list_branches():
        protect_reason = match_protection(name, kind, extra_keeps, wt_by_branch, open_prs)
        merged = is_ancestor(sha, base_sha)
        squash_contained = False
        if merged:
            plus = 0
        else:
            plus = cherry_plus(base_sha, sha)
            if plus > 0:
                squash_contained = merge_contained(base_sha, base_tree, sha)
        effective_merged = merged or plus == 0 or squash_contained
        age_days = int((now - ts) / 86400) if ts else -1

        entry = {
            "kind": kind, "name": name, "sha": sha,
            "date": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else "?",
            "age_days": age_days, "upstream": upstream or "", "track": track or "",
            "merged_ancestor": merged,
            "unique_raw": 0 if merged else count(base_sha, sha),
            "unique_plus": plus,
            "effective_merged": effective_merged,
            "open_pr": bool(open_prs and name in open_prs),
            "worktree": wt_by_branch.get(name, "") if kind == "local" else "",
        }
        if protect_reason:
            entry["verdict"], entry["reason"] = "PROTECTED", protect_reason
        elif effective_merged:
            how = ("ancestor" if merged
                   else "patch-equivalent" if plus == 0
                   else "merge-tree contained (squash)")
            gone = " upstream gone" if "[gone]" in track else ""
            entry["verdict"] = "DELETE"
            entry["reason"] = f"merged into {base} ({how}){gone}"
        else:
            entry["verdict"] = "REVIEW"
            entry["reason"] = f"{plus} unique commit(s) not in {base}"
        branches.append(entry)

    # REVIEW 分支补上下文(数量少,才做昂贵的补充查询)
    for e in branches:
        if e["verdict"] == "REVIEW":
            e["behind"] = count(e["sha"], base_sha)
            e["sample"] = sample_subjects(base_sha, e["sha"])

    # 待移除 worktree:允许根下、非主 worktree、其分支已无活动(超期)或已分类 DELETE
    remove_wts = []
    for w in worktrees:
        path = w["path"]
        if path == main_wt or not w.get("branch"):
            continue
        rel = Path(path).resolve().relative_to(repo_root()).as_posix() if Path(path).resolve().is_relative_to(repo_root()) else path
        if not any(rel.startswith(r) for r in WORKTREE_ALLOWED_ROOTS):
            continue
        b = next((e for e in branches if e["kind"] == "local" and e["name"] == w["branch"]), None)
        if b is None or b["age_days"] > stale_days or b["verdict"] == "DELETE":
            remove_wts.append({
                "path": path, "branch": w["branch"],
                "reason": "branch merged" if (b and b["verdict"] == "DELETE") else "stale worktree",
            })

    return {
        "base": base, "base_sha": base_sha,
        "open_pr_heads": sorted(open_prs),
        "branches": branches,
        "remove_worktrees": remove_wts,
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


# ---- 报告 ----

def plan_actions(result):
    del_remote = [e for e in result["branches"] if e["verdict"] == "DELETE" and e["kind"] == "remote"]
    del_local = [e for e in result["branches"] if e["verdict"] == "DELETE" and e["kind"] == "local"]
    review = [e for e in result["branches"] if e["verdict"] == "REVIEW"]
    protected = [e for e in result["branches"] if e["verdict"] == "PROTECTED"]
    return del_remote, del_local, review, protected


def render_report(result, plan_path, report_path):
    del_remote, del_local, review, protected = plan_actions(result)
    base = result["base"]
    lines = []
    a = lines.append
    a(f"# Branch Clean Report — {result['generated']}")
    a("")
    a(f"- base: `{result['base']}` @ `{result['base_sha'][:9]}`")
    a(f"- 远端可删: **{len(del_remote)}** | 本地可删: **{len(del_local)}** | "
      f"待审(只报告): **{len(review)}** | 保护: **{len(protected)}** | "
      f"待移除 worktree: **{len(result['remove_worktrees'])}**")
    if not result["open_pr_heads"] and result.get("gh_warning"):
        a("- ⚠️ gh 不可用,open-PR 保护未生效")
    a("")
    a(f"## DELETE — 远端已合并(将 `git push origin --delete`,共 {len(del_remote)})")
    a("")
    a("| branch | last commit | 判定 |")
    a("|---|---|---|")
    for e in sorted(del_remote, key=lambda x: x["date"]):
        a(f"| `{e['name']}` | {e['date']} | {e['reason']} |")
    a("")
    a(f"## DELETE — 本地已合并(将 `git branch -d`,共 {len(del_local)})")
    a("")
    a("| branch | last commit | upstream | 判定 |")
    a("|---|---|---|---|")
    for e in sorted(del_local, key=lambda x: x["date"]):
        up = e["track"] or e["upstream"] or "-"
        a(f"| `{e['name']}` | {e['date']} | {up} | {e['reason']} |")
    a("")
    a(f"## Worktree 移除(共 {len(result['remove_worktrees'])})")
    a("")
    for w in result["remove_worktrees"]:
        a(f"- `{w['path']}` — branch `{w['branch']}` ({w['reason']})")
    a("")
    a(f"## REVIEW — 有独有提交,只报告不删(共 {len(review)})")
    a("")
    for e in sorted(review, key=lambda x: x["date"]):
        up = f", upstream {e['track']}" if e["track"] else ""
        a(f"### `{e['kind']}/{e['name']}` — {e['date']} (+{e['unique_plus']} 独有 / -{e.get('behind', '?')} 落后{up})")
        for s in e.get("sample", []):
            a(f"  - {s}")
        a("")
    a(f"## PROTECTED(共 {len(protected)},未列全见 plan json)")
    a("")
    for e in sorted(protected, key=lambda x: (x["kind"], x["name"])):
        a(f"- `{e['kind']}/{e['name']}` — {e['reason']}")
    a("")
    a("## 执行")
    a("")
    a(f"```bash\npython docs/dev_skills/dev-branch-clean/scripts/branch_clean.py execute --plan {plan_path} --yes\n```")
    Path(report_path).write_text("\n".join(lines), encoding="utf-8")
    return lines


# ---- 执行 ----

def remote_heads_now():
    """一次 ls-remote 拿远端当前真实分支 -> {name: sha}。"""
    out = git_out(["ls-remote", "--heads", "origin"], check=False)
    heads = {}
    for line in out.splitlines():
        if not line:
            continue
        sha, ref = line.split("\t")
        if ref.startswith("refs/heads/"):
            heads[ref[len("refs/heads/"):]] = sha
    return heads


def cmd_execute(plan_path, yes):
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    base = plan["base"]
    base_sha = git_out(["rev-parse", f"origin/{base}"])
    log = [f"# Branch Clean Exec Log — {datetime.now().astimezone().isoformat(timespec='seconds')}"]
    if not yes:
        print("refusing: 需要 --yes(先向用户展示报告并取得确认)")
        return 2

    # 1) worktree 移除(必须先于分支删除)
    root = repo_root()
    for w in plan["remove_worktrees"]:
        path = Path(w["path"])
        if not path.exists():
            log.append(f"SKIP worktree(不存在): {w['path']}")
            continue
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            log.append(f"SKIP worktree(不在仓库内,拒绝处理): {w['path']}")
            continue
        if not any(rel.startswith(r) for r in WORKTREE_ALLOWED_ROOTS):
            log.append(f"SKIP worktree(不在允许根下): {w['path']}")
            continue
        proc = git(["worktree", "remove", "--force", str(path)], check=False)
        log.append(("OK   worktree removed: " if proc.returncode == 0 else "FAIL worktree remove: ") + rel
                   + ("" if proc.returncode == 0 else f"\n      {proc.stderr.strip()}"))

    # 2) 本地分支删除(逐条重校验)
    wt_branches = {w.get("branch") for w in list_worktrees()}
    for e in plan["delete_local"]:
        name = e["name"]
        try:
            cur = git_out(["rev-parse", f"refs/heads/{name}"], check=False)
        except RuntimeError:
            cur = ""
        if not cur:
            log.append(f"SKIP local(已不存在): {name}")
            continue
        if name in wt_branches:
            log.append(f"SKIP local(仍被 worktree 占用): {name}")
            continue
        sha = cur  # rev-parse 校验存在性
        if sha != e["sha"]:
            # 分支动过:用当前 sha 重新判定
            if not (is_ancestor(sha, base_sha) or cherry_plus(base_sha, sha) == 0):
                log.append(f"SKIP local(分支已移动且不再满足合并判定): {name}")
                continue
        proc = git(["branch", "-d", name], check=False)
        log.append(("OK   local deleted: " if proc.returncode == 0 else "FAIL local delete: ") + name
                   + ("" if proc.returncode == 0 else f"\n      {proc.stderr.strip()}"))

    # 3) 远端分支批量删除(一次 ls-remote 校准 + 分批 push)
    heads = remote_heads_now()
    pending = []
    for e in plan["delete_remote"]:
        name = e["name"]
        cur = heads.get(name)
        if cur is None:
            log.append(f"SKIP remote(远端已不存在): {name}")
            continue
        if cur != e["sha"] and not (is_ancestor(cur, base_sha) or cherry_plus(base_sha, cur) == 0):
            log.append(f"SKIP remote(分支已移动且不再满足合并判定): {name}")
            continue
        pending.append(name)

    open_prs = list_open_pr_heads() or set()
    for i in range(0, len(pending), PUSH_DELETE_CHUNK):
        chunk = [n for n in pending[i:i + PUSH_DELETE_CHUNK] if n not in open_prs]
        if not chunk:
            continue
        refspecs = [f"refs/heads/{n}" for n in chunk]
        proc = git(["push", "origin", "--delete"] + refspecs, check=False)
        if proc.returncode == 0:
            log.append(f"OK   remote deleted x{len(chunk)}: {', '.join(chunk)}")
        else:
            log.append(f"FAIL batch push({proc.stderr.strip()[:300]}),逐条重试:")
            for n in chunk:
                p1 = git(["push", "origin", "--delete", f"refs/heads/{n}"], check=False)
                log.append(("  OK   remote deleted: " if p1.returncode == 0 else "  FAIL remote delete: ") + n)

    out = Path(plan.get("out_dir", ".git/branch-clean")) / f"exec-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(log) + "\n", encoding="utf-8")
    ok = sum(1 for l in log if l.startswith("OK"))
    fail = sum(1 for l in log if "FAIL" in l.split(":")[0])
    print(f"\ndone: {ok} OK / {fail} FAIL  — 明细 {out}")
    for l in log:
        if "FAIL" in l or l.startswith("SKIP"):
            print(l)
    return 0 if fail == 0 else 1


# ---- 入口 ----


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="分析并产出报告与执行计划(不删任何东西)")
    r.add_argument("--base", default="dev")
    r.add_argument("--stale-days", type=int, default=21)
    r.add_argument("--out-dir", default=".git/branch-clean")
    r.add_argument("--no-fetch", action="store_true")
    r.add_argument("--no-gh", action="store_true")
    r.add_argument("--extra-keep", action="append", default=[], metavar="PATTERN",
                   help="额外的保护 fnmatch 模式,可多次")

    x = sub.add_parser("execute", help="按 plan 执行(逐条重校验)")
    x.add_argument("--plan", required=True)
    x.add_argument("--yes", action="store_true")

    args = ap.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if args.cmd == "execute":
        return cmd_execute(args.plan, args.yes)

    if not args.no_fetch:
        fetch_prune()
    result = analyze(args.base, args.extra_keep, args.stale_days, args.no_gh)
    if not result["open_pr_heads"]:
        result["gh_warning"] = True

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    plan_path = out_dir / f"plan-{ts}.json"
    report_path = out_dir / f"report-{ts}.md"

    del_remote, del_local, review, protected = plan_actions(result)
    plan = {
        "generated": result["generated"], "base": result["base"], "base_sha": result["base_sha"],
        "out_dir": str(out_dir), "repo": str(repo_root()),
        "delete_remote": [{k: e[k] for k in ("name", "sha", "date", "reason")} for e in del_remote],
        "delete_local": [{k: e[k] for k in ("name", "sha", "date", "reason")} for e in del_local],
        "remove_worktrees": result["remove_worktrees"],
    }
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = render_report(result, plan_path, report_path)
    n = len(lines)
    print("\n".join(lines[:80]))
    if n > 80:
        print(f"... (其余 {n - 80} 行见 {report_path})")
    print(f"\nplan:   {plan_path}\nreport: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
