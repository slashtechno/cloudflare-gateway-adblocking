# cloudflare-dns-update — 项目设计文档

> 基于 `cloudflare-gateway-adblocking` 仓库重新设计为更通用、更可维护的 Cloudflare DNS 清单同步引擎。
> 本文档是设计蓝图（design blueprint），用于指导后续开发与维护；代码实现不在本文范围内。

---

## 1. 核心目标与使用场景

### 1.1 一句话定位

`cloudflare-dns-update` 是一个**声明式、幂等、可审计**的同步引擎：把外部「域名清单源」持续对齐到 Cloudflare 上的 DNS 相关目标（v1 聚焦 **Zero Trust Gateway Lists**），并对每次变更给出可读报告与失败告警。

### 1.2 核心目标

1. **幂等可重入**：每天跑都安全，列表 ID 稳定，策略全程在线，绝不因脚本重复执行而堆积重复列表或策略。
2. **配置即代码**：规则源、白名单、目标、护栏全部写在 `sync.yml`，PR 即变更评审。
3. **可观测**：每次运行输出「新增 / 移除 / 总数」与执行报告，失败必有告警。
4. **零人工干预**：内置 GitHub Actions 定时调度 + 手动触发 + 失败自动开 Issue。
5. **可扩展**：新增数据源 / 输出后端 / 通知渠道只需实现约定接口，不改核心。

### 1.3 使用场景

| 场景 | 说明 |
| --- | --- |
| 广告 / 追踪拦截（原项目用途） | 每日从 StevenBlack / oisd / AdGuard 同步到 Gateway，配合 `Block Ads` 策略 |
| 安全威胁情报下发 | SOC 把威胁域名 blocklist 同步成独立的 Gateway 列表，挂到安全策略 |
| 放行名单（allowlist） | 维护「绝不拦截」的合作方域名清单，作为白名单剔除源 |
| 多策略分治 | 同一份配置管理多组列表（广告 / 安全 / 自定义），各自绑定不同策略 |
| GitOps 预览 | 用 `dry-run` / `plan` 在 PR 里展示「本次将变更哪些域名」，合入后再真实写入 |

### 1.4 范围（v1 边界）

- **v1 输出后端**：仅 Cloudflare Zero Trust Gateway Lists（`type=DOMAIN`）。
- 暂不包含：直接管理 Cloudflare DNS 解析记录（A/AAAA/CNAME）、WAF 列表、Firewall Rules。这些作为「未来 sink」预留接口，不做实现。

---

## 2. 现有项目评估：保留什么、重构什么

### 2.1 必须沿用的硬约束（Cloudflare API 约定）

这些是 Cloudflare 的既有契约，新项目**不能另起炉灶**：

- 端点前缀：`https://api.cloudflare.com/client/v4/accounts/{account_id}/gateway/lists` 与 `.../gateway/rules`。
- 鉴权：`Authorization: Bearer {token}` + `Content-Type: application/json`。
- 列表：`type="DOMAIN"`、`items=[{"value": x}]`、识别前缀 `adblock-list`（可配置）。
- 策略：`name="Block Ads"`、`action="block"`、`traffic` 为多片以 `" or "` 连接的 `any(dns.domains[*] in ${list_id})`、单片上限 1000。
- `GET /gateway/lists` 返回列表元信息但**不含 items**，需对每个列表再 `GET /gateway/lists/{id}/items`（带 `result_info.cursors.after` 分页）。

### 2.2 最值得保留的 5 个设计点（来自 `src/automation/`）

1. **幂等同步**（`gateway.py`）：先读云端基线 → 复用旧列表 ID 原地 `PUT` → 仅超出的旧分片才删除 → 策略最后 upsert（PUT 失败时降级为删除重建）。这是原 `src/utils/` 完全没有的。
2. **多格式归一化**（`normalizer.py`）：hosts / 纯域名 / adblock 三类解析、清洗、校验、去重、**排序输出**（排序是跨 Run 差分稳定的前提）。
3. **严格配置校验**（`config.py`）：未知字段、非法 URL、阈值矛盾都报错并指出字段路径；禁用源在加载期即剔除。
4. **结构化报告**（`reporter.py`）：`sync-report.json` + GitHub Step Summary + Markdown，账号只留后四位，错误只记 HTTP 状态与 Cloudflare `errors[].message`。
5. **护栏**（`pipeline.py`）：全源失败 / 域名数过低 / 删除比例过高均中止，避免误清空线上策略。

### 2.3 最该重构的 3 个痛点（来自 `src/utils/` + `src/__main__.py`）

1. **非幂等上传**（`upload.py:51-77`）：每次只 `POST` 新建 `adblock-list-*`，从不查重也不删旧列表；`create_dns_policy`（`upload.py:80-104`）每次新建一条策略 → 每天堆积。
2. **错误被 print 吞掉、且不抛异常、部分 `exit(1)`**（`upload.py:74-77`、`delete.py:19-22`）：失败不可被编排层感知，CI 可能误判成功。
3. **密钥可经命令行入参泄露**（`__main__.py:50-54` 的 `--token/-t`，`95-96` 写入全局 `TOKEN`）：`ps` 可见，且用模块级全局可变状态，无法并发多账号。

### 2.4 新项目相对原项目的重新设计

| 维度 | 原项目 | 新项目 `cloudflare-dns-update` |
| --- | --- | --- |
| 包布局 | `src/`（顶层混乱）+ `src/utils/` + `src/automation/` 并存 | 单一清晰包 `src/cloudflare_dns_update/`，脚手架与核心分离 |
| 列表 / 策略 | 硬编码 `adblock-list-*` + `Block Ads` 单组 | **targets（目标组）**：一组可配多个列表 + 可选策略，支持多组并存 |
| 数据源 | `convert_to_list` 正则只认 hosts | **Provider 抽象**：hosts/domains/adblock/raw，格式自动嗅探 |
| 输出后端 | 仅 Cloudflare | **Sink 抽象**：Gateway（主）+ File（审计/调试），未来 DNS 记录 |
| 通知 | 仅 GitHub Issue（硬编码于 workflow） | **Notifier 抽象**：Issue + Webhook（可选），配置驱动 |
| HTTP 客户端 | 混用 `requests` + `httpx` | 统一 `httpx`（读+写全异步），少一个依赖 |
| 观测 | loguru 单行 + 阶段标签 | 同左 + 可选 JSON 日志（CI 友好）+ `plan` 预览命令 |
| 测试 | 无 | pytest：`normalizer`/`config`/`diff` 单测 + `GatewaySink` MockTransport 集成测 |

---

## 3. 整体设计思路

### 3.1 设计哲学

- **声明式优先**：一切以 `sync.yml` 为准，代码只负责把声明落到 Cloudflare 并回报差异。
- **读 → 算 → 写**三段式：任何写操作之前必须完成「读云端基线 + 计算 diff」，否则 added/removed 无意义；写阶段失败不会影响已读取的基线。
- **失败必须可见**：异常一律向上传导到编排层，转成非零退出码 + 报告 + 告警；绝不用 `print` 静默吞掉。
- **最小权限与最小暴露**：凭据只走环境变量 / GitHub Secrets；报告只脱敏记录。
- **可重放**：相同输入 + 相同云端状态 → 相同结果（确定性排序、确定性分片）。

### 3.2 高层数据流

```
sync.yml
  └─> config loader（校验）
        └─> 对每个 target:
              sources ──(Provider 并发抓取+归一化)──> domain set
              whitelist ──> 剔除（精确 + 父域匹配）
              guard（最低条数 / 删除比例 / 全源失败）
              baseline（GatewaySink.read_remote）→ diff（added/removed/total）
              dry-run? ── yes ──> 仅报告
                        └─ no ──> Sink.apply（原地更新/新建/删旧）+ 策略 upsert
        └─> reporter（JSON + Summary + 产物）
        └─> notifier（失败时）
```

---

## 4. 目录结构

```
cloudflare-dns-update/
├── pyproject.toml                 # 依赖、入口、ruff/black 配置
├── README.md                      # 项目说明 + 快速开始
├── CONTRIBUTING.md                # 贡献指南
├── LICENSE                        # MIT（沿用）
├── .env.example                   # 凭据变量示例
├── config/
│   └── sync.yml                   # 示例配置（取代原 sources.yml）
├── src/cloudflare_dns_update/
│   ├── __init__.py
│   ├── __main__.py                # python -m cloudflare_dns_update 入口
│   ├── cli.py                     # argparse 子命令（sync / plan / validate-config / list-remote）
│   ├── config/
│   │   ├── __init__.py
│   │   ├── model.py               # Settings / Source / Whitelist / Target 数据类
│   │   └── loader.py              # load + 严格校验 + 字段路径报错
│   ├── core/
│   │   ├── fetcher.py             # httpx 并发抓取：超时 + 退避重试 + Retry-After
│   │   ├── normalizer.py          # 多格式解析 / 清洗 / 校验 / 去重 / 排序
│   │   ├── whitelist.py           # 白名单解析（精确 + 父域匹配）
│   │   └── diff.py                # 基线 diff：added / removed / unchanged / total
│   ├── providers/
│   │   ├── base.py                # SourceProvider 协议（fetch -> list[str]）
│   │   ├── hosts.py               # 0.0.0.0 / 127.0.0.1 / ::1 前缀
│   │   ├── domains.py             # 每行一个裸域名
│   │   ├── adblock.py             # ||domain^ / ||*.domain^ / URL 形式
│   │   └── registry.py            # 格式嗅探 + 工厂
│   ├── sinks/
│   │   ├── base.py                # Sink 协议（read_remote / apply）
│   │   ├── gateway.py             # Cloudflare Gateway 客户端（幂等核心）
│   │   └── file.py                # 本地文件 sink（审计 / 调试）
│   ├── notifiers/
│   │   ├── base.py                # Notifier 协议
│   │   ├── github_issue.py        # 失败开/更新 Issue
│   │   └── webhook.py             # Slack/Discord 通用 webhook（可选）
│   ├── pipeline.py                # 编排：遍历 targets、串起上面所有阶段
│   └── reporting/
│       ├── report.py              # SyncReport 数据类（唯一结构化契约）
│       └── render.py              # JSON / Markdown / StepSummary 渲染
├── tests/
│   ├── test_normalizer.py
│   ├── test_config.py
│   ├── test_diff.py
│   ├── test_providers.py
│   └── test_gateway_mock.py       # httpx MockTransport 模拟 Cloudflare
└── .github/workflows/
    └── sync.yml                   # schedule + dispatch + 失败通知
```

---

## 5. 模块划分与职责

| 模块 | 职责 | 关键接口 |
| --- | --- | --- |
| `config/model.py` | 配置数据结构：`Settings`、`Source`、`Whitelist`、`Target` | dataclass |
| `config/loader.py` | 读取 YAML、校验、禁用源过滤、字段路径报错 | `load_config(path) -> SyncConfig` |
| `core/fetcher.py` | 并发拉取规则源，超时 + 指数退避 + `Retry-After`，失败返回结构化结果不中断 | `fetch_sources(sources, settings) -> list[FetchResult]` |
| `core/normalizer.py` | 三位一体解析（hosts/domains/adblock）、清洗、域名合法性、RFC6761 保留名剔除、全域去重、**排序** | `normalize(fetches, settings, whitelist) -> NormalizeResult` |
| `core/whitelist.py` | 解析白名单源 + 内联；精确 + 父域匹配剔除 | `collect_whitelist(...)` |
| `core/diff.py` | 以云端基线对比本次集合 | `diff_domains(current, baseline) -> DiffResult` |
| `providers/*` | 把「一个源」变成「域名集合」，屏蔽格式差异 | `class SourceProvider: async fetch() -> list[str]` |
| `sinks/base.py` | 输出后端抽象：`read_remote()` 取基线、`apply(domains)` 落盘 | `class Sink` |
| `sinks/gateway.py` | Cloudflare Gateway 幂等客户端：分片原地更新、删旧、策略 upsert | `GatewaySink(account_id, token, settings)` |
| `sinks/file.py` | 把最终结果写成本地文件，供审计 / 调试 | `FileSink(path)` |
| `notifiers/*` | 失败通知渠道 | `class Notifier: async notify(report)` |
| `pipeline.py` | 编排：遍历 targets → fetch → normalize → whitelist → guard → baseline → diff → (写) → 报告 → 通知；统一异常分类 | `run(config, dry_run, ...) -> SyncReport` |
| `reporting/report.py` | 唯一对外结构化契约，含时间戳 / 触发 / 各 target 明细 / diff / 写计数 / 错误 | `SyncReport` |
| `reporting/render.py` | 渲染 JSON / Markdown / `$GITHUB_STEP_SUMMARY` | `to_json / to_markdown` |
| `cli.py` | 子命令解析，从环境变量读凭据（**绝不**经 argv），委派 `pipeline` | `sync / plan / validate-config / list-remote` |

---

## 6. 关键设计决策（含理由）

1. **targets 取代硬编码单组**（决策）：原项目把 `adblock-list-*` 和 `Block Ads` 写死在 `upload.py`。新项目用 `targets: [{name, list_prefix, policy_name?, sources, whitelist, ...}]`，使「广告拦截 / 安全情报 / 放行名单」各成一组、互不干扰。**理由**：同一账号常需多套列表+多策略，单组无法表达。
2. **Provider / Sink / Notifier 三套插件接口**（决策）：核心编排与具体 IO 解耦。**理由**：加新数据源（如 DNSMASQ 格式）、新基建（如 DNS 记录）、新通知（如邮件）都不碰 `pipeline.py`。
3. **统一 httpx，弃用 requests**（决策）：读、写、通知全部异步走 `httpx` + `asyncio.Semaphore` 限并发。**理由**：少一个依赖；`upload.py`/`delete.py` 混用两套客户端是历史包袱。
4. **凭据只走环境变量 / Secrets**（决策）：CLI 不提供 `--token`。**理由**：原 `__main__.py:50-54` 把 token 当命令行参数，存在进程列表泄露风险；这是安全红线。
5. **写前必读基线**（决策）：`baseline` 在 `apply` 之前完成。**理由**：added/removed 才有意义；且保证「重复运行零变化 → 零写」。
6. **保留列表 ID 而非删后重建**（决策）：沿用 `gateway.py` 的「复用 ID 原地 PUT」。**理由**：策略 `traffic` 表达式无需改动，DNS 拦截全程在线，无空窗；删除旧分片放在策略重写之后，避免 409。
7. **确定性排序输出**（决策）：归一化后 `sorted` 输出。**理由**：原 `utils.convert_to_list` 用 `list(set(...))`，顺序受 `PYTHONHASHSEED` 随机，跨 Run 差分会抖动。
8. **护栏前置 + 可 `--force`**（决策）：全源失败 / 低于 `fail_safe_min_domains` / 删除比例超 `max_removal_ratio` 即中止；`--force` 仅覆盖删除比例。**理由**：规则源异常时绝不误清空线上策略。
9. **报告是唯一契约**（决策）：Job Summary、产物、告警 Issue 全部由 `SyncReport` 渲染。**理由**：单一数据源，新增字段不必改多处。
10. **dry-run 与 plan 双通道**（决策）：`dry-run` 跳过所有写入（包括基线读取可选）；`plan` 读取基线但只展示 diff 不写。**理由**：PR 评审需要「预览本次变更」而无副作用。

---

## 7. 功能特性清单

**数据源**
- 多源并发拉取；支持 `hosts` / `domains` / `adblock` / `auto`（嗅探）。
- 单源失败默认不中断整体（`fail_on_source_error: false` 时），但记入报告；可配置为「任一失败即中止」。
- 弱源告警：单源解析低于 `min_domains_per_source` 计入 `weak_sources`。

**清洗与转换**
- 剥离注释 / IP 前缀 / `||` `^` `*.` / 引号；小写化、去尾点。
- 域名标签合法性校验；剔除 RFC6761/6762 保留名（`.local`/`.invalid`/`.onion` 等）。
- 白名单：内联 + 远程源，精确匹配 + 父域匹配（白名单含 `example.com` 则放行 `a.b.example.com`）。
- 全域去重后排序。

**同步与策略**
- 分片写入（默认 1000/片，可配 `chunk_size`）。
- 原地更新保持列表 ID 稳定；超出旧分片后置删除。
- `Block Ads`（或 target 指定策略名）幂等 upsert；PUT 失败自动降级重建。
- 多 target 支持。

**报告与可观测**
- `sync-report.json`（结构化）、Job Summary（Markdown）、本地域名清单产物（审计）。
- 分阶段日志标签 `[config] [fetch] [normalize] [diff] [sync] [report]`。
- 可选 JSON 日志（CI 机器解析）。

**调度与告警**
- GitHub Actions：`schedule` cron（默认 UTC 03:17）+ `workflow_dispatch`。
- 失败自动开/更新告警 Issue（标签 `gateway-sync-failure`）；可选 Webhook。

**可靠性**
- 请求超时、指数退避重试、遵循 `Retry-After`、并发限流。
- `concurrency` 防手动与定时并跑；`timeout-minutes` 兜底。
- 失败非零退出码 → CI 标记失败 → 触发通知。

---

## 8. 配置方式（`config/sync.yml`）

```yaml
settings:
  # 网络与重试
  timeout: 30
  max_retries: 3
  backoff_base: 1.5
  max_concurrency: 5
  user_agent: "cloudflare-dns-update/1.0"
  # 分片与命名
  chunk_size: 1000
  list_type: DOMAIN
  list_description: "Managed domain list"
  # 护栏
  min_domains_per_source: 100      # 单源低于此值 → 告警（不中止）
  fail_safe_min_domains: 1000      # 总数低于此值 → 中止
  max_domains: 400000              # 总数超过此值 → 中止
  max_removal_ratio: 0.5           # 相对基线删除超此比例 → 中止
  fail_on_source_error: false      # true 时任一源失败即中止

# 全局白名单：对所有 target 生效
whitelist:
  enabled: true
  sources: []                      # 远程白名单源，字段同 sources
  inline:
    - example.com
    - partner.cdn.net

# 一个或多个目标组
targets:
  - name: adblock
    list_prefix: adblock-list
    policy_name: Block Ads         # 留空则不管理策略
    sources:
      - name: stevenblack
        url: https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts
        format: hosts
        enabled: true
      - name: oisd-small
        url: https://hosts.core.oisd.nl/light.txt
        format: domains
      - name: adguard-dns
        url: https://adguardteam.github.io/AdGuardSDNSFilter/Filters/filter.txt
        format: adblock
    whitelist:
      enabled: true
      inline: [telemetry.example.com]

  - name: security-block
    list_prefix: sec-block
    policy_name: Block Threat Intel
    sources:
      - name: threatfeed
        url: https://example.invalid/threat domains.txt
        format: domains
```

字段校验要点（沿用 `config/loader.py` 的严格策略）：未知顶层键、未知 `settings` 键、非法 `format`、非法 URL scheme、所有源禁用、阈值自相矛盾（如 `min > max`、ratio 不在 `(0,1]`）、`name` 重复 → 均报 `ConfigError` 并指出字段路径。

---

## 9. 运行环境与依赖

### 9.1 运行环境

- **Python**：`>=3.10,<4.0`（建议 `3.11+`；CI 用 `3.12`）。
- **操作系统**：Linux / macOS / Windows（GitHub Actions 用 `ubuntu-latest`）。
- **网络**：出网访问规则源与 `api.cloudflare.com`；建议工作流设 `timeout-minutes: 20`。

### 9.2 依赖

| 依赖 | 版本 | 用途 |
| --- | --- | --- |
| `httpx` | `^0.24` | 统一异步 HTTP（抓取 + 写入 + 通知） |
| `loguru` | `^0.7` | 日志 |
| `pyyaml` | `^6.0` | 解析 `sync.yml` |
| `python-dotenv` | `^1.0` | 本地调试加载 `.env` |

开发依赖：`pytest`、`ruff`、`black`。

> 不再需要 `requests`（原 `src/utils/` 遗留）。

### 9.3 凭据（Secrets）

仅通过环境变量注入，命名沿用既有约定：

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_TOKEN`（需 `Zero Trust: Edit`、`Account Firewall Access Rules: Edit`、`Access: Apps and Policies: Edit`）

GitHub 仓库：`Settings → Secrets and variables → Actions` 添加上述两个 Secrets；工作流以 `env:` 注入，绝不出现在命令行。

---

## 10. CLI 设计

```
python -m cloudflare_dns_update [global] <subcommand> [options]

全局:
  -c, --config PATH     配置路径（默认 config/sync.yml）
  -l, --log-level LEVEL INFO/DEBUG/WARNING/ERROR
  --dry-run             解析与差分照常执行，但不写入任何后端

子命令:
  sync               默认；完整同步（受 dry-run 影响）
  plan               读取云端基线并展示 diff，不写入（PR 预览用）
  validate-config    仅校验 sync.yml，合法即退出 0
  list-remote        打印当前 Cloudflare 上本账号的 Gateway 列表（审计）

通用:
  --report-path PATH        sync-report.json 输出路径
  --domains-path PATH       额外输出最终域名清单（审计）
  --force                   忽略 max_removal_ratio 护栏
```

退出码：`0` 成功 / `1` 失败或被护栏中止 / `2` 配置或参数错误。

---

## 11. GitHub Actions / 自动化

`.github/workflows/sync.yml`：

- `on.schedule.cron: '17 3 * * *'`（改一行调频率；注释标明；提示 60 天无提交会停用 schedule）。
- `on.workflow_dispatch.inputs`：`dry_run`、`force`、`log_level`。
- `permissions: contents: read, issues: write`。
- `concurrency.group: cloudflare-dns-update`（防并跑）。
- 步骤：`checkout@v4` → `setup-python@v5`（pip 缓存）→ `pip install .` → `python -m cloudflare_dns_update sync`（env 注入 Secrets）→ 始终步骤上传 `sync-report.json` + 域名清单（保留 90 天）。
- 独立 `notify` 作业：仅 `failure/aborted` 时，用 `actions/github-script@v7` 按固定标题检索同名 open Issue，存在则追加评论、不存在则新建并打 `gateway-sync-failure` 标签。
- 可选：若配置了 `NOTIFY_WEBHOOK_URL` Secret，额外调用 `webhook` notifier。

---

## 12. 测试策略

- `tests/test_normalizer.py`：各格式解析、URL 形式、保留名剔除、排序稳定性。
- `tests/test_config.py`：字段校验拦截（未知键 / 非法 URL / 阈值矛盾 / 重复 name）。
- `tests/test_diff.py`：added/removed/unchanged/total 与父域白名单。
- `tests/test_providers.py`：hosts/domains/adblock provider 与嗅探工厂。
- `tests/test_gateway_mock.py`：用 `httpx.MockTransport` 模拟 Cloudflare，验证
  - 首次运行建列表 + 建策略；
  - 内容不变再跑零写入、ID 不变；
  - 一片变化 + 一片多余 → 更新 + 删除，策略先重写后删旧（无 409）；
  - 策略 PUT 失败 → 降级重建；
  - 403 → 抛 `GatewayError`。

CI 中 `pytest` + `ruff` 作为 PR 门禁。

---

## 13. 文档规划

### 13.1 README.md（项目说明 + 使用方法）

- 项目简介与一句话定位。
- 快速开始：添加 Secrets → 复制 `config/sync.yml` 并按需增删源 → 本地 `validate-config` → GitHub Actions 手动触发一次。
- 配置参考（指向 `config/sync.yml` 示例 + 字段表）。
- 架构概览图（指向本文档）。
- 调整频率 / 规则源的方法。
- 故障排查速查表。

### 13.2 CONTRIBUTING.md（贡献指南）

- 开发环境：`uv venv && uv pip install -e ".[dev]"`（或 `pip install -e .` + `pip install pytest ruff black`）。
- 代码风格：`ruff` + `black`；提交前 `pytest`。
- **如何新增一个 Provider**：继承 `SourceProvider`，实现 `async fetch() -> list[str]`，在 `registry.py` 注册。
- **如何新增一个 Sink**：继承 `Sink`，实现 `read_remote()` / `apply(domains)`，在 `pipeline` 注入。
- **如何新增一个 Notifier**：继承 `Notifier`，实现 `async notify(report)`。
- PR 清单：类型注解、docstring、单测、不泄露凭据、不引入循环依赖。
- 议题与分支约定。

---

## 14. 从现有项目迁移的注意事项

1. **不沿用 `src/utils/`**：`upload.py`/`delete.py`/`utils.py` 的非幂等与 print 错误处理是技术债，新项目用 `sinks/gateway.py` 替掉。
2. **配置改名**：`sources.yml` → `sync.yml`，并引入 `targets`；旧 `settings/sources/whitelist` 三段可直接平移，但需包进 `targets[].sources`。
3. **保留 `src/__main__.py` 与 `src/utils/`**：作为原 `cloudflare-gateway-adblocking` 的兼容 CLI 暂不删除（保持发布版本行为不变），新能力全部在新包；迁移完成后可择机废弃旧 CLI。
4. **凭据命名不变**：`CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_TOKEN` 沿用，避免用户重新配置 Secrets。
5. **报告兼容**：`sync-report.json` 字段尽量保留原有键（`domains_total`/`domains_added`/`domains_removed` 等），便于既有告警 Issue 与产物解析脚本继续工作。

---

## 15. 里程碑建议（落地顺序）

1. **M1 脚手架**：`pyproject.toml` + 包骨架 + `config/model.py` + `config/loader.py` + `validate-config` 命令。
2. **M2 核心同步**：`core/*` + `providers/*` + `sinks/gateway.py` + `pipeline.py` + `reporting/*`；用 MockTransport 测通幂等。
3. **M3 CLI 与 dry-run/plan**：`cli.py` + `list-remote` + 文件 sink 审计。
4. **M4 自动化**：`sync.yml` 工作流 + 失败 Issue 通知 + 产物留存。
5. **M5 通知与扩展**：`webhook` notifier + 多 target 真实验证 + 文档（README / CONTRIBUTING）。
6. **M6 测试与发布**：补齐单测、`ruff`/`black`、PyPI 发布（沿用 `python-publish.yml`）。

---

_设计文档结束。下一步可由 `M1` 开始实现脚手架。_
