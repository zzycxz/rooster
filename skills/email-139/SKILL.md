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

# 📧 email-139 — 邮箱发送组件

通过 SMTP 服务发送电子邮件。账号凭据**禁止硬编码**，必须通过环境变量 `.env.local` 预配置，**无需在调用时传入 smtp_host / username / password**。

## 账号信息（已预配置于本机 .env.local）

| 字段 | 值（读取自本地环境变量，对 Git 隔离） |
|------|-----|
| SMTP 服务器 | 读取 `SMTP_DEFAULT_HOST` |
| 端口 | 读取 `SMTP_DEFAULT_PORT` |
| 发件人 | 读取 `SMTP_DEFAULT_USER` |
| 认证密码 | 读取 `SMTP_DEFAULT_PASS` |

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

## 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `to` | List[str] | ✅ | 收件人邮箱列表 |
| `subject` | str | ✅ | 邮件主题 |
| `body` | str | ✅ | 正文（纯文本） |
| `attachments` | List[str] | ❌ | 附件文件绝对路径列表 |

## 隐私与安全规约

1. **绝对禁止** 在此文档、系统 Prompt 或任何代码文件中明文写出邮箱账号及密码。
2. 所有敏感连接配置全部放置于项目根目录下的 `.env.local` 中。该文件已被 `.gitignore` 屏蔽，不会上传至代码仓库。
3. 如果邮件发送失败（返回未配置密码），请引导用户修改本地 `.env.local`。
