---
name: dev-tools
description: "开发辅助工具集 (Use when: 需要代码格式化/依赖管理/环境检测/进程管理/端口扫描). NOT for: 文件读写（用 FileSystem）."
metadata:
  rooster:
    emoji: "🔧"
    platform: ["any"]
    category: "development"
    requires:
      python_packages: ["psutil"]
      bins: []
      env_vars: []
---

# Dev Tools — 开发辅助工具集

通过 `python_interpreter` 完成开发环境检测、进程管理、依赖安装等常用开发任务。

## 场景 1：检测已安装 Python 包

```python
import importlib.util, subprocess, sys

packages = ["pandas", "httpx", "pydantic", "fastapi", "numpy"]
for pkg in packages:
    spec = importlib.util.find_spec(pkg)
    status = "✅ installed" if spec else "❌ missing"
    print(f"  {pkg}: {status}")
```

## 场景 2：批量安装 Python 依赖

```python
import subprocess, sys

packages = ["pandas", "httpx", "pydantic"]
for pkg in packages:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", pkg, "-q"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"✅ {pkg} 安装成功")
    else:
        print(f"❌ {pkg} 安装失败: {result.stderr[:200]}")
```

## 场景 3：查找和终止进程

```python
import psutil

# 查找占用特定端口的进程
def find_process_by_port(port: int):
    for conn in psutil.net_connections():
        if conn.laddr.port == port:
            try:
                proc = psutil.Process(conn.pid)
                print(f"端口 {port} 被占用: PID={conn.pid}, 名称={proc.name()}")
                return conn.pid
            except psutil.NoSuchProcess:
                pass
    print(f"端口 {port} 空闲")
    return None

find_process_by_port(8080)

# 终止进程（需谨慎！）
# pid = find_process_by_port(8080)
# if pid:
#     psutil.Process(pid).terminate()
```

## 场景 4：系统环境信息

```python
import sys, os, platform, psutil

print(f"Python: {sys.version}")
print(f"OS: {platform.system()} {platform.release()}")
print(f"CPU: {psutil.cpu_count()} 核, 使用率: {psutil.cpu_percent(interval=1)}%")
mem = psutil.virtual_memory()
print(f"内存: 总计 {mem.total//1024//1024}MB, 可用 {mem.available//1024//1024}MB")
disk = psutil.disk_usage(os.getcwd())
print(f"磁盘: 总计 {disk.total//1024//1024//1024}GB, 剩余 {disk.free//1024//1024//1024}GB")
```

## 场景 5：运行子进程并捕获输出

```python
import subprocess

def run_cmd(cmd: str, cwd: str = None, timeout: int = 30) -> tuple:
    """运行命令，返回 (stdout, stderr, returncode)"""
    result = subprocess.run(
        cmd, shell=True, cwd=cwd,
        capture_output=True, text=True,
        encoding="utf-8", timeout=timeout
    )
    return result.stdout, result.stderr, result.returncode

stdout, stderr, code = run_cmd("python --version")
print(f"Output: {stdout.strip()}")
print(f"Return code: {code}")
```

## 场景 6：扫描开放端口

```python
import socket

def scan_ports(host: str = "127.0.0.1", ports: list = None):
    if ports is None:
        ports = [80, 443, 3000, 5000, 8000, 8080, 8765, 8766]
    open_ports = []
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex((host, port)) == 0:
                open_ports.append(port)
    print(f"开放端口: {open_ports}")
    return open_ports

scan_ports()
```

## 注意事项

1. 终止进程操作高危，建议先查询确认再终止
2. `pip install` 会修改当前 Python 环境，虚拟环境内操作更安全
3. Windows 下 `subprocess.run` 使用 `shell=True` 支持管道命令
