# 每日自动同步 Cloudflare Gateway 广告拦截列表

由 GitHub Actions 驱动：每天从可配置的规则源拉取域名，清洗去重后幂等同步到 Cloudflare Zero Trust Gateway，并输出带变更数量的执行报告。全程无需人工干预。

- 工作流文件：`.github/workflows/sync-gateway-lists.yml`
- 配置文件：`config/sources.yml`
- 实现代码：`src/automation/`

---

## 1. 数据流

```
GitHub Actions(schedule / 手动)
  → [config]    读取并校验 config/sources.yml
  → [fetch]     并发拉取各规则源（超时 + 指数退避重试）
  → [normalize] 多格式解析 → 清洗 → 白名单剔除 → 去重 → 排序
  → [guard]     护栏校验（全源失败 / 域名过少 / 删除过多）
  → [diff]      读取云端现有列表作为基线，计算 新增 / 移除 / 总数
  → [sync]      分片写入 Gateway 列表 → 更新 Block Ads 策略 → 清理多余列表
  → [report]    输出 sync-report.json + Job Summary + 90 天留存产物
  → [notify]    失败时创建 / 更新告警 Issue
```

关键点：**任何写操作都发生在读取云端基线之后**，否则「新增/移除」无意义；**多余列表在策略重写之后才删除**，避免删除被策略引用的列表导致 409。

---

## 2. 凭据与安全存储

凭据只通过 **GitHub Secrets → 环境变量** 注入，绝不出现在命令行参数、URL 或日志中。

在仓库 **Settings → Secrets and variables → Actions → New repository secret** 中添加：

| Secret 名称 | 是否必需 | 说明 |
| --- | --- | --- |
| `CLOUDFLARE_ACCOUNT_ID` | 是 | Cloudflare 账号 ID，位于 Zero Trust 控制台右侧 Account ID |
| `CLOUDFLARE_TOKEN` | 是 | API Token；需具备 `Zero Trust: Edit`、`Account Firewall Access Rules: Edit`、`Access: Apps and Policies: Edit` |

安全设计：

- 代码中仅通过 `os.environ` 读取这两个变量（见 `src/automation/pipeline.py` 的 `read_credentials()`），CLI 不提供 `--token` 之类的参数传递方式，避免出现在 `ps` 输出里。
- Cloudflare 端原有日志交互均由 GitHub 自动脱敏。
- 报告 `sync-report.json` 中的账号 ID 只保留后四位（`account_masked = "***1a2b"`）；错误日志仅记录 HTTP 状态码与 Cloudflare `errors[].message`，不打印响应体与令牌。
- 工作流权限最小化：`permissions: contents: read, issues: write`（后者仅用于失败告警）。

---

## 3. 输入参数

### 3.1 手动触发参数（workflow_dispatch）

在仓库 **Actions → Sync Gateway Adblock Lists → Run workflow** 中填入：

| 输入 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `dry_run` | boolean | `false` | 试运行：完整执行抓取与差分，但不对 Cloudflare 做任何写入 |
| `force` | boolean | `false` | 强制执行：忽略「删除比例」护栏（见 6.2） |
| `log_level` | choice | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR`；`DEBUG` 会额外打印新增/移除的域名样例 |

### 3.2 命令行参数（本地或 CI 中均可使用）

等价于手动触发输入，`python -m src.automation --help`：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--config`, `-c` | `config/sources.yml` | 规则源配置文件路径 |
| `--dry-run` | 关闭 | 同上，不写入 Cloudflare |
| `--force` | 关闭 | 同上，忽略删除比例护栏 |
| `--log-level`, `-l` | `INFO`（或取环境变量 `LOG_LEVEL`） | 日志级别 |
| `--report-path` | `sync-report.json` | 结构化报告输出路径 |
| `--domains-path` | 不输出 | 额外输出最终域名清单（每行一个），便于审计 |

退出码：`0` 成功；`1` 失败或被护栏中止；`2` 参数错误。

### 3.3 `config/sources.yml` → `settings`

控制网络行为、Gateway 资源命名与安全护栏。**未知字段会被直接拒绝**，避免拼写错误被静默忽略。

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `list_name_prefix` | `adblock-list` | Gateway 列表名前缀，实际形如 `adblock-list-1` |
| `list_type` | `DOMAIN` | 列表类型 |
| `list_description` | `A blocklist of ad domains` | 列表描述 |
| `policy_name` | `Block Ads` | DNS 防火墙策略名 |
| `chunk_size` | `1000` | 单个列表最多容纳的域名数（Gateway 上限），超出自动分片 |
| `timeout` | `30` | 单次 HTTP 请求超时（秒） |
| `max_retries` | `3` | 失败最大重试次数（对 5xx / 429 / 网络异常生效） |
| `backoff_base` | `1.5` | 指数退避基数：间隔 = `base ** attempt` 秒，上限 30s；若响应含 `Retry-After` 则优先遵循 |
| `max_concurrency` | `5` | 写入 Cloudflare 时的最大并发数 |
| `user_agent` | `cloudflare-gateway-adblocking-sync/1.0 (+...)` | 拉取规则源时的 UA |
| `min_domains_per_source` | `100` | 单个源解析结果低于此值 → 记入 `weak_sources` 并告警（不中止） |
| `fail_safe_min_domains` | `1000` | 最终域名总数低于此值 → 中止，不写入 Gateway |
| `max_domains` | `400000` | 最终域名总数超过此值 → 中止 |
| `max_removal_ratio` | `0.5` | 相对云端基线删除比例超过此比例 → 中止（可用 `--force` 覆盖） |
| `fail_on_source_error` | `false` | `true` 时任一规则源拉取失败即中止（默认容许部分源失败） |

### 3.4 `config/sources.yml` → `sources`（黑名单规则源）

| 字段 | 必需 | 说明 |
| --- | --- | --- |
| `name` | 是 | 唯一标识，出现在日志与报告的 source 列中（不可重复） |
| `url` | 是 | 规则文件直链，仅支持 `http` / `https` |
| `format` | 否（默认 `auto`） | `hosts` / `domains` / `adblock` / `auto` |
| `enabled` | 否（默认 `true`） | `false` 时跳过该源，无需删除配置 |

`format` 取值说明：

| 取值 | 适用形态 | 示例输入 |
| --- | --- | --- |
| `hosts` | IP 前缀 + 域名 | `0.0.0.0 ads.example.com` |
| `domains` | 每行一个裸域名 | `ads.example.com` |
| `adblock` | AdBlock / AdGuard 过滤规则 | `||ads.example.com^$third-party` |
| `auto` | 自动嗅探前三种 | 未知来源时的推荐选项 |

### 3.5 `config/sources.yml` → `whitelist`（白名单）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关，关闭后整段失效 |
| `sources` | `[]` | 远程白名单源，字段与 `sources` 段一致 |
| `inline` | `[]` | 直接写入的域名，大小写与尾点不敏感 |

匹配规则：**精确匹配 + 父域匹配**（白名单含 `example.com` 时，`a.b.example.com` 同样被放行）。

---

## 4. 更新频率（cron）

GitHub Actions 的 `schedule.cron` 不支持引用变量，因此频率直接写在 YAML 里，改一行即可：

```yaml
on:
  schedule:
    - cron: '17 3 * * *'   # 默认：每天 UTC 03:17（避开整点高峰）
```

| 想做的事 | cron |
| --- | --- |
| 每天一次（默认） | `17 3 * * *` |
| 每 12 小时一次 | `17 3,15 * * *` |
| 每周一早上一次 | `17 3 * * 1` |

> **重要**：仓库若连续 **60 天没有任何提交**，GitHub 会自动停用 `schedule` 触发器。若长期无变更，建议偶尔手动运行一次（Actions → Run workflow）以保持激活。

`concurrency.group: gateway-adblock-sync` 与 `cancel-in-progress: false` 保证手动触发与定时任务不会并跑（后者排队等待），避免对 Gateway 列表并发写入。

---

## 5. 预期输出

### 5.1 Cloudflare Gateway 侧

| 产出 | 形态 |
| --- | --- |
| Gateway 列表 | `adblock-list-1` … `adblock-list-N`，类型 `DOMAIN`，每份最多 `chunk_size` 条（默认 1000 条） |
| DNS 防火墙策略 | `Block Ads`，`action=block`，`traffic` 表达式为 `any(dns.domains[*] in $列表ID)` 用 ` or ` 连接 |
| 幂等性 | 已存在的分片保持 **列表 ID 不变** 并原地更新内容；只有超出范围的旧分片才会在策略重写后被删除 |

示例：26 万个域名 → 261 个列表 + 1 条 `Block Ads` 策略。请留意所用套餐对 **列表数量** 与 **列表条目总数** 的配额；若超出，可在 `sources.yml` 中减少启用的规则源。

### 5.2 `sync-report.json`（结构化结果）

| 字段 | 说明 |
| --- | --- |
| `started_at` / `finished_at` / `duration_seconds` | 起止时间与总耗时（UTC，ISO-8601） |
| `trigger` | `schedule` 或 `workflow_dispatch` |
| `status` | `success` / `failure` / `aborted`（`aborted` 表示被护栏拦下，未写入） |
| `failure_reason` | 失败/中止原因 |
| `dry_run` | 是否为试运行 |
| `account_masked` | 脱敏后的账号 ID |
| `sources[]` | 每个源的 `format`/`bytes`/`lines`/`valid`/`unique`/`ok`/`error` |
| `failed_sources` / `weak_sources` | 拉取失败、解析结果偏少的源 |
| `domains_total` / `domains_added` / `domains_removed` / `domains_unchanged` | **本次域名总数与变更数量** |
| `cloud_before` | 同步前云端已生效域名数（差分基线） |
| `dropped_whitelist` / `duplicates_merged` | 白名单剔除数、跨源重复合并数 |
| `lists_created` / `lists_updated` / `lists_deleted` | Gateway 列表写操作计数 |
| `policy_id` / `policy_action` | 策略 ID 与动作（`created` / `updated` / `recreated` / `dry-run`） |
| `errors[]` | `{stage, message, http_status}` |

### 5.3 其他产物

| 产物 | 位置 | 说明 |
| --- | --- | --- |
| Job Summary | Actions 运行页 Summary | 状态、指标表、规则源明细表，无需下载即可查看 |
| 执行产物 | Artifact `gateway-sync-report`（保留 90 天） | `sync-report.json` 与 `sync-domains.txt`（本次最终域名全量清单） |
| 告警 Issue | 失败时自动创建 | 标题 `[Automation] Gateway adblock list sync failed`，标签 `gateway-sync-failure`；重复失败会在同一 Issue 下追加评论而非不断新建 |

---

## 6. 执行日志格式

日志沿用本仓库 loguru 的标准格式，级别右侧额外带 **阶段标签**，便于在长日志里检索：

```
YYYY-MM-DD HH:mm:ss |  LEVEL  | [阶段] 内容
```

阶段标签含义：`[config]` 配置加载、`[fetch]` 源抓取、`[normalize]` 清洗去重、`[guard]` 护栏拦截、`[diff]` 差分统计、`[sync]` 云端写入、`[report]` 报告输出。

### 6.1 一次完整运行的日志样例

```
2026-10-30 20:00:30 |  INFO  | [config] 已加载 config/sources.yml：3 source(s), chunk=1000, timeout=30s, retries=3, whitelist=on(0 remote / 0 inline)
2026-10-30 20:00:33 |  INFO  | [fetch] stevenblack 200 OK, 2.2 MB, 83,555 lines in 0.40s (attempt 1)
2026-10-30 20:00:33 |  INFO  | [fetch] oisd-small 200 OK, 1.3 MB, 59,961 lines in 2.83s (attempt 1)
2026-10-30 20:00:33 |  INFO  | [fetch] adguard-dns 200 OK, 4.3 MB, 187,181 lines in 0.39s (attempt 1)
2026-10-30 20:00:34 |  INFO  | [normalize] stevenblack format=hosts 候选=76749 有效=76733 丢弃(非法=3 保留字=13) 去重后=76733
2026-10-30 20:00:34 |  INFO  | [normalize] oisd-small format=domains 候选=59949 有效=59948 丢弃(非法=1 保留字=0) 去重后=59948
2026-10-30 20:00:35 |  INFO  | [normalize] adguard-dns format=adblock 候选=185171 有效=184677 丢弃(非法=494 保留字=0) 去重后=184665
2026-10-30 20:00:35 |  INFO  | [normalize] 汇总 命中白名单剔除=0 重复合并=60880 最终=260466
2026-10-30 20:00:35 |  INFO  | [sync] 云端基线：261 个列表 / 260,000 个域名
2026-10-30 20:00:35 |  INFO  | [diff] 基线=260,000 本次=260,466 新增=+1185 移除=-1082 无变化=258,815
2026-10-30 20:00:35 |  INFO  | [diff] 本次需写入 261 个列表分片（每片上限 1000 条）
2026-10-30 20:00:40 |  INFO  | [sync] 列表写入完成 新建=1 更新=260 未变化=0 用时=4.2s
2026-10-30 20:00:41 |  INFO  | [sync] 策略 Block Ads 已更新，覆盖 261 个列表
2026-10-30 20:00:41 |  INFO  | [report] 已写入结构化报告 sync-report.json
2026-10-30 20:00:41 |  INFO  | [report] 完成 status=success total=260,466 added=+1,185 removed=-1,082 created=1 updated=260 deleted=0 用时=11.3s
```

> 说明：`[fetch]`、`[normalize]`、`[diff]`、`[report]` 四行为真实试运行抓取输出；`[sync]` 行为格式一致的示例（需要真实凭据才会产生写操作）。

### 6.2 护栏拦截（status=aborted）

```
2026-10-30 20:01:51 | ERROR   | [normalize] unreachable 拉取失败，已跳过：ConnectionError: HTTPSConnectionPool(...)
2026-10-30 20:01:51 | ERROR   | [guard] 全部 1 个规则源均拉取失败，拒绝写入 Cloudflare
2026-10-30 20:01:51 |  INFO   | [report] 完成 status=failure total=0 ...
```

其它典型护栏日志：

```
[guard] 本次仅解析出 412 个域名，低于护栏阈值 fail_safe_min_domains=1000，疑似规则源异常，已中止
[guard] 本次将移除 180,000/260,000 个已生效域名（69.2%），超过护栏阈值 max_removal_ratio=50.0%，已中止。确认无误后可用 --force 强制执行
```

### 6.3 部分源失败（默认不中止）

```
2026-10-30 20:00:31 | ERROR   | [normalize] adguard-dns 拉取失败，已跳过：HTTP 500
2026-10-30 20:00:31 | WARNING | [normalize] oisd-small 仅解析出 12 个域名，低于阈值 100，请检查规则源是否异常
2026-10-30 20:00:36 |  INFO   | [report] 完成 status=success total=76,733 ...
```

此时可用源的结果会被正常写入，失败的源记录在报告的 `failed_sources` / `weak_sources` 中；若希望任一源失败即中止，设 `settings.fail_on_source_error: true`。

---

## 7. 幂等性与失败恢复

| 场景 | 行为 |
| --- | --- |
| 内容完全没变 | 列表写入调用为 0，仅刷新一次策略；报告显示 `unchanged` |
| 某个分片内容变化 | 只更新该分片，`列表 ID 不变`；策略表达式无需变更 |
| 域名总数变少 | 先重写策略，再删除超出范围的旧分片，避免出现「策略引用已删除列表」 |
| 云 API 返回 429/5xx | 按 `Retry-After` 或指数退避重试，超过 `max_retries` 才报错 |
| 策略 `PUT` 被旧环境拒绝 | 自动回退为「删除并重建」策略（`policy_action=recreated`），保证仍能收敛 |
| 任一步骤失败 | 非零退出码 → 工作流失败 → 自动开/更新 Issue；报告与产物照常上传 |

---

## 8. 本地试运行

```bash
pip install requests httpx loguru python-dotenv pyyaml   # 或 pip install .
python -m src.automation --dry-run --log-level DEBUG
```

说明：`--dry-run` 会真实抓取并按完整流程解析、排序，但不读取云端基线也不写入，适合确认规则源配置是否正确。

若要在本地验证写入，先设置凭据再去掉 `--dry-run`：

```bash
export CLOUDFLARE_ACCOUNT_ID=xxxx
export CLOUDFLARE_TOKEN=xxxx
python -m src.automation --domains-path /tmp/domains.txt
```

---

## 9. 故障排查

| 现象 | 可能原因与处理 |
| --- | --- |
| `缺少凭据：请在环境变量中设置 CLOUDFLARE_ACCOUNT_ID 与 CLOUDFLARE_TOKEN` | Secrets 未配置，或名字写错（**不是** `CF_API_KEY`） |
| 单个源 `HTTP 403 / 404` | 源地址变更；到浏览器确认 URL，或在 `sources.yml` 把该源设为 `enabled: false` |
| `[normalize] 某源 有效=0` | `format` 选错：改成 `auto` 让程序自动嗅探；也可开 `DEBUG` 观察候选行 |
| 定时任务没跑 | ① 仓库 60 天无提交被停用；② 仅 `default` 分支的 schedule 生效；③ cron 是 UTC 时间 |
| 频繁出现 `[sync] HTTP 429` | 降低 `max_concurrency`；单次运行 API 调用量约为 `现列表数 + 新列表数 + 少量策略操作` |
| Gateway 报错列表/条目数超限 | 减少启用的规则源，或改用更精简的规则集 |
| 误删了不该删的域名 | 把域名加入 `whitelist.inline`（支持父域匹配），下一轮同步会自动补回 |
| 报告里 `status=aborted` | 护栏生效，Cloudflare **未被写入**；按 `[guard]` 日志处理后必要时加 `--force` |
