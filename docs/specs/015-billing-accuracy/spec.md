# 015 计费准确性（billing-accuracy）

> 状态：实施中 | 日期：2026-08-12
> 关联：修复清单 P0-2 / P0-3

## 1. 背景与目标

修复计费引擎三个错账点：
1. 配额计数未按租户套餐读取（全部按试用版 100 计）。
2. 账单基础费对所有套餐写死 99，超额费率 1 元/条与 PRD 0.5 元/条不符。
3. 试用租户被生成账单并可能被逾期降级。

## 2. 业务规则与公式

### 2.1 套餐配额（PRD 3.9）

| 套餐 | 月对话配额 |
|---|---|
| trial | 100 |
| basic | 2000 |
| pro | 10000 |
| enterprise | 100000 |

未知套餐降级为 trial。配额计数使用 `INCRBYFLOAT`，按模型权重折算。

### 2.2 账单公式

```
base_fee    = PLAN_BASE_FEES[plan_type]          # 配置化
overage_cnt = max(0, month_count - PLAN_QUOTA[plan_type])
overage_fee = round(overage_cnt * OVERAGE_PRICE, 2)   # OVERAGE_PRICE 默认 0.5 元/条
invoice_total = base_fee + overage_fee
```

示例（basic，某月计数 2100）：
`base_fee = 999`，`overage_cnt = 100`，`overage_fee = 50.00`，`total = 1049.00`。

示例（trial，默认不收费）：不生成账单；若配置 `BILLING_CHARGE_TRIAL=true`，
`base_fee = 0`，仅当超额时按公式计费。

### 2.3 试用租户

- **2026-08-12 产品决策 B**：试用租户免费但超额按单价计费。
  默认 `billing_charge_trial=true`：生成账单，`base_fee=0`，
  超额部分 = max(0, count - 100) * OVERAGE_PRICE；逾期未付按既有规则降级只读。
- 保留配置开关 `BILLING_CHARGE_TRIAL=false` 作为逃生口（关闭后不生成、不降级）。

## 3. 验收条件

- [ ] `incr_usage_for_tenant(basic)` 计数到 2000 前 `is_overage=False`，配额显示 2000。
- [ ] basic 账单：计数 2100 → base 999 / overage 50.00 / total 1049.00。
- [ ] pro 账单：计数 10500 → base 4999 / overage 250.00。
- [x] trial（决策 B）：生成账单，base=0 且仅超额计费；关闭开关时完全不生成。
- [ ] 逾期降级跳过“不收费的 trial 租户”。

## 4. 边界与错误处理

- 配额计数为小数（模型权重 0.5）时，账单超额按实际累计值计算，不做整数截断。
- 配置 JSON 解析失败 → 回退默认值并告警，不阻断计费。
- `billing_charge_trial` 关闭时，历史遗留 trial 账单不触发降级。
- 未知套餐 → 按 trial 处理。

## 5. 非功能约束

- 计费路径不得阻断对话主流程（失败降级，只告警）。
- 金额统一用 `Decimal`/`Numeric(10,2)`，禁止浮点直接比较。
- 所有公式与默认值必须集中配置，禁止散落硬编码。
