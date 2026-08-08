# animeko-loon

从 [Animeko](https://github.com/open-ani/animeko) 数据源 JSON 自动提取域名，生成 **Loon 插件** 与 **规则列表**。

## 安装

### 方式 A：插件（推荐，策略已写进规则）

Loon → 配置 → 插件 → 安装：

| 策略 | Raw URL |
|---|---|
| 直连 `DIRECT` | `https://raw.githubusercontent.com/beckyeeky/animeko-loon/main/dist/Animeko-DIRECT.plugin` |
| 代理 `PROXY`（安装后绑定策略组） | `https://raw.githubusercontent.com/beckyeeky/animeko-loon/main/dist/Animeko-PROXY.plugin` |
| 拒绝 `REJECT` | `https://raw.githubusercontent.com/beckyeeky/animeko-loon/main/dist/Animeko-REJECT.plugin` |

一键导入（DIRECT）：

```text
loon://import?plugin=https%3A%2F%2Fraw.githubusercontent.com%2Fbeckyeeky%2Fanimeko-loon%2Fmain%2Fdist%2FAnimeko-DIRECT.plugin
```

### 方式 B：规则订阅（策略在订阅时选择）

```text
https://raw.githubusercontent.com/beckyeeky/animeko-loon/main/dist/Animeko.list
```

Loon → 配置 → 规则 → 订阅规则 → 添加上述 URL，策略选 `DIRECT` 或你的代理组。

jsDelivr 备用：

```text
https://cdn.jsdelivr.net/gh/beckyeeky/animeko-loon@main/dist/Animeko-DIRECT.plugin
https://cdn.jsdelivr.net/gh/beckyeeky/animeko-loon@main/dist/Animeko.list
```

## 默认数据源

配置见 [`config/sources.json`](./config/sources.json)：

| id | URL |
|---|---|
| online | `https://raw.githubusercontent.com/MajoSissi/animeko-source/main/dist/online.json` |
| bt1 | `https://sub.creamycake.org/v1/bt1.json` |
| css1 | `https://sub.creamycake.org/v1/css1.json` |

另含 Animeko 官方与核心依赖域名：`animeko.org`、`myani.org`、`bgm.tv`、`dandanplay.net`、`mikanani.me` 等，以及源列表托管域名（GitHub / Gitee / creamycake）。

## 自动构建

GitHub Actions（[`.github/workflows/build.yml`](./.github/workflows/build.yml)）：

1. 拉取 `config/sources.json` 中的源
2. 提取 `http(s)://` host，归一成根域，过滤图标 CDN 噪声
3. 生成 `dist/` 下插件与列表
4. 有变更则 commit 回 `main`

触发条件：

- 每天定时
- 修改 `config/` 或 `scripts/` 后 push
- 手动 `workflow_dispatch`

本地构建：

```bash
python3 scripts/build_plugin.py --config config/sources.json --out dist
```

离线（已有 JSON）：

```bash
mkdir -p .cache/sources
# 将 online.json / bt1.json / css1.json 放到 .cache/sources/
python3 scripts/build_plugin.py --offline .cache/sources --out dist
```

## 产物

| 文件 | 说明 |
|---|---|
| `dist/Animeko-DIRECT.plugin` | 域名 → DIRECT |
| `dist/Animeko-PROXY.plugin` | 域名 → PROXY（用户绑定策略组） |
| `dist/Animeko-REJECT.plugin` | 域名 → REJECT |
| `dist/Animeko.list` | 无策略规则列表，订阅时选策略 |
| `dist/Animeko-UA-experimental.list` | USER-AGENT 实验规则（默认不要用） |
| `dist/report.json` | 构建报告与源抓取状态 |

## 策略说明

Loon **插件** `[Rule]` 只允许：

- `DIRECT`
- `REJECT` 系列
- `PROXY`（由你选择/绑定策略组）

规则行本身不能写任意策略组名，也不能像 Rewrite 那样用 `${policy}` 展开。因此本仓用 **三个插件文件** 对应三种策略；若要挂自定义策略组，用 `Animeko.list` 订阅并在订阅界面选组。

## USER-AGENT 与 CDN

Loon 支持：

```text
USER-AGENT,*Animeko*,DIRECT
```

以及逻辑规则（3.1.7+）：

```text
AND,((USER-AGENT,*Animeko*),(DOMAIN-SUFFIX,cdn.example.com)),DIRECT
```

限制：

1. iOS **没有** 稳定的按 App 进程分流；UA 是唯一接近「只对这个客户端」的 HTTP 层手段。
2. Animeko 真实 UA 需在 Loon「最新请求」确认；未确认前不要启用 UA 全量直连（会误伤其他带相同关键字的请求）。
3. 源 JSON **不含** 播放 CDN。检索站域名直连后，m3u8/分片 host 仍可能走代理——到「最新请求」复制 host，追加到 `config/sources.json` 的 `core_domains`，或本地规则。

## 添加自己的源

编辑 `config/sources.json`：

```json
{
  "id": "my-source",
  "name": "custom",
  "url": "https://example.com/source.json",
  "enabled": true
}
```

push 到 `main` 后 Actions 会重建 `dist/`。

## 与 Loon-Rules 的关系

[beckyeeky/Loon-Rules](https://github.com/beckyeeky/Loon-Rules) 放通用远程规则列表。本仓专注 Animeko 源解析与插件产物，二者正交。

## License

MIT
