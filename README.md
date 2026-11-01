# MiniClaw

## 项目背景

MiniClaw 是一款以语音交互为主的家庭陪伴助手，面向需要简单、自然交流和日常提醒支持的家庭场景。项目支持语音对话、家庭角色配置、提醒管理与经授权的家庭信息和记忆管理。

## 目标人群

主要面向希望为长者提供易用语音陪伴与提醒服务的家庭；长者是主要使用者，家人可参与角色、提醒及共享信息的管理。

## 技术栈

- 前端：Next.js、React、TypeScript，支持渐进式 Web 应用（PWA）。
- 后端：Python、FastAPI、SQLAlchemy、Pydantic。
- 对话与模型接口：LangGraph、OpenAI 兼容接口；语音识别和语音合成通过兼容接口接入。
- 数据与任务：PostgreSQL、Alembic、LangGraph PostgreSQL Checkpoint、独立提醒调度进程。
- 部署与网关：Docker Compose、Caddy、ffmpeg。

## 开源声明

项目使用了 FastAPI、Next.js、React、LangGraph、SQLAlchemy、PostgreSQL 及其他开源组件，具体依赖及版本见 `api/requirements.txt` 和 `web/package.json`。
