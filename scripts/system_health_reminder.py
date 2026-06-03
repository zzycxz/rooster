#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统健康日志提醒脚本
功能：收集系统信息并生成日志，通过邮件发送提醒
安全注意：请勿在此文件硬编码任何个人邮箱或密码！所有敏感信息从 .env.local 读取。
"""

import psutil
import platform
import datetime
import smtplib
from email.mime.text import MIMEText
from email.header import Header
import json
import os

# 尝试加载环境变量 (需要安装 python-dotenv)
try:
    from dotenv import load_dotenv
    # 优先加载当前目录或上级目录的 .env.local
    load_dotenv(".env.local")
    load_dotenv()
except ImportError:
    pass

# 从环境变量读取敏感配置（请在 .env.local 中配置以下四个变量）
SMTP_SERVER = os.environ.get("SMTP_DEFAULT_HOST", "smtp.139.com")
SMTP_PORT = int(os.environ.get("SMTP_DEFAULT_PORT", "465"))
SENDER_EMAIL = os.environ.get("SMTP_DEFAULT_USER", "")
EMAIL_PASSWORD = os.environ.get("SMTP_DEFAULT_PASS", "")
RECEIVER_EMAIL = os.environ.get("SYSTEM_HEALTH_RECEIVER", SENDER_EMAIL)

LOG_DIR = os.environ.get("SYSTEM_HEALTH_LOG_DIR", os.path.expanduser("~\\Desktop"))
LOG_TXT_FILE = os.path.join(LOG_DIR, "system_health_reminders.log")
LOG_JSON_FILE = os.path.join(LOG_DIR, "system_health_logs.json")

def get_system_health_info():
    """收集系统健康信息"""
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_count = psutil.cpu_count(logical=False)
        cpu_count_logical = psutil.cpu_count(logical=True)
        
        mem = psutil.virtual_memory()
        memory_total = mem.total / (1024**3)
        memory_used = mem.used / (1024**3)
        memory_percent = mem.percent
        
        disk = psutil.disk_usage('/')
        disk_total = disk.total / (1024**3)
        disk_used = disk.used / (1024**3)
        disk_percent = disk.percent
        
        system_info = {
            "平台": platform.system(),
            "版本": platform.version(),
            "主机名": platform.node(),
            "处理器": platform.processor()
        }
        
        health_data = {
            "检查时间": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "CPU使用率": f"{cpu_percent}%",
            "CPU核心数": f"{cpu_count} 物理核心 / {cpu_count_logical} 逻辑核心",
            "内存使用": f"{memory_used:.2f}GB / {memory_total:.2f}GB ({memory_percent}%)",
            "磁盘使用": f"{disk_used:.2f}GB / {disk_total:.2f}GB ({disk_percent}%)",
            "系统信息": system_info
        }
        
        return health_data
        
    except Exception as e:
        return {"错误": f"收集系统信息失败: {str(e)}"}

def generate_health_report(health_data):
    """生成健康报告"""
    report_lines = ["【系统健康日志提醒】", ""]
    
    for key, value in health_data.items():
        if key == "系统信息":
            report_lines.append(f"{key}:")
            for sub_key, sub_value in value.items():
                report_lines.append(f"  - {sub_key}: {sub_value}")
        else:
            report_lines.append(f"{key}: {value}")
    
    report_lines.append("")
    report_lines.append("【建议】")
    
    if "CPU使用率" in health_data:
        cpu_usage = int(health_data["CPU使用率"].replace("%", ""))
        if cpu_usage > 80:
            report_lines.append("- CPU使用率较高，建议检查运行的程序")
    
    if "内存使用" in health_data:
        mem_usage = int(health_data["内存使用"].split("(")[1].replace("%)", ""))
        if mem_usage > 80:
            report_lines.append("- 内存使用率较高，建议关闭不必要的程序")
    
    if "磁盘使用" in health_data:
        disk_usage = int(health_data["磁盘使用"].split("(")[1].replace("%)", ""))
        if disk_usage > 80:
            report_lines.append("- 磁盘空间不足，建议清理垃圾文件")
    
    if not any(f"使用率较高" in s or "空间不足" in s for s in report_lines[-4:]):
        report_lines.append("- 系统运行正常，请继续保持良好的使用习惯")
    
    report_lines.append("")
    report_lines.append("---")
    report_lines.append("Rooster 系统助手自动发送")
    
    return "\n".join(report_lines)

def send_email_reminder(report):
    """发送邮件提醒"""
    try:
        if not EMAIL_PASSWORD or not SENDER_EMAIL:
            with open(LOG_TXT_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n{report}\n")
            return f"邮件未发送（未配置 SMTP_DEFAULT_USER 或 SMTP_DEFAULT_PASS），已保存到日志: {LOG_TXT_FILE}"
        
        message = MIMEText(report, 'plain', 'utf-8')
        message['From'] = Header("Rooster 系统助手", 'utf-8')
        message['To'] = Header("系统管理员", 'utf-8')
        message['Subject'] = Header(f"系统健康日志提醒 - {datetime.datetime.now().strftime('%Y-%m-%d')}", 'utf-8')
        
        smtp = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        if SMTP_PORT in (465, 587):
            # 对于 465 端口的兼容处理（smtplib.SMTP_SSL 更正规，但保留原脚本逻辑）
            if SMTP_PORT == 465:
                smtp = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
            else:
                smtp.starttls()
                
        smtp.login(SENDER_EMAIL, EMAIL_PASSWORD)
        smtp.sendmail(SENDER_EMAIL, [RECEIVER_EMAIL], message.as_string())
        smtp.quit()
        
        return "邮件提醒发送成功"
        
    except Exception as e:
        with open(LOG_TXT_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n{report}\n")
        return f"邮件发送失败: {str(e)}，已保存到日志文件: {LOG_TXT_FILE}"

def save_health_log(health_data):
    """保存健康日志到文件"""
    try:
        logs = []
        if os.path.exists(LOG_JSON_FILE):
            with open(LOG_JSON_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        
        logs.append(health_data)
        logs = logs[-30:]
        
        with open(LOG_JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
            
    except Exception as e:
        print(f"保存日志失败: {str(e)}")

def main():
    """主函数"""
    print(f"[{datetime.datetime.now()}] 开始执行系统健康检查...")
    health_data = get_system_health_info()
    save_health_log(health_data)
    report = generate_health_report(health_data)
    send_result = send_email_reminder(report)
    print(send_result)
    print(f"[{datetime.datetime.now()}] 系统健康检查完成")

if __name__ == "__main__":
    main()
