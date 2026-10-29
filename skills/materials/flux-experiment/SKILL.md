---
name: flux-experiment
description: 调用云实验平台（flux / CrystalFlux）开放 API 创建并启动试验任务。当用户要求"跑一组实验""下发试验任务""创建实验方案任务""启动试验任务""提交晶体生长推荐方案给仿真平台"，或提供 recommend_recipes_str / 方案ID / 方案类型（baseline、optimize、control 等）等实验方案 JSON 时使用本技能。支持把人类可读的方案 JSON 校验后自动序列化为 API 所需的转义字符串，并做量纲与物理一致性检查。
---

# 云实验平台试验任务（flux-experiment）

封装两个开放接口，形成「校验方案 → 创建任务 → 启动任务」的闭环。

| 能力 | 方法 | 路径 |
| --- | --- | --- |
| 创建试验任务 | `POST` | `/open/api/v1/tasks` |
| 启动试验任务 | `POST` | `/open/api/v1/tasks/{taskId}/start` |

Base URL 默认 `http://<flux-base-url>`，鉴权头 `X-Api-Key`。

## 何时用

- 用户给出实验方案（JSON/表格/文字），要求"跑起来""下发到仿真平台" → `create` + `start`
- 只想检查方案写得对不对（温度程序矛盾、比例写错、字段缺失） → `validate`
- 只想确认平台连通 → `probe`

## 不要做的事

- **不要自己拼 `paramValue` 的转义字符串。** 这是本 API 唯一的真实踩坑点：方案要先 `json.dumps` 成字符串再塞进 JSON body，手工转义极易产生非法 JSON。用 `--recipes-file` 传文件，脚本负责序列化。
- **不要在未告知用户的情况下 `start`。** 创建任务会占用平台队列资源；先创建，把 `taskId`/`taskCode` 报给用户，确认后再启动。
- **不要修改 `templateId`**（固定 `2043611007682150400`），也不要改 `paramName`（固定 `recommend_recipes_str`）。`templateId` 默认以字符串下发（该值超出JS 安全整数范围），需切换时用 `--template-id`，理由见 `references/api.md`。

## 用法

统一通过脚本调用（Python 3.8+，仅标准库，无需安装依赖）：

```bash
# 1) 预检连通性 + 鉴权
python scripts/flux_api.py probe

# 2) 校验方案（离线，不发请求）——建议每次都先跑
python scripts/flux_api.py validate --recipes-file recipes.json

# 3) 创建任务（默认只打印请求体，不真的发；确认后加 --execute）
python scripts/flux_api.py create \
  --task-name "2026-10-13 Na助熔剂基线方案" \
  --recipes-file recipes.json --dry-run
python scripts/flux_api.py create \
  --task-name "..." --recipes-file recipes.json --execute

# 4) 启动任务
python scripts/flux_api.py start --task-id 1234567890 --execute

# 5) 查询任务状态（只读，直接发请求）
python scripts/flux_api.py status --task-id 1234567890

# 6) 停止任务（默认 dry-run，确认后加 --execute）
python scripts/flux_api.py stop --task-id 1234567890 --execute

# 7) 回收结果（只读；未完成返回 pending:true，平台侧产出节奏暂不受控）
python scripts/flux_api.py result --task-id 1234567890
```

Windows 控制台默认 GBK，中文输出会乱码：调用时前置 `PYTHONIOENCODING=utf-8`（或统一加 `--json`）。

任何子命令加 `--json` 输出机器可读结果，加 `--json` + `--execute` 可直接被上层 agent 解析。

### recipes.json 长什么样

顶层是**数组**，每个元素是一个"推荐方案对象"，字段与平台约定一致：

```json
[
  {
    "方案ID": "方案_A",
    "方案类型": "baseline",
    "方案给人的一句话说明": "中规中矩方案：基于统计窗口的推荐值。",
    "工艺参数": {
      "原料信息": "Al, In, Se (按化学计量比 1:1:3)",
      "原料标准化": "Al:In:Se=1:1:3",
      "助熔剂信息": "Na 助熔剂",
      "助熔剂标准化": "Na:(Al+In+Se)=2.7:1",
      "容器": "Alumina crucible",
      "籽晶": "Not specified",
      "分离与后处理": {
        "分离方式": "Dissolution in ethanol and water",
        "分离温度_摄氏": 25,
        "晶体的进一步处理": "Washing with ethanol and water, dried at 65°C"
      },
      "温度程序": {
        "是否存在次高温预反应段": "否",
        "低温段保温温度_摄氏": 600,
        "低温段保温时间_h": 0,
        "升温到次高温时间_h": 11.5,
        "次高温段温度_摄氏": 600,
        "次高温段保温时间_h": 2,
        "升温到最高温时间_h": 1,
        "最高温段保温温度_摄氏": 870,
        "最高温段保温时间_h": 24,
        "降温速率_主降温_℃每小时": 1.8,
        "降温时间_主降温_h": 150,
        "冷却速率_至室温_标签": "炉冷"
      }
    },
    "溯源信息": {
      "主要参考配方ID": ["rec_0282", "rec_0322"],
      "参考方案类型ID-名称": ["scheme_001-高助熔剂稀释慢冷方案"],
      "参考材料ID-化学式": ["mat_273-ZnSiP2", "mat_315-ZnSnP2"]
    },
    "预期结果标签": {
      "预期晶体尺寸": "mm 级",
      "预期风险水平": "低",
      "风险来源简述": ["参数接近统计均值，风险可控。"]
    }
  }
]
```

多方案就往数组里多加几个元素（`方案_A`、`方案_B`…），一次任务批量下发。

## 执行流程（推荐给 agent 的顺序）

1. `probe` —— 不通就别往下走，把超时/401/403 如实报给用户，别反复重试。
2. `validate` —— 有 ERROR 先修，有 WARN 念给用户听让他决定。
3. `create --dry-run` —— 把将要下发的任务名和方案条数复述给用户确认。
4. `create --execute` —— 记录返回的 `taskId`、`taskCode`。
5. `start --execute` —— 用户确认后再启动。

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `FLUX_BASE_URL` | `http://<flux-base-url>` | 平台地址 |
| `FLUX_API_KEY` | 见 `config.local.json` | `X-Api-Key` 的值，环境变量优先 |
| `FLUX_TIMEOUT` | `30` | 单请求超时秒数 |

API Key 默认写在同目录 `config.local.json`，该文件已在 `.gitignore` 建议列表中；**不要把 key 抄进 issue、日志或对话摘要**。

## 排错

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 连接超时 / `code=0` | 内网不可达（不在实验网段/VPN 未连） | `probe` 确认后让用户连内网，不要盲目重试 |
| `401` / `403` | key 失效或无权限 | 找平台管理员换 key，不要重试 |
| `code=2000` 但 `data.taskId` 为空 | 平台侧创建成功但未回填 | 记录 `msg`，重试一次；仍失败则人工到平台确认 |
| `推荐方案json` 解析失败 | 手工转义破坏了 JSON | 改用 `--recipes-file`，让脚本序列化 |
| 启动返回 `200` 但 `data` 为空 | 正常，start 无返回体 | 以创建阶段的 `taskId` 为准 |

完整字段契约与已知歧义见 `references/api.md`。