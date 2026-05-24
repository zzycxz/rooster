---
name: data-analysis
description: "数据分析与可视化 (Use when: 需要处理 CSV/Excel 数据、生成图表、统计分析). NOT for: 非数据文件、GUI 操作."
metadata:
  rooster:
    emoji: "📊"
    platform: ["any"]
    category: "data"
    requires:
      python_packages: ["pandas", "matplotlib", "openpyxl"]
      bins: []
      env_vars: []
---

# Data Analysis — 数据分析与可视化

通过 `python_interpreter` 工具，使用 pandas/matplotlib 完成数据处理和图表生成。

## 常用场景

- 读取 CSV/Excel 并统计汇总
- 数据清洗与缺失值处理
- 生成折线图、柱状图、散点图
- 透视表与分组聚合
- 数据导出为新 Excel/CSV

## 读取与基础分析

```python
import pandas as pd

# 读取文件
df = pd.read_csv(r"C:\path\to\data.csv", encoding="utf-8")
# 或 Excel: df = pd.read_excel(r"C:\path\to\data.xlsx", sheet_name="Sheet1")

print(f"数据形状: {df.shape}")
print(df.head())
print(df.describe())
print(df.isnull().sum())
```

## 数据清洗

```python
# 删除重复行
df = df.drop_duplicates()

# 填充缺失值
df["column"].fillna(df["column"].mean(), inplace=True)

# 类型转换
df["date"] = pd.to_datetime(df["date"])
df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
```

## 生成图表

```python
import matplotlib
matplotlib.use("Agg")  # 非交互模式，必须在 import pyplot 之前
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(10, 6))
df.groupby("category")["amount"].sum().plot(kind="bar", ax=ax)
ax.set_title("各类别销售额汇总")
ax.set_xlabel("类别")
ax.set_ylabel("金额")
plt.tight_layout()

output_path = r"C:\Users\user\Desktop\chart.png"
plt.savefig(output_path, dpi=150, bbox_inches="tight")
print(f"[RESULT_PATH: {output_path}]")  # 供 Rooster 识别产出文件
plt.close()
```

## 透视表与分组

```python
# 透视表
pivot = df.pivot_table(
    values="amount",
    index="region",
    columns="product",
    aggfunc="sum",
    fill_value=0
)
print(pivot)

# 导出结果
output_excel = r"C:\Users\user\Desktop\pivot_result.xlsx"
pivot.to_excel(output_excel)
print(f"[RESULT_PATH: {output_excel}]")
```

## 注意事项

1. 始终使用 `matplotlib.use("Agg")` 避免无界面环境报错
2. 文件路径使用原始字符串 `r"..."` 或正斜杠
3. 中文显示需设置字体：`plt.rcParams["font.sans-serif"] = ["SimHei"]`
4. 大文件（>10 万行）使用 `chunksize` 分块读取
