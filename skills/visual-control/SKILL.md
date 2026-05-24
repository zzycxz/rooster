---
name: visual-control
description: "视觉打标与精确点击控制 (Use when: 需要与 Windows GUI 应用交互、定位并点击按钮、输入框、链接等界面元素). NOT for: 文本处理、代码执行、非 GUI 输出解析."
metadata:
  rooster:
    emoji: "🖥️"
    platform: ["windows"]
    category: "automation"
    requires:
      python_packages: ["ultralytics", "pyautogui"]
      bins: []
      env_vars: []
---

# Visual Control — 视觉打标与精确点击

基于 YOLO 的视觉感知引擎，将屏幕像素位置转化为可执行的自动化操作。

## 使用场景

✅ **以下情况使用此技能：**

- 需要点击 Windows 应用中的按钮
- 需要向表单输入框填写内容
- 动态弹窗/对话框出现，需要实时定位
- 用户说"点击"、"打开"、"找到并点击"

## 不适用场景

❌ **不使用此技能：**

- 解析文本文件内容 → 改用 `file_system_op`
- 仅需截图查看并描述 → 改用 `desktop_read_screen`

## 标准工作流

```
步骤 1：扫描并打标屏幕元素
  desktop_grounding_scan()
  → 返回带字母标签的截图（A=按钮1, B=输入框2 ...）

步骤 2：执行交互（通过宏工具 desktop_act）
  desktop_act(action="click", element_id="A", scan_cache="<上一步返回的 cache_key>")
  desktop_act(action="type", text="要输入的内容")

步骤 3：验证结果
  desktop_read_screen()  → 截图确认状态
```

## 关键原则

- **先扫描后点击**：始终先调用 `desktop_grounding_scan` 获取最新标签，再执行 `desktop_act`。
- **去噪优先**：打标时只标记交互元素（按钮、输入框、链接），过滤噪音。
- **视觉优先**：遇到动态弹窗，相信 YOLO 的实时像素结果。

## 注意事项

- 屏幕分辨率变化会影响坐标精度，高 DPI 屏幕需配置缩放补偿。
- `scan_cache` 参数可复用上一次扫描结果，避免重复打标（同一帧界面内有效）。
