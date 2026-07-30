# 五灵集卡 API 设计

## 1. 通用约定

- Base URL：`/api/v1`
- JSON 字段使用 `snake_case`，时间使用带时区的 ISO 8601。
- Take-Home 不实现正式认证。受保护接口通过 `X-Demo-User-Id` 选择种子用户；生产环境应由微信登录/短信登录后的认证中间件注入用户身份。
- 所有命令接口强制携带 8–100 字符的 `Idempotency-Key`。幂等作用域为 `(当前用户, 接口动作, key)`。
- 相同 key 与相同请求重试，返回第一次的成功或业务失败结果；同一 key 改变参数返回 `409 IDEMPOTENCY_KEY_REUSED`。
- 活动周期均按 `Asia/Shanghai` 计算；每日从 00:00 开始，每周从周一 00:00 开始。

错误响应：

```json
{
  "error": {
    "code": "RECIPIENT_DAILY_LIMIT_REACHED",
    "message": "今日普通卡领取次数已用完",
    "request_id": "req_8f4e1b55c4934db6"
  }
}
```

## 2. 接口

### 2.1 演示用户

`GET /demo/users`

用于 H5 切换种子用户，无需用户头。响应：

```json
[
  {
    "id": "c3d4...",
    "nickname": "小满（普通用户）",
    "role": "USER"
  }
]
```

### 2.2 卡包

`GET /cards`

请求头：`X-Demo-User-Id`

始终返回按展示顺序排列的五种卡，未持有卡的 `quantity` 为 0：

```json
[
  {
    "id": "692f...",
    "code": "QINGLONG",
    "name": "青龙",
    "rarity": "NORMAL",
    "quantity": 2,
    "question": "此刻最值得你主动争取的机会是什么？",
    "interpretation": "青龙象征生发与行动……",
    "display_order": 1
  }
]
```

### 2.3 抽卡

`POST /draws`

请求头：`X-Demo-User-Id`、`Idempotency-Key`；无请求体。

```json
{
  "draw_id": "77da...",
  "card": {"id": "692f...", "code": "QINGLONG", "name": "青龙", "quantity": 3},
  "draws_remaining_today": 2
}
```

概率权重为青龙、朱雀、白虎、玄武各 `2475/10000`，麒麟 `100/10000`。每次独立，无保底。

常见错误：`429 DRAW_LIMIT_REACHED`。

### 2.4 创建赠卡链接

`POST /gift-links`

请求头：`X-Demo-User-Id`、`Idempotency-Key`

```json
{"card_type_id": "692f..."}
```

`201` 响应：

```json
{
  "gift_id": "a3a9...",
  "share_url": "http://localhost:8000/g/gt_opaque-token",
  "expires_at": "2026-08-06T09:00:00+00:00",
  "status": "AVAILABLE",
  "card": {"id": "692f...", "name": "青龙", "quantity": 0}
}
```

创建链接不检查或扣减赠送额度，也不消耗赠送者卡包。链接 7 天过期，每位赠送者每天最多创建 50 条，避免垃圾链接。普通用户返回 `403 SENDER_ROLE_FORBIDDEN`。

### 2.5 赠卡预览

`GET /gift-links/{token}`

公开接口，用于分享落地页，只返回赠送者昵称、卡片内容、状态与过期时间，不泄露额度。

### 2.6 领取赠卡

`POST /gift-links/{token}/claims`

请求头：`X-Demo-User-Id`、`Idempotency-Key`；无请求体。

```json
{
  "gift_id": "a3a9...",
  "claimed_at": "2026-07-30T09:05:00+00:00",
  "card": {"id": "692f...", "name": "青龙", "quantity": 1}
}
```

校验顺序：链接存在 → 未领取 → 未过期 → 不是本人 → 发送者等级严格高于领取者 → 发送者额度 → 领取者上限。只有全部通过才同时扣双方用量、改变链接状态并发卡。

主要错误：

| HTTP | code | 含义 | 链接是否保留 |
|---|---|---|---|
| 403 | `RECIPIENT_ROLE_FORBIDDEN` | 非高级送低级 | 是 |
| 409 | `CANNOT_CLAIM_OWN_GIFT` | 领取自己的链接 | 是 |
| 409 | `GIFT_ALREADY_CLAIMED` | 已被他人领取 | 否 |
| 410 | `GIFT_LINK_EXPIRED` | 已过期 | 否 |
| 429 | `SENDER_QUOTA_EXHAUSTED` | 赠送者当前额度不足 | 是 |
| 429 | `RECIPIENT_DAILY_LIMIT_REACHED` | 普通卡日收卡已达 1 张 | 是 |
| 429 | `RECIPIENT_WEEKLY_LIMIT_REACHED` | 麒麟周收卡已达 1 张 | 是 |

## 3. 数据模型

| 表 | 核心字段 | 约束/用途 |
|---|---|---|
| `users` | `id, nickname, role, phone` | 角色为 USER / MASTER / INHERITOR |
| `card_types` | `code, rarity, draw_weight, question, interpretation` | 五种系统预置卡，整数权重合计 10000 |
| `card_balances` | `user_id, card_type_id, quantity` | 联合主键；`quantity >= 0`，卡包快速读取 |
| `card_transactions` | `delta, source_type, source_id` | 获卡/扣卡审计流水；来源唯一约束防重复 |
| `quota_usage` | `user_id, kind, period_start, used` | 联合主键；存周期用量而非剩余值 |
| `gift_links` | `token_hash, sender_id, card_type_id, status, claimed_by, expires_at` | token 只存 SHA-256；一条链接至多一次领取 |
| `idempotency_records` | `actor_id, operation, key, request_hash, http_status, response_body` | 幂等作用域唯一，缓存成功及业务失败 |

额度上限：

| 身份 | 普通卡赠送/日 | 麒麟赠送/周 |
|---|---:|---:|
| 普通用户 | 0 | 0 |
| 普通大师 | 3 | 1 |
| 传承大师 | 8 | 2 |

所有用户普通卡每天最多收 1 张、麒麟每周最多收 1 张、每天最多抽 3 次。`period_start` 使重置无需定时任务：新周期自然写入新行，额度不累积、不结转。

## 4. 最难接口与并发权衡

### 领取

领取在一个数据库事务中：

1. 取得该请求的 PostgreSQL 事务级 advisory lock，串行化相同幂等键。
2. `SELECT gift_links ... FOR UPDATE` 锁住链接。多人抢同一链接时只有第一人能看到 `AVAILABLE` 并提交。
3. 对当前周期用量执行 `INSERT ... ON CONFLICT DO NOTHING`，随后 `SELECT ... FOR UPDATE`。
4. 锁住赠送者的赠卡用量行并检查 `used < limit`。同一赠送者的多条链接会竞争同一行，因此最后 1 点额度只可能扣一次。
5. 锁住领取者收卡用量，增加卡余额与流水，将链接改为 `CLAIMED`。
6. 任一步失败则整体回滚，链接和双方额度都不改变。

这里选择数据库悲观锁，而不是 Redis 分布式锁：PostgreSQL 是最终事实来源，事务同时覆盖额度、链接和卡余额，不会出现“Redis 锁成功但数据库提交失败”的双写问题。热点链接会被串行化，但单条链接只成功一次，锁持有时间很短。

### 抽卡

抽卡先锁幂等键，再锁用户当日 `DRAW` 用量。扣次数、随机选卡、增加余额、写流水和保存响应同成同败。随机数仅在首次请求的事务内生成；重试直接返回持久化结果，避免重复扣次数或重新随机。

## 5. 兑换设计（不实现）

`POST /redemptions` 要求已绑定手机号和 `Idempotency-Key`。事务内按固定卡种顺序锁定五行 `card_balances`，确认每种至少 1 张后各扣 1，写五条流水，增加权益余额并保存兑换记录。卡扣减、权益增加和幂等响应共用一个 PostgreSQL 事务；任一步失败全部回滚。固定锁顺序避免两个并发兑换互相死锁。

## 6. 模糊点与假设

- 题面未说明赠卡是否消耗本人卡片。本实现将身份额度理解为系统发卡额度，不要求拥有、不扣本人库存。
- 题面只给出传承大师普通卡日额度 8。暂定普通大师为 3/日、1 麒麟/周，传承大师为 8/日、2 麒麟/周。
- 链接有效期未定义，暂定 7 天；有效链接跨周期后可使用新周期额度。
- 题面未定义活动结束时间，本实现不设置活动结束，仅展示规则。

## 7. 扩展到 10 万 DAU

首先出现瓶颈的是热点链接请求与 PostgreSQL 连接数。可在 CDN/网关限流无效 token，请求进入应用后先读缓存的“已领取/已过期”终态；只有仍可领取的请求进入数据库。应用无状态横向扩容，连接池前增加 PgBouncer。额度行可能成为高身份用户的热点，但每次赠送成功数很小；若额度配置显著放大，可按用户分片，仍由单分片事务保证一致性。卡包读接口可缓存，写后按用户失效。

