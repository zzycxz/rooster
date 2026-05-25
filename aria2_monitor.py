import os
import sys
import json
import time
import urllib.request
import urllib.error

def load_env():
    # Simple env loader
    base = os.path.dirname(os.path.abspath(__file__))
    for env_name in [".env.local", ".env"]:
        env_path = os.path.join(base, env_name)
        if os.path.exists(env_path):
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.split("#", 1)[0].strip()
                    if "=" in line:
                        k, v = line.split("=", 1)
                        if k not in os.environ:
                            os.environ[k] = v.strip('"').strip("'")

def get_rpc_url():
    port = os.environ.get("ARIA2_RPC_PORT", "6800")
    # Default URL
    url = f"http://localhost:{port}/jsonrpc"
    return url

def get_token():
    return os.environ.get("ARIA2_TOKEN", "") or os.environ.get("ARIA2_RPC_SECRET", "")

def rpc_call(method, params=None):
    if params is None:
        params = []
    url = get_rpc_url()
    token = get_token()
    
    rpc_params = list(params)
    if token:
        rpc_params.insert(0, f"token:{token}")
        
    payload = {
        "jsonrpc": "2.0",
        "id": "monitor",
        "method": method,
        "params": rpc_params
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=2.0) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": {"message": f"Connection failed: {e.reason}"}}
    except Exception as e:
        return {"error": {"message": str(e)}}

def format_size(bytes_val):
    bytes_val = float(bytes_val)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.2f} PB"

def format_speed(bytes_per_sec):
    return f"{format_size(bytes_per_sec)}/s"

def print_ui():
    # ANSI escape codes for coloring
    GREEN = "\033[92m"
    BLUE = "\033[94m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    RED = "\033[91m"
    RESET = "\033[0m"
    BOLD = "\033[1m"
    
    # Clear screen
    os.system('cls' if os.name == 'nt' else 'clear')
    
    print(f"{BOLD}{CYAN}================================================================{RESET}")
    print(f"{BOLD}{CYAN}⚡ Rooster Aria2 Downloader Monitor (Aria2 实时下载监视器) ⚡{RESET}")
    print(f"{BOLD}{CYAN}================================================================{RESET}")
    
    # Check status
    ver_res = rpc_call("aria2.getVersion")
    if "error" in ver_res:
        print(f"\n{RED}❌ Error: 无法连接到 Aria2 RPC 服务。{RESET}")
        print(f"   原因: {ver_res['error']['message']}")
        print(f"   请确保 guardian.py 正在运行，或手动拉起了 aria2c 服务。")
        return False
        
    version = ver_res["result"]["version"]
    print(f"🟢 {GREEN}Aria2 状态: 运行中{RESET} | 版本: {BOLD}{version}{RESET} | 接口: {BOLD}{get_rpc_url()}{RESET}\n")
    
    # Active downloads
    active_res = rpc_call("aria2.tellActive", [["gid", "completedLength", "totalLength", "downloadSpeed", "files", "status"]])
    active = active_res.get("result", [])
    
    print(f"{BOLD}{BLUE}[正在下载任务 (Active): {len(active)}]{RESET}")
    if not active:
        print(f"  {YELLOW}(暂无进行中的下载任务){RESET}")
    else:
        for idx, task in enumerate(active):
            gid = task["gid"]
            completed = int(task["completedLength"])
            total = int(task["totalLength"])
            speed = int(task["downloadSpeed"])
            
            # Extract filename
            filename = "Unknown"
            if task.get("files"):
                first_file = task["files"][0]
                path = first_file.get("path", "")
                if path:
                    filename = os.path.basename(path)
                elif first_file.get("uris"):
                    filename = first_file["uris"][0]["uri"].split("/")[-1].split("?")[0]
            
            percent = (completed / total * 100.0) if total > 0 else 0.0
            
            # Progress bar
            bar_len = 20
            filled_len = int(bar_len * percent / 100.0)
            bar = "█" * filled_len + "░" * (bar_len - filled_len)
            
            # ETA calculation
            eta_str = "N/A"
            if speed > 0 and total > completed:
                eta_secs = (total - completed) / speed
                if eta_secs > 3600:
                    eta_str = f"{int(eta_secs // 3600)}h {int((eta_secs % 3600) // 60)}m"
                elif eta_secs > 60:
                    eta_str = f"{int(eta_secs // 60)}m {int(eta_secs % 60)}s"
                else:
                    eta_str = f"{int(eta_secs)}s"
                    
            print(f"\n{idx+1}. {BOLD}{filename}{RESET}")
            print(f"   📂 任务 GID: {CYAN}{gid}{RESET}")
            print(f"   📊 进度: [{bar}] {BOLD}{percent:.2f}%{RESET} ({format_size(completed)} / {format_size(total)})")
            print(f"   🚀 速度: {GREEN}{format_speed(speed)}{RESET} | ⏳ 剩余时间 (ETA): {YELLOW}{eta_str}{RESET}")

    # Stopped / Completed / Error downloads
    stopped_res = rpc_call("aria2.tellStopped", [0, 5, ["gid", "completedLength", "totalLength", "files", "status", "errorCode"]])
    stopped = stopped_res.get("result", [])
    
    print(f"\n{BOLD}{BLUE}[历史下载记录 (Recently Stopped/Completed): {len(stopped)}]{RESET}")
    if not stopped:
        print(f"  {YELLOW}(暂无历史任务记录){RESET}")
    else:
        for idx, task in enumerate(stopped):
            gid = task["gid"]
            status = task["status"]
            completed = int(task["completedLength"])
            total = int(task["totalLength"])
            
            filename = "Unknown"
            if task.get("files"):
                first_file = task["files"][0]
                path = first_file.get("path", "")
                if path:
                    filename = os.path.basename(path)
            
            status_color = GREEN if status == "complete" else RED
            status_text = "已完成" if status == "complete" else f"失败 (Error {task.get('errorCode', 'Unknown')})"
            
            print(f"   [{status_color}{status_text}{RESET}] {filename} ({format_size(completed)}) | GID: {gid}")
            
    print(f"\n{BOLD}{CYAN}----------------------------------------------------------------{RESET}")
    print(f"💡 {BOLD}网页版监控小贴士 (Premium Recommendation):{RESET}")
    print(f"   我们极其推荐您使用 {BOLD}AriaNg{RESET} 网页版进行完全可视化的进度管理与任务控制！")
    print(f"   只需在浏览器打开: {BOLD}http://ariang.mayswind.net/latest/{RESET}")
    print(f"   并在 【AriaNg 配置】 -> 【RPC】 中设置：")
    print(f"   • RPC 地址: {BOLD}127.0.0.1{RESET} | 端口: {BOLD}{os.environ.get('ARIA2_RPC_PORT', '6800')}{RESET}")
    print(f"   • RPC 密钥: {BOLD}{get_token() if get_token() else '(无)'}{RESET}")
    print(f"   即可秒级连接，直接在网页上拖拽下载、限速、删除任务！")
    print(f"{BOLD}{CYAN}================================================================{RESET}")
    print("按 Ctrl+C 退出监控模式。 (每 2 秒更新一次)")
    return True

if __name__ == "__main__":
    load_env()
    # Enable ANSI escape color codes on Windows 10+
    if os.name == 'nt':
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        
    try:
        while True:
            success = print_ui()
            if not success:
                break
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n已退出监视器。")
