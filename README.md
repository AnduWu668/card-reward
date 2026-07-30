# 五灵集卡

一个可运行的 AI Backend Take-Home：FastAPI + PostgreSQL 后端，以及以抽卡为主的移动端 H5。核心关注幂等、周期额度和赠卡领取并发正确性。

## 一键启动

要求：Docker Desktop / Docker Compose。

```bash
docker compose up --build
```

打开：

- H5：<http://localhost:8000>
- Swagger API 文档：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/health>

启动过程会自动执行 Alembic 初始化并写入 5 种卡及 4 个不同身份的演示用户。删除并重建全部本地演示数据：

```bash
docker compose down -v
docker compose up --build
```

## 演示路径

1. 在页面右上角选择任意种子用户，点击底部抽卡。
2. 点击主卡片翻面查看该卡的题目与解读，点击下方五张小卡切换。
3. 切换为普通大师或传承大师，抽卡结果弹层会显示“赠送此卡”。
4. 生成并复制赠卡链接；在同一页面切换成更低等级用户，再打开链接领取。
5. 尝试本人领取、同级领取、重复领取或超过收卡上限，观察稳定的业务错误。

> `X-Demo-User-Id` 仅服务本地演示，并非生产鉴权方案。

## 本地开发与测试

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
docker compose up -d db
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload
pytest
```

测试会自动创建并使用独立的 `card_reward_test` 数据库；测试代码带有防误删保护，
不会 drop/recreate 正在供 H5 使用的开发数据库。

详细的接口契约、数据模型、锁策略、额度假设和兑换设计见 [docs/API.md](docs/API.md)。

## 取舍

- 实现：抽卡、卡包、赠卡链接、领取、幂等、周期额度、种子数据、H5。
- 只设计：绑定手机号与 399 套餐兑换。题目将其列为选做，本项目把时间集中在必做闭环和并发正确性。
- H5 参考给定截图的午夜星空氛围与信息层级，背景为 AI 生成的原创素材，卡片与交互使用 HTML/CSS 实现，未复制原图资产。
- 数据库迁移的初始版本以 SQLAlchemy metadata 建表，后续结构变更应生成显式 Alembic revision。

## AI 使用说明

我使用 Codex 协助梳理歧义、设计 API 和并发事务、生成样板代码与测试，并使用图像生成工具制作原创 H5 背景；我自己确认了业务假设、API 契约、数据库锁顺序、额度边界和最终运行结果。
