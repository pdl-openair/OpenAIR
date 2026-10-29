# OpenAIR 测试代码架构

> 状态：已确认  
> 更新时间：2026-09-20  
> 职责：规定 OpenAIR 长期测试代码的仓库归属、目录结构、Suite 组织和代码依赖。

---

## 1. 代码归属

长期测试代码按**受保护结果由谁承担**决定归属：

| 情况 | 长期代码归属 |
| --- | --- |
| 受保护结果由一个 Production 组件独立承担 | `<production component>/tests/` |
| 受保护结果必须由多个 Production 责任共同成立 | `verification/suites/` |
| 最终判断对象是科研语义、Agent / Skill 能力或其他开放性质量 | `evals/` |
| 多个正式测试资产共同使用的测试基础设施 | `testkit/` |
| 只服务当前故障定位或阶段性观察 | `tools/diagnostics/` |

数据库、对象存储、Docker、Provider、真实 runtime、Replay 等运行条件或测试材料不决定代码归属。

测试架构中的 `minimum_owner` 与代码目录不是一一对应关系；代码归属始终根据受保护结果对应的 Production 责任边界确定。

[SPEC-ENG-SPEC-TEST-02] 本条为 SPEC-TEST-02 的正文锚点：测试资产放置守卫（`verification/suites/test_asset_architecture/guard.py`，已在 test 基线）待 tg 合入接线，接线前以 L4 评审项承载（登记见 `spec/enforcement/enforcement-matrix.md`）。

---

## 2. 长期目录结构

```text
OpenAIR/
├─ <production component>/
│  └─ tests/
│     └─ <product responsibility>/
│        ├─ suite.yaml
│        └─ test_*
│
├─ verification/
│  └─ suites/
│     └─ <protected outcome>/
│        ├─ suite.yaml
│        └─ tests/
│
├─ evals/
│  └─ <domain or capability>/
│     └─ ... versioned eval assets ...
│
├─ testkit/
│  └─ ... shared test infrastructure ...
│
└─ tools/
   └─ diagnostics/
      └─ ... temporary diagnostic assets ...
```

`unit`、`integration`、`e2e`、`smoke`、`regression`、`acceptance`、`nightly`、`release`、`live` 等名称可以描述测试层级、运行方式或使用场景，不建立新的长期代码归属类型。

---

## 3. 三类正式测试代码的边界

### 3.1 组件测试

受保护结果能够由一个 Production 组件独立承担时，测试跟随该组件。

即使运行时使用真实数据库、Provider 或其他真实依赖，只要受保护责任仍由该组件独立承担，代码归属仍是组件测试。

### 3.2 Verification

受保护结果必须由多个 Production 责任共同成立时，测试进入 `verification/suites/`。

使用完整部署环境、真实 Provider、数据库或 Docker 本身不会使一项测试成为 Verification；决定因素仍是受保护结果是否跨越多个 Production 责任。

### 3.3 Eval

最终判断对象是科研语义、Agent / Skill 专业能力或其他不能仅靠确定性产品行为断言成立的开放性质量时，正式评测资产进入 `evals/`。

同一个研究场景可以同时具有软件测试和 Eval：确定性产品行为由组件测试或 Verification 证明，开放性科研质量由 Eval 判断。

---

## 4. Suite 组织

组件测试和 Verification 以 **Suite** 作为长期代码单元：

```text
<responsibility or protected outcome>/
├─ suite.yaml
└─ test_*
```

一个 Suite 对应一项稳定的 `test_responsibility`。多个测试文件可以共同证明同一责任；运行配置不同，不因此复制成多套长期 Suite。

`suite.yaml` 的正式字段和资产成立条件由《OpenAIR 测试设计合同》规定。测试代码架构只规定它在代码结构中的位置。

---

## 5. Testkit、Diagnostics 与代码依赖

`testkit/` 保存多个正式测试资产稳定共用的测试基础设施，例如环境准备、Fixture 装载、Fake / Replay 支撑、公共启动器、客户端和证据采集能力。

单个 Suite 专用的辅助代码留在该 Suite 内；跨多个正式资产稳定复用的基础设施进入 `testkit/`。Testkit 不承担产品受保护结果的判定责任。

[SPEC-ENG-SPEC-TEST-02] testkit/ 与 diagnostics/ 的放置边界属同一登记行（共享设施→testkit/，阶段性诊断→tools/diagnostics/，禁散落生产目录与 scripts/ 下新增长期测试）。

`tools/diagnostics/` 保存阶段性诊断、故障定位和一次性探针。诊断资产只有在形成稳定受保护结果、明确测试责任并满足正式测试资产合同后，才进入组件测试、Verification 或 Eval。

长期测试代码保持以下依赖方向：

```text
组件测试       ──→ Production / testkit
Verification  ──→ Production / testkit
Eval          ──→ Production / testkit / versioned eval assets
```

同时保持：

- Production 不依赖测试代码；
- `testkit/` 不依赖某个具体 Suite 的产品断言；
- 一个正式 Suite 不借另一个正式 Suite 的断言建立自身正确性；
- Fixture、Fake、Replay 不成为产品规则来源；
- Diagnostics 不成为正式测试资产的公共依赖中心。
