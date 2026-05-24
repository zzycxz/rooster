import os
import smtplib
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field
from toolset.base import BaseTool


class EmailSendArgs(BaseModel):
    smtp_host: str = Field(description="SMTP 服务器地址，如 smtp.qq.com")
    smtp_port: int = Field(465, description="SMTP 端口，SSL 通常为 465，STARTTLS 通常为 587")
    username: str = Field(description="发件人邮箱地址")
    password: str = Field(description="邮箱密码或应用专用密码")
    to: List[str] = Field(description="收件人邮箱列表")
    subject: str = Field(description="邮件主题")
    body: str = Field(description="邮件正文（纯文本）")
    attachments: List[str] = Field(default_factory=list, description="附件文件绝对路径列表")
    use_ssl: bool = Field(True, description="是否使用 SSL 加密连接")


class EmailSendTool(BaseTool):
    """SMTP 邮件发送工具 — 支持纯文本、附件、SSL/STARTTLS"""

    name: str = "email_send"
    kit: str = "Comms"
    description: str = (
        "Send an email via SMTP with optional file attachments. "
        "Supports SSL (port 465) and STARTTLS (port 587). "
        "Requires smtp_host, username, password, to, subject, body."
    )
    domain: str = "comms"
    risk_level: str = "medium"
    reversible: bool = False
    args_schema: Optional[type] = EmailSendArgs

    async def run(self, **kwargs) -> str:
        smtp_host = kwargs.get("smtp_host")
        smtp_port = kwargs.get("smtp_port", 465)
        username = kwargs.get("username")
        password = kwargs.get("password")
        to = kwargs.get("to", [])
        subject = kwargs.get("subject", "")
        body = kwargs.get("body", "")
        attachments = kwargs.get("attachments", [])
        use_ssl = kwargs.get("use_ssl", True)

        # Try to read default values from environment variables
        # 尝试从环境变量读取默认值
        if not smtp_host:
            smtp_host = os.getenv("SMTP_DEFAULT_HOST", "")
        if not username:
            username = os.getenv("SMTP_DEFAULT_USER", "")
        if not password:
            password = os.getenv("SMTP_DEFAULT_PASS", "")

        if not smtp_host or not username or not password:
            return "Error: smtp_host, username, and password are required (or set SMTP_DEFAULT_* in .env)."
        if not to:
            return "Error: at least one recipient ('to') is required."

        try:
            # Execute blocking SMTP operation in thread pool
            # 在线程池中执行阻塞的 SMTP 操作
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._send, smtp_host, smtp_port, username, password, to, subject, body, attachments, use_ssl
            )
            return result
        except Exception as e:
            return f"Email Send Error: {type(e).__name__}: {e}"

    def _send(self, host, port, username, password, to, subject, body, attachments, use_ssl):
        msg = MIMEMultipart()
        msg["From"] = username
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        # Add attachments
        # 添加附件
        attached_count = 0
        for filepath in attachments:
            p = Path(filepath)
            if not p.exists():
                continue
            with open(p, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={p.name}")
            msg.attach(part)
            attached_count += 1

        # Send
        # 发送
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()

        try:
            server.login(username, password)
            server.sendmail(username, to, msg.as_string())
        finally:
            server.quit()

        return f"Email sent successfully to {len(to)} recipient(s): {', '.join(to)}. Attachments: {attached_count}."
