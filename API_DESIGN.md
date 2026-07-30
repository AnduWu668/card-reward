# 五灵集卡 API 设计方案

## 1. 设计目标

本系统覆盖以下核心闭环：

1. 用户每天抽卡，抽到的卡进入永久卡包。
2. 大师创建赠卡链接，创建时不扣赠送额度。
3. 第一个满足条件的用户领取成功后，才扣赠送者额度并发卡。
4. 并发情况下，一条链接只能被领取一次，赠送额度也不能被超扣。

后端使用：

- FastAPI
- PostgreSQL
- SQLAlchemy
- Alembic

所有活动周期按 `Asia/Shanghai` 时区计算。

> 当前项目不实现正式登录。受保护接口使用 `X-Demo-User-Id` 标识演示用户，生产环境应替换为认证中间件提供的可信用户身份。

---

## 2. 通用 API 约定

Base URL：

```text
/api/v1
```

命令类接口必须携带：

```http
Idempotency-Key: 8c356834-9541-45f2-a1a0-2ae329d8083e
```

幂等作用域：

```text
当前用户 + 操作类型 + Idempotency-Key
```

规则：

- 相同 key、相同请求：返回第一次保存的响应。
- 相同 key、不同参数：返回 `409 IDEMPOTENCY_KEY_REUSED`。
- 成功和确定性的业务失败都会保存。
- 幂等键不会跨用户、跨接口复用。

统一错误结构：

```json
{
  "error": {
    "code": "DRAW_LIMIT_REACHED",
    "message": "今日 3 次抽卡机会已用完",
    "request_id": "req_88bba732f9524b49"
  }
}
```

---

## 3. API 一览

| 用户动作 | Method | Path | 是否需要用户身份 | 是否需要幂等键 |
|---|---|---|---:|---:|
| 查询演示用户 | GET | `/demo/users` | 否 | 否 |
| 查看卡包 | GET | `/cards` | 是 | 否 |
| 抽卡 | POST | `/draws` | 是 | 是 |
| 创建赠卡链接 | POST | `/gift-links` | 是 | 是 |
| 查看赠卡链接 | GET | `/gift-links/{token}` | 否 | 否 |
| 领取赠卡 | POST | `/gift-links/{token}/claims` | 是 | 是 |

兑换为选做部分，本项目只设计、不实现：

```text
POST /redemptions
```

---

## 4. 查询演示用户

```http
GET /api/v1/demo/users
```

用途：供 H5 下拉框切换不同身份。

响应：

```json
[
  {
    "id": "c3d45e89-3df8-41ec-9a88-a6dd165d4ee8",
    "nickname": "小满（普通用户）",
    "role": "USER"
  },
  {
    "id": "3853a777-630a-4fd4-bd79-d32fa0fe69af",
    "nickname": "青禾（普通大师）",
    "role": "MASTER"
  }
]
```

角色枚举：

| 值 | 含义 |
|---|---|
| `USER` | 普通用户 |
| `MASTER` | 普通大师 |
| `INHERITOR` | 传承大师 |

---

## 5. 查看卡包

```http
GET /api/v1/cards
X-Demo-User-Id: {user_id}
```

返回全部五种卡。没有持有的卡也会返回，`quantity` 为 0，方便前端稳定渲染五个卡位。

响应：

```json
[
  {
    "id": "971b114b-cc95-454d-aad9-ef38a45bd06a",
    "code": "BAIHU",
    "name": "白虎",
    "rarity": "NORMAL",
    "quantity": 2,
    "question": "你需要为哪件事划清边界？",
    "interpretation": "白虎象征决断与守护……",
    "display_order": 3
  }
]
```

卡片编码：

| code | 名称 | 稀有度 | 抽取概率 |
|---|---|---|---:|
| `QINGLONG` | 青龙 | 普通 | 24.75% |
| `ZHUQUE` | 朱雀 | 普通 | 24.75% |
| `BAIHU` | 白虎 | 普通 | 24.75% |
| `XUANWU` | 玄武 | 普通 | 24.75% |
| `QILIN` | 麒麟 | 稀有 | 1% |

---

## 6. 抽卡

```http
POST /api/v1/draws
X-Demo-User-Id: {user_id}
Idempotency-Key: {unique_key}
```

无请求体。

成功响应：

```json
{
  "draw_id": "44faf242-de7e-4caa-ba75-2c7483ddd953",
  "card": {
    "id": "971b114b-cc95-454d-aad9-ef38a45bd06a",
    "code": "BAIHU",
    "name": "白虎",
    "rarity": "NORMAL",
    "quantity": 1,
    "question": "你需要为哪件事划清边界？",
    "interpretation": "白虎象征决断与守护……",
    "display_order": 3
  },
  "draws_remaining_today": 2
}
```

事务内操作：

1. 锁定幂等键。
2. 锁定用户当天的抽卡用量。
3. 检查是否少于每天 3 次。
4. 使用服务端随机数按整数权重选卡。
5. 抽卡用量加 1。
6. 卡余额加 1。
7. 写入卡片流水。
8. 保存幂等响应。
9. 提交事务。

任一步失败都会整体回滚。

抽卡概率使用整数权重：

```text
青龙 2475
朱雀 2475
白虎 2475
玄武 2475
麒麟  100
合计 10000
```

常见错误：

```http
429 Too Many Requests
```

```json
{
  "error": {
    "code": "DRAW_LIMIT_REACHED",
    "message": "今日 3 次抽卡机会已用完"
  }
}
```

---

## 7. 创建赠卡链接

```http
POST /api/v1/gift-links
X-Demo-User-Id: {sender_id}
Idempotency-Key: {unique_key}
Content-Type: application/json
```

请求：

```json
{
  "card_type_id": "971b114b-cc95-454d-aad9-ef38a45bd06a"
}
```

成功响应：

```http
201 Created
```

```json
{
  "gift_id": "a3a98480-2455-40a0-abdd-a3572ab096eb",
  "share_url": "http://localhost:8000/g/gt_opaque-random-token",
  "expires_at": "2026-08-06T09:00:00Z",
  "status": "AVAILABLE",
  "card": {
    "id": "971b114b-cc95-454d-aad9-ef38a45bd06a",
    "code": "BAIHU",
    "name": "白虎",
    "rarity": "NORMAL",
    "quantity": 0
  }
}
```

业务规则：

- 普通用户不能创建赠卡链接。
- 创建链接时不扣额度。
- 不要求赠送者拥有该卡，也不扣赠送者卡包。
- 一条链接只对应一种卡，并且最多成功领取一次。
- 链接有效期为 7 天。
- 每位大师每天最多创建 50 条链接，用于防止无限创建垃圾数据。
- 数据库不保存原始 token，只保存其 SHA-256 摘要。

> 链接创建数量不等同于成功赠送额度。即使创建了很多链接，最终成功领取数仍由领取事务中的赠送额度限制。

---

## 8. 查看赠卡链接

```http
GET /api/v1/gift-links/{token}
```

这是公开预览接口，不要求用户身份。

响应：

```json
{
  "sender_nickname": "青禾（普通大师）",
  "card": {
    "id": "971b114b-cc95-454d-aad9-ef38a45bd06a",
    "code": "BAIHU",
    "name": "白虎",
    "rarity": "NORMAL",
    "quantity": 0,
    "question": "你需要为哪件事划清边界？",
    "interpretation": "白虎象征决断与守护……",
    "display_order": 3
  },
  "status": "AVAILABLE",
  "expires_at": "2026-08-06T09:00:00Z"
}
```

接口不会暴露：

- 赠送者 ID
- 赠送者剩余额度
- 已领取用户信息
- 数据库中的链接 ID

---

## 9. 领取赠卡

```http
POST /api/v1/gift-links/{token}/claims
X-Demo-User-Id: {recipient_id}
Idempotency-Key: {unique_key}
```

无请求体。

成功响应：

```json
{
  "gift_id": "a3a98480-2455-40a0-abdd-a3572ab096eb",
  "claimed_at": "2026-07-30T09:05:00Z",
  "card": {
    "id": "971b114b-cc95-454d-aad9-ef38a45bd06a",
    "code": "BAIHU",
    "name": "白虎",
    "rarity": "NORMAL",
    "quantity": 1,
    "question": "你需要为哪件事划清边界？",
    "interpretation": "白虎象征决断与守护……",
    "display_order": 3
  }
}
```

校验顺序：

1. 链接是否存在。
2. 链接是否已被领取。
3. 链接是否过期。
4. 是否领取自己的链接。
5. 赠送者身份是否严格高于领取者。
6. 赠送者本周期额度是否充足。
7. 领取者本周期收卡次数是否超限。

领取成功后，在同一事务内：

1. 赠送者额度用量加 1。
2. 领取者收卡用量加 1。
3. 领取者卡余额加 1。
4. 写入卡片流水。
5. 链接状态改为 `CLAIMED`。
6. 记录领取者与领取时间。
7. 保存幂等响应。

### 身份规则

| 赠送者 | 可以赠给 |
|---|---|
| 普通用户 | 无 |
| 普通大师 | 普通用户 |
| 传承大师 | 普通大师、普通用户 |

### 额度规则

| 身份 | 普通卡赠送额度 | 麒麟赠送额度 |
|---|---:|---:|
| 普通用户 | 0 | 0 |
| 普通大师 | 3 张/日 | 1 张/周 |
| 传承大师 | 8 张/日 | 2 张/周 |

所有领取者：

- 普通卡最多领取 1 张/日。
- 麒麟最多领取 1 张/周。

### 领取错误

| HTTP | code | 含义 | 链接是否仍可被别人领取 |
|---:|---|---|---:|
| 403 | `RECIPIENT_ROLE_FORBIDDEN` | 不满足高级送低级 | 是 |
| 409 | `CANNOT_CLAIM_OWN_GIFT` | 领取自己的链接 | 是 |
| 409 | `GIFT_ALREADY_CLAIMED` | 已被别人领取 | 否 |
| 410 | `GIFT_LINK_EXPIRED` | 链接过期 | 否 |
| 429 | `SENDER_QUOTA_EXHAUSTED` | 赠送者额度不足 | 是 |
| 429 | `RECIPIENT_DAILY_LIMIT_REACHED` | 普通卡日收卡超限 | 是 |
| 429 | `RECIPIENT_WEEKLY_LIMIT_REACHED` | 麒麟周收卡超限 | 是 |

---

## 10. 核心数据模型

### users

```text
id
nickname
role
phone
created_at
```

### card_types

```text
id
code
name
rarity
draw_weight
question
interpretation
display_order
```

### card_balances

```text
user_id
card_type_id
quantity
updated_at

PRIMARY KEY (user_id, card_type_id)
CHECK quantity >= 0
```

用于快速查询用户当前每种卡的数量。

### card_transactions

```text
id
user_id
card_type_id
delta
source_type
source_id
created_at
```

记录每次抽卡、领取和未来兑换的卡片变动，便于审计与排查。

### quota_usage

```text
user_id
kind
period_start
used
updated_at

PRIMARY KEY (user_id, kind, period_start)
CHECK used >= 0
```

`kind` 包括：

```text
DRAW
GIFT_NORMAL
GIFT_RARE
RECEIVE_NORMAL
RECEIVE_RARE
```

系统存储本周期的 `used`，而不是一个需要定时重置的 `remaining`。

例如普通大师当天已成功赠送 2 张普通卡：

```text
limit = 3
used = 2
remaining = limit - used = 1
```

次日使用新的 `period_start` 行，用量自然从 0 开始，不需要零点重置任务，也不会累积额度。

### gift_links

```text
id
token_hash
sender_id
card_type_id
status
claimed_by
created_at
expires_at
claimed_at
```

状态：

```text
AVAILABLE
CLAIMED
```

过期状态不必异步写入数据库，由 `expires_at <= now` 动态判断。

### idempotency_records

```text
id
actor_id
operation
key
request_hash
http_status
response_body
created_at

UNIQUE (actor_id, operation, key)
```

---

## 11. 两类关键并发问题

### 11.1 同一链接多人同时领取

领取事务执行：

```sql
SELECT *
FROM gift_links
WHERE token_hash = :token_hash
FOR UPDATE;
```

第一位请求者锁住链接并完成领取后，将状态改为 `CLAIMED`。

其他并发请求只能等待。拿到锁后会重新读取状态，此时发现已领取，返回：

```text
409 GIFT_ALREADY_CLAIMED
```

因此一条链接最多发出一张卡。

### 11.2 多条链接同时争抢最后一点额度

假设普通大师当前普通卡赠送额度只剩 1，但已经创建两条链接。

两个领取事务会锁定同一行：

```sql
SELECT *
FROM quota_usage
WHERE user_id = :sender_id
  AND kind = 'GIFT_NORMAL'
  AND period_start = :today
FOR UPDATE;
```

第一个事务将 `used` 从 2 改为 3。

第二个事务拿到锁后重新检查：

```text
used >= limit
```

随后返回：

```text
429 SENDER_QUOTA_EXHAUSTED
```

额度不会变成负数，也不会发出第二张卡。

---

## 12. 为什么使用 PostgreSQL 行锁

本系统没有使用 Redis 分布式锁，主要原因是：

- 链接状态、额度、卡余额都以 PostgreSQL 为最终事实来源。
- 行锁与业务写操作属于同一个事务。
- 数据库提交失败时，所有状态一起回滚。
- 避免 Redis 锁成功、数据库事务失败所带来的双写一致性问题。

对于一条只允许领取一次的热点链接，短暂串行化是合理的。

如果访问量进一步增大，可以：

- 在网关限制单 IP、单 token 的请求速率。
- 缓存已经领取或过期的终态。
- 使用 PgBouncer 管理连接。
- 将应用服务无状态横向扩容。

---

## 13. 兑换接口设计（选做，不实现）

```http
POST /api/v1/redemptions
X-Demo-User-Id: {user_id}
Idempotency-Key: {unique_key}
```

请求：

```json
{
  "reward_code": "PACKAGE_399"
}
```

事务步骤：

1. 检查手机号是否绑定。
2. 按固定顺序锁定五种卡余额。
3. 检查每种卡是否至少有 1 张。
4. 五种卡各扣 1。
5. 写入五条卡片流水。
6. 增加 AI 聊天轮数和档案数量权益。
7. 创建兑换记录。
8. 保存幂等响应。
9. 提交事务。

所有操作共用同一个 PostgreSQL 事务，因此能够保证：

```text
扣卡成功 ⇔ 权益发放成功
```

固定的卡片锁定顺序可以减少两个并发兑换之间的死锁风险。

---

## 14. 本题中的业务假设

题目没有完全定义的部分采用以下假设：

1. 赠卡不消耗赠送者卡包，赠送额度本身就是系统发卡限制。
2. 普通大师额度暂定为普通卡 3 张/日、麒麟 1 张/周。
3. 传承大师额度为普通卡 8 张/日、麒麟 2 张/周。
4. 链接 7 天过期。
5. 有效旧链接跨过额度重置点后，可以使用新周期额度。
6. 每人每天最多创建 50 条赠卡链接，用于防滥用。
7. 活动暂不设置统一结束时间。
