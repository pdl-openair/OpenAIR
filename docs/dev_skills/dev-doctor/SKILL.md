---
name: dev-doctor
description: >-
  开发者首次接入项目或环境检测/修复时触发。当用户说"环境检测"、"装项目"、
  "doctor"、"我环境怎么了"、"setup"、"一键安装"、"环境有问题"、
  "跑 setup.bat"时使用本 skill。
  目前仅提供 Windows 版（双击仓库根 setup.bat）。
---

# Dev: Doctor

## When to use

新人首次接入项目，或环境不一致需要修复时。Skill 会一次跑完：检测 `git / Node>=18 / pnpm>=9 / Python>=3.12 / IDE`，齐全后一键完成 `pnpm install` / 后端 venv / `dev_skills` 同步到 `.claude/.cursor/.qoder/.trae`。可重复运行，已就绪项自动跳过。

## Rules source

- [doctor.ps1](doctor.ps1) — Windows 实现；脚本只动仓库内已 ignore 路径，系统级依赖只打印命令、不替用户安装
- 仓库根 [`setup.bat`](../../../setup.bat) — 双击入口（推荐给非开发同事）
- [`docs/03_工程/待专项更新/GETTING_STARTED.md`](../../03_工程/待专项更新/GETTING_STARTED.md) §0 — 开发者版一键体检
- [`docs/dev_skills/GUIDE_FOR_EVERYONE.md`](../GUIDE_FOR_EVERYONE.md) — 非开发者使用速查

## Output

引导用户：**Windows 双击仓库根 `setup.bat`** → 看 4 节体检报告（环境检测 / 项目级修复 / IDE 启动指引 / 启动卡片）→ 缺什么照打印命令复制；环境齐全后输入 Y 完成项目级修复。Mac / Linux 暂未提供脚本，参考 doctor.ps1 步骤手动执行同等操作。
