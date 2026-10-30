#!/usr/bin/env node
/**
 * Claude Code CLI 分块安装器（npmmirror / CN 网络专用）。
 *
 * 为什么不用 `npm install -g`：平台原生子包 tarball（~130MB）在本网络路径上
 * 传输中途必然 ECONNRESET；npm 对 optional dependency 的下载失败静默跳过，
 * 留下不可用的 `claude` 占位桩（exit: "claude native binary not installed"）。
 * 小体积 HTTP Range 请求可稳定通过，因此按 4MB 分块下载 + 逐块重试，最后用
 * registry manifest 的 dist.shasum 校验完整性，再按 npm 的目录布局解开两个
 * 包并执行官方 install.cjs（把原生二进制拷到 bin/claude.exe 上）。
 *
 * 环境变量：
 *   NPM_REGISTRY         默认 https://registry.npmmirror.com
 *   CLAUDE_CODE_VERSION  默认 latest（固定版本传入如 2.1.312）
 *   INSTALL_PREFIX       默认 /usr/local
 */
import { createHash } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'

const REGISTRY = process.env.NPM_REGISTRY || 'https://registry.npmmirror.com'
const VERSION = process.env.CLAUDE_CODE_VERSION || 'latest'
const PREFIX = process.env.INSTALL_PREFIX || '/usr/local'
const CHUNK = 4 * 1024 * 1024
const TRIES = 8

function die(msg) {
  console.error(`install_claude_cli: ${msg}`)
  process.exit(1)
}

async function getJSON(url) {
  const r = await fetch(url)
  if (!r.ok) die(`manifest 请求失败 ${r.status}: ${url}`)
  return r.json()
}

/** musl 检测与官方 install.cjs 保持一致（glibc 缺失 glibcVersionRuntime）。 */
function platformPkgName() {
  const key =
    `${process.platform}-${os.arch()}` +
    (process.platform === 'linux' &&
    process.report?.getReport?.().header?.glibcVersionRuntime === undefined
      ? '-musl'
      : '')
  const map = {
    'darwin-arm64': 'darwin-arm64',
    'darwin-x64': 'darwin-x64',
    'linux-x64': 'linux-x64',
    'linux-arm64': 'linux-arm64',
    'linux-x64-musl': 'linux-x64-musl',
    'linux-arm64-musl': 'linux-arm64-musl',
  }
  const suffix = map[key]
  if (!suffix) die(`不支持的平台: ${key}`)
  return `@anthropic-ai/claude-code-${suffix}`
}

/** 分块下载；每块独立重试（指数退避），整体用 manifest shasum (sha1) 校验。 */
async function chunkedDownload(url, shasum) {
  const probe = await fetch(url, { headers: { Range: 'bytes=0-0' } })
  if (probe.status !== 206) die(`服务器不支持 Range（status ${probe.status}）: ${url}`)
  const contentRange = probe.headers.get('content-range') || ''
  const total = Number((contentRange.match(/\/(\d+)$/) || [])[1])
  if (!total) die(`无法解析文件大小: ${contentRange}`)

  const parts = []
  for (let start = 0; start < total; start += CHUNK) {
    const end = Math.min(start + CHUNK, total) - 1
    for (let attempt = 1; ; attempt++) {
      try {
        const r = await fetch(url, { headers: { Range: `bytes=${start}-${end}` } })
        if (r.status !== 206) throw new Error(`status ${r.status}`)
        const buf = Buffer.from(await r.arrayBuffer())
        if (buf.length !== end - start + 1) throw new Error(`short read ${buf.length}`)
        parts.push(buf)
        process.stdout.write(`\r下载 ${end + 1}/${total} bytes`)
        break
      } catch (err) {
        if (attempt >= TRIES) die(`分块 ${start}-${end} 重试 ${TRIES} 次仍失败: ${err.message}`)
        await new Promise((r) => setTimeout(r, 1000 * attempt))
      }
    }
  }
  process.stdout.write('\n')

  const full = Buffer.concat(parts)
  const actual = createHash('sha1').update(full).digest('hex')
  if (actual !== shasum) die(`shasum 不匹配: 期望 ${shasum}，实际 ${actual}`)
  return full
}

function extractTo(tarball, destDir) {
  fs.mkdirSync(destDir, { recursive: true })
  const tarFile = `${destDir}.tgz`
  fs.writeFileSync(tarFile, tarball)
  const res = spawnSync('tar', ['xzf', tarFile, '-C', destDir])
  if (res.status !== 0) die(`tar 解压失败: ${res.stderr}`)
  fs.unlinkSync(tarFile)
}

async function main() {
  const mainName = '@anthropic-ai/claude-code'
  const enc = encodeURIComponent
  const mainManifest = await getJSON(`${REGISTRY}/${enc(mainName)}/${VERSION}`)
  const platName = platformPkgName()
  const platVersion = (mainManifest.optionalDependencies || {})[platName]
  if (!platVersion) die(`${mainName}@${mainManifest.version} 未声明 ${platName}`)
  const platManifest = await getJSON(`${REGISTRY}/${enc(platName)}/${platVersion}`)
  console.log(`安装 ${mainName}@${mainManifest.version} + ${platName}@${platVersion}`)

  const mainTgz = await chunkedDownload(
    mainManifest.dist.tarball,
    mainManifest.dist.shasum,
  )
  const platTgz = await chunkedDownload(
    platManifest.dist.tarball,
    platManifest.dist.shasum,
  )

  // 复刻 npm 全局安装布局：<prefix>/lib/node_modules/<name>/...
  // 注意 platName 自带 @scope 前缀，不能再拼 scope 目录。
  const mainDir = path.join(PREFIX, 'lib', 'node_modules', '@anthropic-ai', 'claude-code')
  const platDir = path.join(mainDir, 'node_modules', platName)
  const staging = fs.mkdtempSync('/tmp/claude-cli-')
  extractTo(mainTgz, `${staging}/main`)
  extractTo(platTgz, `${staging}/plat`)
  fs.rmSync(mainDir, { recursive: true, force: true })
  // 顺序：主包目标的父目录要先建（rename 不建父目录）；平台包父目录在主包
  // 落位之后建，避免 mkdir -p 连带创建 mainDir 导致主包 rename EEXIST。
  fs.mkdirSync(path.dirname(mainDir), { recursive: true })
  fs.renameSync(`${staging}/main/package`, mainDir)
  fs.mkdirSync(path.dirname(platDir), { recursive: true })
  fs.renameSync(`${staging}/plat/package`, platDir)

  // 官方 postinstall：把平台原生二进制拷到 bin/claude.exe 占位符上。
  // 注意它缺平台包时也 exit 0（只打印提示），靠占位符大小兜底检测。
  const post = spawnSync('node', ['install.cjs'], { cwd: mainDir })
  if (post.status !== 0) die(`install.cjs 失败: ${post.stderr}`)

  // 放真实文件（非符号链接）到 bin，避免跨构建阶段 COPY symlink 的歧义。
  const binSrc = path.join(mainDir, 'bin', 'claude.exe')
  if (!fs.existsSync(binSrc) || fs.statSync(binSrc).size < 1024 * 1024) {
    die(`bin/claude.exe 仍是占位桩（install.cjs 未找到平台包），布局: ${platDir}`)
  }
  const binDst = path.join(PREFIX, 'bin', 'claude')
  fs.mkdirSync(path.dirname(binDst), { recursive: true })
  fs.copyFileSync(binSrc, binDst)
  fs.chmodSync(binDst, 0o755)

  const check = spawnSync(binDst, ['--version'], { encoding: 'utf8' })
  if (check.status !== 0) die(`claude --version 失败: ${check.stderr}`)
  console.log(`claude CLI 就绪: ${check.stdout.trim()}`)
}

main().catch((e) => die(e.stack || String(e)))
