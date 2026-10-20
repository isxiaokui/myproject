# 花伴智能养花助手

一个基于 Vue、FastAPI、LangChain 和 Chroma 的花卉养护助手，支持花卉知识检索、天气查询、上传图片识花与病虫害初步诊断，以及基于知识库的浇水和施肥计划建议。

## 项目结构

```text
.
├── backend/
│   ├── app/                 # FastAPI 应用与工具
│   ├── data/                # 花卉资料；chroma_db 为本地生成的向量库
│   ├── scripts/             # 初始化知识库、手动检索脚本
│   ├── .env.example         # 环境变量示例
│   └── requirements.txt
└── frontend/                # Vue 3 + Vite 聊天页面
```

## 启动

1. 根目录 `.env` 和 `backend/.env` 是本机配置，不要提交到 Git。若文件不存在，从对应 `.env.example` 复制；设置强随机 `POSTGRES_PASSWORD` 和 `AUTH_SECRET_KEY`，并填入自己的 `ZAI_API_KEY`。需要天气功能时再填 `WEATHER_API_KEY`。这些文件不应沿用曾经提交到 Git 的任何密钥。
2. Docker Compose 会从根目录 `.env` 读取 PostgreSQL 配置，并在容器内覆盖 `DATABASE_URL`、`DB_HOST` 和 `REDIS_URL`；无需把数据库密码写入容器的 `DATABASE_URL`。本地直接启动后端时，才需把 `backend/.env` 中的 `DATABASE_URL` 改成 URL 编码后的实际数据库连接串。
3. 安装后端依赖：`pip install -r requirements.txt`（在 `backend` 目录执行）。
4. 在 `backend` 目录启动 API：`python -m uvicorn app.main:app --reload --port 3001`。应用启动时会在 PostgreSQL 中自动创建会话和消息表。
5. 将 `frontend/.env.example` 复制为 `frontend/.env`；本地开发可保持默认 API 地址，部署时设置为后端 API 的地址。
6. 在 `frontend` 目录执行 `npm install`，再执行 `npm run dev`。

## Docker 部署

项目根目录的 `compose.yaml` 可启动 PostgreSQL、Redis、FastAPI 后端和 Nginx 前端。部署前确认两份忽略的环境文件都已创建：根目录 `.env` 设置强随机 `POSTGRES_PASSWORD`；`backend/.env` 设置新的 `AUTH_SECRET_KEY` 和有效的 `ZAI_API_KEY`，需要天气功能时再设置 `WEATHER_API_KEY`。按正式域名配置 `FRONTEND_ORIGINS`。Compose 会在容器内使用服务名连接数据库和 Redis，不要把容器地址设成 `localhost`。如果任何密钥曾经提交到 Git，应先在对应服务后台撤销并重发，再写入新值。

先构建后端和前端镜像（分开构建可降低小规格服务器同时构建的内存峰值）：

```bash
docker compose build backend
docker compose build web
```

首次部署或需要重建知识库时，在服务器执行一次 `docker compose run --rm backend python scripts/build_kb.py`；它会调用智谱 Embedding API，并将 Chroma 数据写入持久化卷。该步骤成功后再运行 `docker compose up -d`，并检查 `docker compose ps`、`docker compose logs backend`，再实际验证注册、登录、聊天、图片分析和天气功能。未配置或未更换示例 API Key 时，服务可能启动但模型功能不可用。PostgreSQL 与 Redis 只绑定服务器本机回环地址，后端端口不映射到公网，网页由 Nginx 的 80 端口提供；安全组不要开放 3001、5432 或 6379。真实用户登录前先配置 HTTPS，HTTP 仅用于临时验证。

## 流式聊天

聊天页面通过 `POST /chat/stream` 接收 Server-Sent Events（SSE）。每个 `data:` 事件是 JSON，类型为 `start`、`token`、`done` 或 `error`；流正常结束时最后发送 `data: [DONE]`。工具调用与工具参数只在后端处理，不会作为事件发送给浏览器。普通 JSON 聊天接口 `/chat` 仍保留，图片接口仍返回 JSON。模型上下文只使用最近 20 条消息；数据库仍保留完整会话历史。单条消息最多 12,000 字符，请求最多携带 100 条消息。

后端直接依赖版本固定在 `backend/requirements.txt`，可在 `backend` 目录通过 `pip install -r requirements.txt` 安装。数据库连接使用 Psycopg 连接池（默认最大 10 条，可用 `DB_POOL_MIN_SIZE`、`DB_POOL_MAX_SIZE`、`DB_POOL_TIMEOUT_SECONDS` 调整）。登录和注册按 IP 限流（默认每分钟分别 10 次、5 次）；聊天默认每分钟每 IP 30 次、每账号 10 次，图片分析默认每分钟每 IP 10 次、每账号 5 次，可通过 `CHAT_IP_RATE_LIMIT`、`CHAT_USER_RATE_LIMIT`、`IMAGE_IP_RATE_LIMIT`、`IMAGE_USER_RATE_LIMIT` 和 `RESOURCE_RATE_WINDOW_SECONDS` 调整。Redis 可用时多进程共享计数，Redis 故障时回退到单进程计数。Redis 缓存本身故障会降级为不缓存。示例文件中的 API Key 和认证密钥是占位值，必须替换为自己的配置。

花卉向量检索结果也会按查询文本、返回条数和 `KNOWLEDGE_CACHE_VERSION` 缓存，默认有效期 15 分钟。重建或更新知识库后，请递增 `KNOWLEDGE_CACHE_VERSION` 以立即隔离旧缓存；天气和知识缓存均依赖 Redis 可用，但 Redis 故障会回退为直接查询。

## 自动化测试

在 `backend` 目录执行以下命令运行后端测试：

```powershell
python -m unittest discover -s tests -v
```

测试覆盖账号校验与令牌、会话鉴权和用户隔离、聊天消息保存、图片格式转换与识别结果解析，以及天气城市匹配、预报解析和超时处理。模型、数据库和天气服务均使用 mock，不需要配置真实 API Key 或启动 PostgreSQL。

图片分析支持识花和病虫害初步诊断，上传格式为 JPEG、PNG、WebP，最大 8 MB。后端会在内存中校验并缩放图片，不会将图片写入磁盘。图片诊断仅供初步参考，不能替代现场检验。
模型调用超时可通过后端 `MODEL_TIMEOUT_SECONDS` 调整；部署到其他域名时，还需把前端源地址加入后端 `FRONTEND_ORIGINS`（多个地址用逗号分隔）。
识花把握不足或有多个候选时会保留候选和判断依据，不会把特定养护资料绑定到未经确认的花名。

首次构建花卉向量库时，在 `backend` 目录执行 `python scripts/build_kb.py`。

图片上传请求体整体限制为 8 MB 加 64 KB multipart 开销。生产部署还应通过 TLS 反向代理、设置安全密钥和精确 CORS 域名，并避免将数据库或 Redis 端口暴露到公网。

## 并发压测

后端 CI 会运行 unittest 与前端构建。需要压测时，另建测试环境安装 Locust（不加入运行时依赖）：`python -m pip install locust`。先启动 API，并通过测试账号获取 token，然后在 `backend` 目录设置 `LOAD_TEST_TOKEN`，运行：

```powershell
locust -f loadtest/locustfile.py --host http://127.0.0.1:3001 --users 20 --spawn-rate 2 --run-time 2m --headless
```

该场景仅压测已认证的会话列表接口，不调用外部大模型；逐级增加并发并观察延迟、错误率、数据库连接数与 CPU/内存。不要对生产服务直接运行压测。

向量库保存在 `backend/data/chroma_db/`，已加入 Git 忽略规则；需要在新环境中运行知识库构建脚本生成。

聊天历史保存在 PostgreSQL 的 `conversations` 和 `messages` 表中；前端侧栏可新建、切换和删除会话。账号通过用户名和密码注册/登录，会话按账号隔离。升级前已存在的无主会话不会自动分配给新注册用户，以免泄露旧数据；如需保留，应由管理员核实归属后手动迁移。需要先启动 PostgreSQL 并设置数据库连接参数与 `AUTH_SECRET_KEY`，未配置数据库或认证密钥时后端启动会报出明确错误。
