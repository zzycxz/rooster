---
name: email-139
description: "通过中国移动 139 邮箱发送邮件（支持附件、纯文本）。凭据已预配置，直接调用即可。"
metadata:
  rooster:
    emoji: "📧"
    category: "comms"
    platform: ["any"]
    author: "rooster-community"
    requires:
      python_packages: []
      bins: []
      env_vars:
        - SMTP_DEFAULT_HOST
        - SMTP_DEFAULT_USER
        - SMTP_DEFAULT_PASS
---

# 📧 email-139 — 139 邮箱发送

通过中国移动 139 邮箱 SMTP 服务发送电子邮件。账号凭据已通过环境变量预配置，**无需在调用时传入 smtp_host / username / password**。

## 账号信息（已预配置于 .env.local）

| 字段 | 值 |
|------|-----|
| SMTP 服务器 | smtp.139.com |
| 端口 | 465（SSL） |
| 发件人 | 13520131488@139.com |
| 认证方式 | POP3/SMTP 授权码（非账号密码） |

## 工具

使用 **`email_send`** 工具发送邮件。

## 典型调用

### 发送普通文本邮件

```python
email_send(
    to=["recipient@example.com"],
    subject="会议纪要 - 5月28日",
    body="你好，\n\n附上今天会议的纪要，请查阅。\n\n祝好"
)
```

> `smtp_host`、`username`、`password` 已从环境变量自动填充，无需提供。

### 发送带附件的邮件

```python
email_send(
    to=["recipient@example.com"],
    subject="报告附件",
    body="你好，\n\n请查收附件中的报告文件。\n\n祝好",
    attachments=[r"C:\Users\user\Desktop\report.pdf"]
)
```

**附件注意事项：**
- 支持任意文件格式（pdf、xlsx、docx、txt、png 等）
- 多个附件传列表：`attachments=["file1.pdf", "file2.xlsx"]`
- 单封邮件附件总大小建议不超过 10MB（139 限制约 50MB，但过大易被反垃圾拦截）
- 附件路径必须是绝对路径，路径不存在时该附件被静默跳过

### 发送给多个收件人

```python
email_send(
    to=["alice@example.com", "bob@example.com"],
    subject="周报",
    body="各位好，\n\n本周工作汇报如下……\n\n祝好"
)
```

## 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `to` | List[str] | ✅ | 收件人邮箱列表 |
| `subject` | str | ✅ | 邮件主题 |
| `body` | str | ✅ | 正文（纯文本） |
| `attachments` | List[str] | ❌ | 附件文件绝对路径列表 |
| `smtp_host` | str | ❌ | 默认读取 `SMTP_DEFAULT_HOST` |
| `smtp_port` | int | ❌ | 默认 465（SSL） |
| `username` | str | ❌ | 默认读取 `SMTP_DEFAULT_USER` |
| `password` | str | ❌ | 默认读取 `SMTP_DEFAULT_PASS` |
| `use_ssl` | bool | ❌ | 默认 `True`（139 邮箱须使用 SSL） |

## 反垃圾评分规避指南

139 邮箱使用自动反垃圾评分系统，以下写法会**显著拉高得分**并导致 550 拒绝：

| 高风险写法 | 建议替代 |
|-----------|---------|
| 主题含"测试"、"test"、"验证" | 用真实业务主题，如"会议纪要"、"周报" |
| 正文含"自动发送"、"系统通知" | 用自然语言正文，以"你好"开头 |
| 短时间内连续发多封相似邮件 | 间隔至少 2 分钟，或内容有实质差异 |
| 发件人与收件人相同（自发自收） | 尽量发给不同地址 |

**✅ 推荐正文模板：**
```
你好，

[具体内容]

祝好
```

## 注意事项

1. **139 邮箱必须使用 SSL（端口 465）**，不支持 587/STARTTLS。
2. `password` 字段填的是 **POP3/SMTP 授权码**，不是账号登录密码。
3. 如果发送失败返回 `SMTPDataError: (550, ... score is XX)`，说明被反垃圾拦截，参考上方规避指南调整内容或等待 10-15 分钟后重试。

