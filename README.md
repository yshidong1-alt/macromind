# MacroMind V6 · 全球宏观投资训练台

从数据、政策、利率到资产传导，记录每日三问与原始判断。此仓库是根据原会话需求重新搭建的 V6，不是找回的历史 ZIP。

## 当前可用功能

- 中文响应式看板：11 项全球资产、美国宏观、中国指标占位、每日数据简报。
- 服务端 FRED 适配器：CPI、核心 CPI、PCE、核心 PCE、非农月度变化、失业率、GDP 年化季环比、零售月环比、有效联邦基金利率。
- FRED 日度市场参考：S&P 500、NASDAQ 100、WTI **现货**、VIX、2Y/10Y 美债收益率、USD/CNY。不是交易所实时行情。
- 可选 Twelve Data 日线适配器，按账户授权设置 symbol 映射，剔除当前 UTC 日期的未完成日线。未配置的黄金、DXY、沪深 300、恒生保持缺失；不以 ETF 或其他指数冒充目标资产。
- 每日三问、资产方向、信心值与当时数据快照写入 SQLite。按北京时间每天一条，原始判断不可覆盖；T+1/7/30 **日历天**手动复盘；支持完整 JSON 导出。
- 密码登录、HttpOnly 会话、CSRF 校验、登录限速；健康检查公开，个人记录及数据 API 需登录。
- 缓存与独立刷新周期：市场 1 小时、宏观 6 小时。供应商失败保留旧值并标记 STALE；不将抓取时间当作行情时间。
- Docker、Compose、Render Blueprint、持久磁盘及自动化测试。

## 尚未实现 / 不能声称已验证

中国官方数据接入、经济日历与 Consensus、历史 vintage 查询、自动收益归因、AI 评分及经验证的 Macro Score 模型尚未实现。简报只整理已获取数据，不生成新闻。`macro_score` 返回 `null`。FRED 返回最新修订序列及 real-time 元数据，不能把它当作历史首次发布值；快照只保留本应用实际保存时所见内容。

未提供真实数据密钥时，接口返回 `UNCONFIGURED` 和 `null`；测试中的虚构供应商响应仅存在于测试文件，绝不进入正式数据库。公网部署完成与真实数据验证情况请看 `VERIFICATION.md`。

## Render 部署

1. 登录 [Render](https://dashboard.render.com/)，选择 **New → Blueprint**，连接 `yshidong1-alt/macromind` 的 `main` 分支。
2. 使用根目录 `render.yaml`。设置 `APP_PASSWORD`（至少 16 字符，作为你的网页登录密码）；`SESSION_SECRET` 由 Render 自动生成。
3. 核对付费 Web Service 与 1 GB 持久磁盘费用后部署。磁盘挂载 `/var/data`，数据库为 `/var/data/macromind.sqlite3`。**不要改用没有持久磁盘的免费实例保存正式训练记录。**
4. 在服务的 **Environment** 中设置 `FRED_API_KEY`，保存并重启。密钥只填服务端，不能提交到仓库或聊天。
5. 可选设置 `TWELVE_DATA_API_KEY` 与 `MARKET_SYMBOLS_JSON`。后者为 JSON 对象，键只能是 `sp500,nasdaq100,gold,wti,vix,dxy,us2y,us10y,usdcny,csi300,hsi` 中的资产 ID，值为你已在供应商账户中核对的 symbol。不同订阅覆盖范围不同；配置不等于已获行情权限。
6. 等待 Render 显示 Live，访问分配给服务的实际 HTTPS URL。`/api/health` 应返回 `status: ok`；登录后检查数据状态、保存一条判断，再在 Render 重启服务检查记录仍在。

Blueprint 只预配置服务运行与存储所需环境变量；没有数据密钥也能启动，但不会产生行情。公开 GitHub 仓库不会公开数据库或密钥。

Render 默认文件系统是临时的，只有磁盘挂载路径内的文件跨部署保存：[持久磁盘文档](https://render.com/docs/disks)。配置依据：[Blueprint 规范](https://render.com/docs/blueprint-spec)。单进程、单实例是本版设计约束；不要增加 workers、开启 preload 或水平扩容，否则会重复启动刷新线程。

## 本地运行

需要 Python 3.9+（Docker 使用 3.12）。

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export APP_PASSWORD='替换为你自己的至少16字符密码'
export SESSION_SECRET='替换为你自己的至少32字符随机字符串'
export PORT=10000
gunicorn -c gunicorn.conf.py wsgi:app
```

访问 `http://localhost:10000`。默认数据库在 `data/`，默认无数据源密钥。直接运行不会自动加载 `.env`；使用操作系统环境变量或 Docker Compose。

```sh
cp .env.example .env
# 在本地编辑 .env，填写自己的密码与密钥
docker compose up --build -d
```

Compose 绑定本机回环地址，使用命名卷保留数据库。**不要执行 `docker compose down -v`，它会删除数据卷。**

## 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 无需登录的服务及数据库连通检查 |
| POST / GET / DELETE | `/api/session` | 登录 / 获取会话与 CSRF token / 退出 |
| GET | `/api/market` | 全球资产与数据状态 |
| GET | `/api/macro`、`/api/macro/cpi` | 宏观数据 |
| GET | `/api/briefing` | 今日数据简报和三问 |
| GET | `/api/status` | 配置布尔值和刷新状态，不返回密钥 |
| POST | `/api/refresh/market`、`/api/refresh/macro` | 异步刷新；手动调用间隔至少 5 分钟 |
| GET / POST | `/api/judgments` | 查询 / 保存今天的判断 |
| POST | `/api/judgments/:id/reviews` | 保存到期的复盘 |
| GET | `/api/export` | 导出记录和原始数据快照 |

登录后写操作需 `X-CSRF-Token` 请求头。生产环境必须启用 HTTPS；`APP_ENV=production` 时 Cookie 为 Secure，缺少足够长度的密码/会话密钥会拒绝启动。此版是单人训练台，不支持多用户隔离。

### 数据口径

- `observation_date`：供应商观测日 / 宏观统计期，**不是抓取时间或保证的发布日期**。
- `fetched_at`：服务器成功获取时间（UTC）；`last_attempt_at` 为尝试时间。
- FRED 的 `realtime_start`/`realtime_end` 仅按原始语义保留，不声称首次发布值。
- `UNCONFIGURED` 无数据源；`UNKNOWN` 等待首次获取；`ERROR` 首次获取失败；`STALE` 过期或刷新失败；`EOD` 日度参考；`RELEASED` 已获取宏观观测。
- 日度参考观测超过 7 天或缓存超过 24 小时标记过期。宏观观测按各系列设置宽松统计期阈值，缓存超过 48 小时过期；这不是经济发布日历。
- 本版不输出 LIVE 或 DELAYED，因为尚未验证任何交易所实时/延迟授权。

## 测试与备份

```sh
python -m unittest discover -s tests -v
```

测试会验证无密钥行为、权限、日期、缓存回退、判断不可覆盖、复盘、实际 HTTP 服务及进程重启持久性。测试供应商数据使用 mock，不能替代真实密钥验收。

定期通过页面“导出记录”下载 JSON；另用 SQLite 的在线 backup API 备份整个数据库，不在运行时直接复制单个 WAL 主文件。不要把导出文件或数据库上传到公开仓库。

数据源文档：[FRED observations](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)、[FRED API key](https://fred.stlouisfed.org/docs/api/api_key.html)、[Twelve Data 时区](https://support.twelvedata.com/en/articles/5745849-timezones)。
