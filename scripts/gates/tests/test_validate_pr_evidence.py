"""validate_pr_evidence 单测：覆盖每种失败模式（B3 规格 §2.1-2）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import validate_pr_evidence as vpe  # noqa: E402

GOOD_BODY = """## 改了什么

- 新增 validate_pr_evidence 校验器

## 关联任务

Closes #1

## 没改什么

N/A

## 主要修改文件

- scripts/gates/validate_pr_evidence.py

## 怎么验证

验证层级: L3

- 验证步骤：pytest scripts/gates/tests -q
- 预期结果：全绿

## 影响面勾选

- [x] 均不涉及
- [ ] 影响 shared 类型

## CI run 链接

https://github.com/example/openair/actions/runs/123

## 风险分级

风险分级: medium

补充说明：无
"""


def _run(tmp_path, body, title="", extra_args=None):
    f = tmp_path / "pr_body.md"
    f.write_text(body, encoding="utf-8")
    args = ["--file", str(f)]
    if title:
        args += ["--title", title]
    if extra_args:
        args += extra_args
    return vpe.main(args)


# ---------- 全绿路径 ----------


def test_good_body_passes(tmp_path, capsys):
    assert _run(tmp_path, GOOD_BODY) == 0
    out = capsys.readouterr().out
    assert "0 FAIL" in out
    assert "验证层级: L3" in out
    assert "风险分级: medium" in out


def test_na_local_ok_for_non_fix(tmp_path):
    body = GOOD_BODY.replace(
        "https://github.com/example/openair/actions/runs/123", "N/A（本地验证）"
    )
    assert _run(tmp_path, body) == 0


def test_from_env_mode(monkeypatch, capsys):
    monkeypatch.setenv("PR_BODY", GOOD_BODY)
    monkeypatch.setenv("PR_TITLE", "feat(api): add validator")
    assert vpe.main(["--from-env"]) == 0


# ---------- 六节缺失/为空 ----------


def test_missing_section_fails(tmp_path, capsys):
    body = GOOD_BODY.replace("## CI run 链接", "## CI 记录不相关标题")
    assert _run(tmp_path, body) == 1
    out = capsys.readouterr().out
    assert "必填节缺失: CI run 链接" in out


def test_missing_verify_section_fails(tmp_path, capsys):
    body = GOOD_BODY.replace("## 怎么验证", "## 验证记录别的名字")
    assert _run(tmp_path, body) == 1
    assert "必填节缺失: 怎么验证" in capsys.readouterr().out


def test_empty_section_fails(tmp_path, capsys):
    body = GOOD_BODY.replace("- 新增 validate_pr_evidence 校验器\n", "-\n", 1)
    assert _run(tmp_path, body) == 1
    assert "必填节为空" in capsys.readouterr().out


def test_section_with_only_comment_counts_empty(tmp_path):
    body = GOOD_BODY.replace(
        "- 新增 validate_pr_evidence 校验器", "<!-- 只有注释不算内容 -->"
    )
    assert _run(tmp_path, body) == 1


# ---------- 验证层级行 ----------


def test_missing_level_line_fails(tmp_path, capsys):
    """D5 场景：缺『验证层级』行 → 退出 1 且 FAIL 项指明。"""
    body = GOOD_BODY.replace("验证层级: L3\n", "")
    assert _run(tmp_path, body) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "验证层级" in out


def test_level_out_of_range_fails(tmp_path):
    body = GOOD_BODY.replace("验证层级: L3", "验证层级: L7")
    assert _run(tmp_path, body) == 1


def test_level_l6_accepted(tmp_path, capsys):
    body = GOOD_BODY.replace("验证层级: L3", "验证层级: L6")
    assert _run(tmp_path, body) == 0
    assert "L6" in capsys.readouterr().out


# ---------- 影响面勾选 ----------


def test_no_checked_box_fails(tmp_path, capsys):
    body = GOOD_BODY.replace("- [x] 均不涉及", "- [ ] 均不涉及")
    assert _run(tmp_path, body) == 1
    assert "影响面勾选" in capsys.readouterr().out


# ---------- CI 链接节 ----------


def test_ci_missing_link_fails(tmp_path):
    body = GOOD_BODY.replace("https://github.com/example/openair/actions/runs/123", "-")
    assert _run(tmp_path, body) == 1


def test_ci_na_rejected_for_fix_pr(tmp_path, capsys):
    body = GOOD_BODY.replace(
        "https://github.com/example/openair/actions/runs/123", "N/A（本地验证）"
    )
    assert _run(tmp_path, body, title="fix(api): repair validator") == 1
    assert "fix 类 PR 不允许 N/A" in capsys.readouterr().out


def test_ci_na_accepted_for_feat_pr(tmp_path):
    body = GOOD_BODY.replace(
        "https://github.com/example/openair/actions/runs/123", "N/A（本地验证）"
    )
    assert _run(tmp_path, body, title="feat(api): add validator") == 0


def test_ci_half_na_text_fails(tmp_path):
    """非显式整行 N/A（本地验证）的含糊表述不算数。"""
    body = GOOD_BODY.replace(
        "https://github.com/example/openair/actions/runs/123", "本地跑过，没问题"
    )
    assert _run(tmp_path, body) == 1


def test_ci_comment_residue_without_link_fails(tmp_path, capsys):
    """F1 带病样本：CI 节只剩模板注释（注释自带『（http…）』字样）+ 无真实链接 → 必须失败。"""
    body = GOOD_BODY.replace(
        "https://github.com/example/openair/actions/runs/123",
        "<!-- 填本次 PR 的 CI run 链接（http…）。确无 CI 时整行写：N/A（本地验证） -->\n-",
    )
    assert _run(tmp_path, body) == 1
    out = capsys.readouterr().out
    assert "CI run 链接: 缺少 http 链接" in out  # 证明 http 判定未借注释残留通过


def test_ci_na_only_in_comment_fails(tmp_path):
    """F1：N/A（本地验证）只出现在注释里不算数，必须整行正文。"""
    body = GOOD_BODY.replace(
        "https://github.com/example/openair/actions/runs/123", "<!-- N/A（本地验证） -->"
    )
    assert _run(tmp_path, body) == 1


# ---------- 风险分级 ----------


def test_risk_value_missing_fails(tmp_path):
    body = GOOD_BODY.replace("风险分级: medium", "风险分级: ")
    assert _run(tmp_path, body) == 1


def test_risk_invalid_word_fails(tmp_path):
    body = GOOD_BODY.replace("风险分级: medium", "风险分级: 不确定")
    assert _run(tmp_path, body) == 1


def test_risk_high_ok(tmp_path):
    body = GOOD_BODY.replace("风险分级: medium", "风险分级: high")
    assert _run(tmp_path, body) == 0


def test_risk_uppercase_value_reported_lowercase(tmp_path, capsys):
    """F5：大小写不敏感接受（High），报告统一小写。"""
    body = GOOD_BODY.replace("风险分级: medium", "风险分级: High")
    assert _run(tmp_path, body) == 0
    out = capsys.readouterr().out
    assert "风险分级: high" in out
    assert "风险分级: High" not in out


def test_risk_fallback_whole_text_order_locked(tmp_path):
    """F5：节内无值行时允许全文兜底（先节内后全文的宽松读被复审接受，此处锁定该顺序）。"""
    body = GOOD_BODY.replace("风险分级: medium", "（漏填）")
    body = body.replace("N/A\n\n## 主要修改文件", "N/A\n风险分级: low\n\n## 主要修改文件")
    assert _run(tmp_path, body) == 0


# ---------- 输入缺失 ----------


def test_from_env_missing_body_fails(monkeypatch, capsys):
    monkeypatch.delenv("PR_BODY", raising=False)
    assert vpe.main(["--from-env"]) == 1
    assert "PR_BODY" in capsys.readouterr().out


def test_unreadable_file_fails(tmp_path, capsys):
    assert vpe.main(["--file", str(tmp_path / "nope.md")]) == 1
    assert "FAIL" in capsys.readouterr().out


# ---------- 解析单元 ----------


def test_split_sections_and_aliases():
    sections = vpe.split_sections("前言忽略\n# 改了什么\n内容A\n## 没改什么\n内容B\n")
    assert sections == [("改了什么", "内容A"), ("没改什么", "内容B")]
    assert vpe.find_section(sections, "改了什么") == "内容A"
    assert vpe.find_section(sections, "怎么验证") is None


def test_legacy_section_alias_accepted():
    body = GOOD_BODY.replace("## 没改什么", "## 本次没有改什么")
    results = vpe.validate(body)
    assert not any("必填节缺失: 没改什么" in d for _, d in results)
