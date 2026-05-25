# guardian.py
"""
Immortal Guardian — external watchdog process.

Responsibilities:
1. Launch and monitor the Rooster child process (src/main.py).
2. Detect crashes, hangs, and resource overflows via heartbeat + resource monitoring.
3. Auto-recover: install missing packages, clean port conflicts.
4. Alert via webhook and exit safely when retry limits are reached.
"""

import subprocess
import sys
import os
import time
import re
import logging
import json
import http.client
import signal
import threading
import hashlib
import random
import platform
from datetime import datetime
from typing import Optional, Dict, Any, List

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False

# 安全自愈允许安装的包白名单，防止利用 stderr 注入下载恶意包
_ALLOWED_PACKAGES = frozenset({
    "requests", "httpx", "aiohttp",
    "pandas", "numpy", "scipy",
    "openpyxl", "xlrd", "xlwt",
    "pillow", "matplotlib",
    "beautifulsoup4", "lxml",
    "pydantic", "pyyaml", "toml",
    "python-docx", "pypdf2", "reportlab",
    "rich", "tqdm", "psutil"
})

# Load .env and .env.local
def load_env():
    base = os.path.dirname(__file__)

    # Load .env.local first (secrets, higher priority)
    env_local = os.path.join(base, ".env.local")
    if os.path.exists(env_local):
        with open(env_local, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.split("#", 1)[0].strip()  # strip inline comments
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    if k not in os.environ:
                        os.environ[k] = v.strip('"').strip("'")

    # Load .env (defaults, lower priority)
    env_path = os.path.join(base, ".env")
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.split("#", 1)[0].strip()  # strip inline comments
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    if k not in os.environ:
                        os.environ[k] = v.strip('"').strip("'")

load_env()

# ── Directories / Logging ────────────────────────────────────────────────────
_ROOT_DIR           = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR           = os.path.join(_ROOT_DIR, ".rooster")
_LOG_DIR            = os.path.join(_DATA_DIR, "logs")
_PIDFILE            = os.path.join(_DATA_DIR, "guardian.pid")
_STATUS_FILE        = os.path.join(_DATA_DIR, "guardian_status.json")
os.makedirs(_LOG_DIR, exist_ok=True)
os.makedirs(_DATA_DIR, exist_ok=True)
_stream = sys.stdout
if hasattr(_stream, "reconfigure"):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [GUARDIAN] - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(_LOG_DIR, "guardian.log"), encoding='utf-8'),
        logging.StreamHandler(_stream),
    ]
)
logger = logging.getLogger(__name__)

# ── Configuration (all overridable via env vars) ─────────────────────────────
MAX_FIX_RETRIES         = int(os.environ.get("GUARDIAN_MAX_RETRIES", "3"))

# Heartbeat
_HEARTBEAT_INTERVAL     = int(os.environ.get("GUARDIAN_HEARTBEAT_INTERVAL", "30"))
_HEARTBEAT_TIMEOUT      = int(os.environ.get("GUARDIAN_HEARTBEAT_TIMEOUT",  "10"))
_HEARTBEAT_MAX_FAILS    = int(os.environ.get("GUARDIAN_HEARTBEAT_MAX_FAILS", "3"))
_STARTUP_GRACE          = int(os.environ.get("GUARDIAN_STARTUP_GRACE",      "60"))

# Restart throttle
_RESTART_WINDOW         = int(os.environ.get("GUARDIAN_RESTART_WINDOW",          "300"))
_MAX_RESTARTS_IN_WINDOW = int(os.environ.get("GUARDIAN_MAX_RESTARTS_IN_WINDOW",  "5"))

# Resource monitoring (requires psutil)
_RES_CPU_LIMIT_PCT      = float(os.environ.get("GUARDIAN_CPU_LIMIT",             "95"))
_RES_MEM_LIMIT_MB       = float(os.environ.get("GUARDIAN_MEM_LIMIT_MB",         "2048"))
_RES_CHECK_INTERVAL     = int(os.environ.get("GUARDIAN_RES_CHECK_INTERVAL",      "15"))
_RES_VIOLATION_DURATION = int(os.environ.get("GUARDIAN_RES_VIOLATION_DURATION", "120"))

# Alerts (Webhook: Feishu / DingTalk / Slack compatible JSON)
_WEBHOOK_URL            = os.environ.get("GUARDIAN_WEBHOOK_URL", "")

class Guardian:
    def __init__(self, target_script: str = "src/main.py"):
        self.target_script = target_script
        self.retry_count = 0
        self._shutdown = False
        self._child: Optional[subprocess.Popen] = None
        # Circuit breaker: stop retrying after N consecutive identical errors
        self._last_error_hash: Optional[str] = None
        self._same_error_streak: int = 0
        self._same_error_limit: int = 2
        # Heartbeat
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_fails: int = 0
        self._heartbeat_killed: bool = False
        self._child_start_time: float = 0.0
        # Resource monitoring
        self._resource_thread: Optional[threading.Thread] = None
        self._resource_killed: bool = False
        # Restart throttle window
        self._restart_timestamps: List[float] = []
        # Schedule trigger thread
        self._schedule_thread: Optional[threading.Thread] = None
        # Port (from env, consistent with launcher.py)
        self._gateway_port = int(os.environ.get("GATEWAY_PORT", "8765"))
        # Register signal handlers
        signal.signal(signal.SIGINT, self._on_signal)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, self._on_signal)

    # ------------------------------------------------------------------
    # Signals / graceful shutdown
    # ------------------------------------------------------------------
    def _on_signal(self, signum, frame):
        """SIGINT / SIGTERM → mark shutdown + gracefully terminate child."""
        logger.warning(f"⚡ Received signal {signum}, shutting down gracefully...")
        self._shutdown = True
        self._graceful_kill_child()

    def _graceful_kill_child(self, timeout: int = 8):
        """SIGTERM first, wait for grace period, then SIGKILL the entire process tree."""
        if not self._child or self._child.poll() is not None:
            return
        try:
            self._child.terminate()
            try:
                self._child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning("⚠️ Child did not exit within grace period, force-killing process tree.")
                self._kill_process_tree(self._child.pid)
        except Exception as e:
            logger.error(f"Error killing child process: {e}")

    def _write_status_json(self):
        """Serialize current Guardian state to JSON for dashboard consumption."""
        child_alive = self._child is not None and self._child.poll() is None
        uptime = time.time() - self._child_start_time if child_alive and self._child_start_time else 0
        status = {
            "alive": True,
            "pid": os.getpid(),
            "child_pid": self._child.pid if child_alive else None,
            "child_alive": child_alive,
            "child_uptime_sec": round(uptime, 1),
            "retry_count": self.retry_count,
            "max_retries": MAX_FIX_RETRIES,
            "heartbeat_fails": self._heartbeat_fails,
            "circuit_breaker_streak": self._same_error_streak,
            "restart_count": len(self._restart_timestamps),
            "gateway_port": self._gateway_port,
            "timestamp": time.time(),
        }
        try:
            with open(_STATUS_FILE, 'w', encoding='utf-8') as f:
                json.dump(status, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _kill_process_tree(self, pid: int):
        """Recursively kill process tree (parent + all descendants)."""
        if _PSUTIL_AVAILABLE:
            try:
                parent = psutil.Process(pid)
                for child in parent.children(recursive=True):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                parent.kill()
                return
            except psutil.NoSuchProcess:
                return
            except Exception:
                pass
        if platform.system() == "Windows":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10
            )
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Single-instance mutex (PID file)
    # ------------------------------------------------------------------
    def _acquire_pidfile(self) -> bool:
        """Write PID file; return False if another Guardian is already running."""
        if os.path.exists(_PIDFILE):
            try:
                with open(_PIDFILE, 'r') as f:
                    old_pid = int(f.read().strip())
                alive = False
                if _PSUTIL_AVAILABLE:
                    alive = psutil.pid_exists(old_pid)
                elif platform.system() != "Windows":
                    try:
                        os.kill(old_pid, 0)
                        alive = True
                    except (OSError, ProcessLookupError):
                        pass
                if alive:
                    logger.critical(
                        f"🚫 Another Guardian is already running (PID={old_pid}), exiting."
                    )
                    return False
            except (ValueError, IOError):
                pass
        try:
            with open(_PIDFILE, 'w') as f:
                f.write(str(os.getpid()))
        except Exception as e:
            logger.warning(f"⚠️ Cannot write PID file: {e}")
        return True

    def _release_pidfile(self):
        """Clean up PID file (only delete if we own it)."""
        try:
            if os.path.exists(_PIDFILE):
                with open(_PIDFILE, 'r') as f:
                    stored_pid = int(f.read().strip())
                if stored_pid == os.getpid():
                    os.remove(_PIDFILE)
        except Exception as e:
            logger.warning(f"⚠️ Error releasing PID file: {e}")

    # ------------------------------------------------------------------
    # Pre-start cleanup
    # ------------------------------------------------------------------
    def _cleanup_on_start(self):
        """On Guardian start, proactively clean any leftover processes on the monitored port."""
        logger.info(f"🔍 Checking port {self._gateway_port} for leftovers...")
        self._free_port(f"Address already in use: ('0.0.0.0', {self._gateway_port})")
        # Auto-start aria2c daemon if not already running
        self._ensure_aria2_running()

    def _ensure_aria2_running(self):
        """Check if aria2c RPC is responding; if not, launch it as a daemon."""
        aria2_port = int(os.environ.get("ARIA2_RPC_PORT", "6800"))
        # Quick connectivity check
        try:
            conn = http.client.HTTPConnection("127.0.0.1", aria2_port, timeout=2)
            conn.request("POST", "/jsonrpc",
                body=b'{"jsonrpc":"2.0","id":"ping","method":"aria2.getVersion","params":[]}',
                headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            conn.close()
            if resp.status < 400:
                logger.info(f"✅ aria2c already running on port {aria2_port}.")
                return
        except Exception:
            pass

        logger.info(f"⬇️ aria2c not detected, launching daemon on port {aria2_port}...")
        try:
            # Find aria2c binary
            import shutil
            aria2c_bin = shutil.which("aria2c")
            if not aria2c_bin:
                logger.warning("⚠️ aria2c not found in PATH, skipping auto-launch.")
                return
            cmd = [
                aria2c_bin,
                "--enable-rpc",
                "--rpc-listen-all=true",
                f"--rpc-listen-port={aria2_port}",
                "--rpc-allow-origin-all",
                "--daemon",
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            time.sleep(1)
            logger.info(f"✅ aria2c daemon started on port {aria2_port}.")
        except Exception as e:
            logger.warning(f"⚠️ Failed to auto-start aria2c: {e}")

    # ------------------------------------------------------------------
    # Exponential backoff
    # ------------------------------------------------------------------
    def _backoff_sleep(self, attempt: int):
        """Exponential backoff with random jitter (cap 30s)."""
        base = min(2 ** attempt, 30)
        jitter = random.uniform(0, base * 0.2)
        delay = base + jitter
        logger.info(f"⏳ Backing off {delay:.1f}s before restart...")
        time.sleep(delay)

    # ------------------------------------------------------------------
    # Error fingerprinting for circuit breaker
    # ------------------------------------------------------------------
    def _hash_error(self, traceback_msg: str) -> str:
        """Hash the last 3 lines of a traceback as an error fingerprint."""
        lines = [l for l in traceback_msg.splitlines() if l.strip()]
        key = "\n".join(lines[-3:]) if len(lines) >= 3 else traceback_msg
        return hashlib.md5(key.encode()).hexdigest()

    # ------------------------------------------------------------------
    # Restart throttle (sliding window)
    # ------------------------------------------------------------------
    def _check_restart_throttle(self) -> bool:
        """
        Sliding window: block if more than _MAX_RESTARTS_IN_WINDOW restarts
        within _RESTART_WINDOW seconds. Returns True = allow restart.
        """
        now = time.time()
        cutoff = now - _RESTART_WINDOW
        self._restart_timestamps = [t for t in self._restart_timestamps if t > cutoff]
        if len(self._restart_timestamps) >= _MAX_RESTARTS_IN_WINDOW:
            logger.critical(
                f"🌊 Restart storm intercepted: {len(self._restart_timestamps)} restarts "
                f"in {_RESTART_WINDOW}s (limit {_MAX_RESTARTS_IN_WINDOW}), stopping."
            )
            return False
        self._restart_timestamps.append(now)
        return True

    # ------------------------------------------------------------------
    # Stream relay (runs in background thread)
    # ------------------------------------------------------------------
    @staticmethod
    def _relay_stream(
        src,
        dst,
        collector: Optional[List[str]]
    ):
        """Read src line-by-line, write to dst; optionally collect into list."""
        try:
            for line in src:
                dst.write(line)
                dst.flush()
                if collector is not None:
                    collector.append(line)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------
    def _heartbeat_loop(self):
        """Background thread: after startup grace, periodically HTTP GET /api/health."""
        grace_start = time.time()
        while not self._shutdown and (time.time() - grace_start) < _STARTUP_GRACE:
            if self._child is None or self._child.poll() is not None:
                return
            time.sleep(5)

        while not self._shutdown:
            if self._child is None or self._child.poll() is not None:
                return
            time.sleep(_HEARTBEAT_INTERVAL)
            if self._child is None or self._child.poll() is not None:
                return

            ok = False
            try:
                conn = http.client.HTTPConnection(
                    "127.0.0.1", self._gateway_port, timeout=_HEARTBEAT_TIMEOUT
                )
                conn.request("GET", "/api/health")
                resp = conn.getresponse()
                conn.close()
                ok = resp.status < 500
            except Exception:
                pass

            if ok:
                self._heartbeat_fails = 0
                continue

            self._heartbeat_fails += 1
            logger.warning(
                f"💓 Heartbeat failed ({self._heartbeat_fails}/{_HEARTBEAT_MAX_FAILS}), "
                f"port {self._gateway_port} not responding."
            )
            if self._heartbeat_fails >= _HEARTBEAT_MAX_FAILS:
                logger.error("💀 Heartbeat failed consecutively, force-killing child.")
                self._heartbeat_killed = True
                self._graceful_kill_child()
                return

    # ------------------------------------------------------------------
    # Resource monitoring
    # ------------------------------------------------------------------
    def _resource_monitor_loop(self):
        """Background thread: monitor CPU / memory, restart if sustained overflow (requires psutil)."""
        if not _PSUTIL_AVAILABLE:
            logger.warning("⚠️ psutil not installed, skipping resource monitoring (pip install psutil).")
            return

        cpu_since: Optional[float] = None
        mem_since: Optional[float] = None

        while not self._shutdown:
            time.sleep(_RES_CHECK_INTERVAL)
            if self._child is None or self._child.poll() is not None:
                return
            try:
                proc    = psutil.Process(self._child.pid)
                cpu_pct = proc.cpu_percent(interval=1)
                mem_mb  = proc.memory_info().rss / 1024 / 1024
            except psutil.NoSuchProcess:
                return
            except Exception as e:
                logger.debug(f"Resource sampling failed: {e}")
                continue

            now = time.time()

            if cpu_pct > _RES_CPU_LIMIT_PCT:
                if cpu_since is None:
                    cpu_since = now
                elif now - cpu_since >= _RES_VIOLATION_DURATION:
                    logger.error(
                        f"🔥 CPU above {_RES_CPU_LIMIT_PCT}% for {_RES_VIOLATION_DURATION}s "
                        f"(current {cpu_pct:.1f}%), force-restarting."
                    )
                    self._resource_killed = True
                    self._graceful_kill_child()
                    return
            else:
                cpu_since = None

            if mem_mb > _RES_MEM_LIMIT_MB:
                if mem_since is None:
                    mem_since = now
                elif now - mem_since >= _RES_VIOLATION_DURATION:
                    logger.error(
                        f"💾 Memory above {_RES_MEM_LIMIT_MB}MB for {_RES_VIOLATION_DURATION}s "
                        f"(current {mem_mb:.1f}MB), force-restarting."
                    )
                    self._resource_killed = True
                    self._graceful_kill_child()
                    return
            else:
                mem_since = None

    # ------------------------------------------------------------------
    # Port conflict auto-cleanup
    # ------------------------------------------------------------------
    def _free_port(self, traceback_msg: str) -> bool:
        """Extract port number from error message and kill the process holding it."""
        port_match = re.search(r':(\d+)[\]\'")\s]*[,\s]*Address already in use', traceback_msg)
        if not port_match:
            port_match = re.search(r"127\.0\.0\.1:(\d+)", traceback_msg)
        if not port_match:
            port_match = re.search(r"0\.0\.0\.0:(\d+)", traceback_msg)
        if not port_match:
            port_match = re.search(r"['\"]?\d+\.\d+\.\d+\.\d+['\"]?\s*,\s*(\d+)", traceback_msg)
        if not port_match:
            logger.error("🔌 Could not extract port number from error message.")
            return False

        port = port_match.group(1)
        logger.info(f"🔌 Attempting to free port {port}...")

        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["netstat", "-ano"], capture_output=True, timeout=10
                )
                stdout_str = ""
                if result.stdout:
                    try:
                        stdout_str = result.stdout.decode("utf-8")
                    except UnicodeDecodeError:
                        stdout_str = result.stdout.decode("gbk", errors="ignore")
                pids = set()
                for line in stdout_str.splitlines():
                    if f":{port}" in line and "LISTENING" in line:
                        parts = line.split()
                        if parts:
                            pids.add(parts[-1])
                if not pids:
                    logger.info(f"🔌 Port {port} is already free.")
                    return True
                for pid in pids:
                    logger.info(f"🔫 Killing process PID={pid} on port {port}")
                    subprocess.run(
                        ["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=10
                    )
            else:
                result = subprocess.run(
                    ["lsof", "-ti", f":{port}"], capture_output=True, timeout=10
                )
                stdout_str = ""
                if result.stdout:
                    try:
                        stdout_str = result.stdout.decode("utf-8")
                    except UnicodeDecodeError:
                        stdout_str = result.stdout.decode("gbk", errors="ignore")
                pids = stdout_str.strip().splitlines()
                if not pids:
                    logger.info(f"🔌 Port {port} is already free.")
                    return True
                for pid in pids:
                    logger.info(f"🔫 Killing process PID={pid.strip()} on port {port}")
                    subprocess.run(
                        ["kill", "-9", pid.strip()], capture_output=True, timeout=10
                    )
            time.sleep(2)
            logger.info(f"✅ Port {port} cleaned up.")
            return True
        except Exception as e:
            logger.error(f"❌ Port cleanup failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Alert notification
    # ------------------------------------------------------------------
    def _notify_critical(self, message: str):
        """Send alert via Webhook (Feishu/DingTalk/Slack); silent if GUARDIAN_WEBHOOK_URL is not set."""
        if not _WEBHOOK_URL:
            return
        try:
            from urllib.parse import urlparse
            parsed = urlparse(_WEBHOOK_URL)
            host   = parsed.netloc
            path   = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            payload = json.dumps({
                "msg_type": "text",
                "content": {"text": f"[Rooster Guardian] {message}"},
            })
            if parsed.scheme == "https":
                conn = http.client.HTTPSConnection(host, timeout=5)
            else:
                conn = http.client.HTTPConnection(host, timeout=5)
            conn.request("POST", path, payload, {"Content-Type": "application/json"})
            conn.getresponse()
            conn.close()
        except Exception as e:
            logger.debug(f"Webhook send failed (non-fatal): {e}")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def start(self):
        """Single-instance mutex → pre-start cleanup → enter watchdog loop."""
        if not self._acquire_pidfile():
            return
        try:
            self._cleanup_on_start()
            self._run_loop()
        finally:
            self._release_pidfile()

    def _run_loop(self):
        """Start-monitor-repair loop (with heartbeat / resource monitoring / throttle)."""
        while not self._shutdown and self.retry_count < MAX_FIX_RETRIES:
            # Restart throttle
            if not self._check_restart_throttle():
                self._notify_critical(
                    f"Restart storm: more than {_MAX_RESTARTS_IN_WINDOW} restarts "
                    f"in {_RESTART_WINDOW}s, stopping."
                )
                break

            logger.info(
                f"🚀 Starting child process: {self.target_script} "
                f"(attempt {self.retry_count + 1}/{MAX_FIX_RETRIES})"
            )

            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "ROOSTER_GUARDIAN_MODE": "true"}
            popen_kwargs: Dict[str, Any] = dict(
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                bufsize=1,
                env=env,
            )
            if platform.system() != "Windows":
                popen_kwargs["start_new_session"] = True
            self._child = subprocess.Popen(
                [sys.executable, self.target_script], **popen_kwargs
            )
            self._child_start_time = time.time()
            self._heartbeat_killed = False
            self._resource_killed  = False
            self._heartbeat_fails  = 0

            # Start heartbeat + resource monitoring + schedule trigger threads
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop, daemon=True
            )
            self._resource_thread = threading.Thread(
                target=self._resource_monitor_loop, daemon=True
            )
            if self._schedule_thread is None or not self._schedule_thread.is_alive():
                self._schedule_thread = threading.Thread(
                    target=self._schedule_loop, daemon=True
                )
                self._schedule_thread.start()
            self._heartbeat_thread.start()
            self._resource_thread.start()

            self._write_status_json()

            # Relay stdout / collect stderr in background threads
            stderr_lines: List[str] = []
            t_out = threading.Thread(
                target=self._relay_stream,
                args=(self._child.stdout, sys.stdout, None),
                daemon=True
            )
            t_err = threading.Thread(
                target=self._relay_stream,
                args=(self._child.stderr, sys.stderr, stderr_lines),
                daemon=True
            )
            t_out.start()
            t_err.start()

            self._child.wait()
            t_out.join(timeout=5)
            t_err.join(timeout=5)

            exit_code = self._child.returncode
            stderr_output = "".join(stderr_lines)

            if self._shutdown:
                logger.info("🛑 Guardian received shutdown signal, exiting main loop.")
                break

            self._write_status_json()

            # Heartbeat or resource overflow restarts (don't count toward repair limit)
            if self._heartbeat_killed:
                logger.warning("💓 Heartbeat timeout restart (not counted toward repair limit).")
                self._backoff_sleep(1)
                continue
            if self._resource_killed:
                logger.warning("📊 Resource overflow restart (not counted toward repair limit).")
                self._backoff_sleep(1)
                continue

            if exit_code == 0:
                logger.info("✅ Child process exited normally (config change restart).")
                self._backoff_sleep(2)
                continue
                break

            logger.error(f"❌ Child process crashed (Exit Code: {exit_code})")
            logger.error(f"📄 Error traceback:\n{stderr_output}")

            # ---- Circuit breaker ----
            err_hash = self._hash_error(stderr_output)
            if err_hash == self._last_error_hash:
                self._same_error_streak += 1
            else:
                self._same_error_streak = 0
                self._last_error_hash = err_hash

            if self._same_error_streak >= self._same_error_limit:
                msg = (
                    f"Circuit breaker triggered: same error occurred "
                    f"{self._same_error_streak + 1} times consecutively, self-heal failed."
                )
                logger.critical(f"🔌 {msg}")
                self._notify_critical(msg)
                break

            # ---- Enter repair ----
            if self._attempt_repair(stderr_output):
                self.retry_count += 1
                logger.info("🔄 Repair attempt complete, restarting...")
                self._backoff_sleep(self.retry_count)
            else:
                logger.error("🚫 Unrecognized error, manual intervention required.")
                self._notify_critical(f"Repair failed, manual intervention needed.\nError: {stderr_output[:500]}")
                break

        if self.retry_count >= MAX_FIX_RETRIES:
            msg = f"Max repair attempts reached ({MAX_FIX_RETRIES}), self-heal failed."
            logger.critical(f"🚨 {msg}")
            self._notify_critical(msg)

    def _attempt_repair(self, traceback_msg: str) -> bool:
        """Core repair logic: only handle auto-fixable known error types."""
        # 1. Missing package → pip install
        if "ModuleNotFoundError" in traceback_msg or "No module named" in traceback_msg:
            return self._fix_missing_package(traceback_msg)

        # 2. Port conflict → auto-cleanup
        if "Address already in use" in traceback_msg or "WinError 10048" in traceback_msg:
            return self._free_port(traceback_msg)

        # 3. Everything else → alert and wait for human
        logger.error("🚫 Cannot auto-fix this error, manual intervention required.")
        self._notify_critical(f"Guardian: unfixable crash\n{traceback_msg[:500]}")
        return False

    def _fix_missing_package(self, traceback_msg: str) -> bool:
        match = re.search(r"No module named '([^']+)'", traceback_msg)
        if match:
            pkg = match.group(1).strip()
            # 严格安全过滤：仅允许安装白名单内的依赖包
            top_pkg = pkg.split('.')[0]
            if top_pkg not in _ALLOWED_PACKAGES:
                logger.critical(
                    f"🛡️ [Security Sentinel] 拦截到未授权的自动依赖安装请求：'{pkg}'。 "
                    f"此包未在安全白名单内，已拒绝安装以防范任意包注入漏洞。"
                )
                return False

            logger.warning(f"🔧 Missing package '{pkg}', installing via pip...")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
                return True
            except Exception as e:
                logger.error(f"❌ pip install failed: {e}")
        return False

    # ------------------------------------------------------------------
    # Schedule trigger thread
    # ------------------------------------------------------------------
    def _schedule_loop(self):
        """Background thread: check .rooster/schedules.json every 60s and fire due tasks."""
        schedules_path = os.path.join(_DATA_DIR, "schedules.json")
        last_fire: Dict[str, str] = {}  # schedule_id -> "YYYY-MM-DD HH:MM"

        while not self._shutdown:
            try:
                if not os.path.exists(schedules_path):
                    time.sleep(60)
                    continue

                with open(schedules_path, "r", encoding="utf-8") as f:
                    schedules = json.load(f)

                now = datetime.now()
                now_hhmm = now.strftime("%H:%M")
                now_date = now.strftime("%Y-%m-%d")

                for entry in schedules:
                    if not entry.get("enabled", True):
                        continue

                    sid = entry.get("id", "")
                    freq = entry.get("frequency", "daily")
                    target_time = entry.get("time", "08:00")
                    task_text = entry.get("task", "")
                    session_id = entry.get("session_id", "")

                    should_fire = False
                    fire_key = ""

                    if freq == "daily" and target_time == now_hhmm:
                        fire_key = f"{now_date} {now_hhmm}"
                    elif freq == "hourly" and now.minute == 0:
                        fire_key = f"{now_date} {now_hhmm}"
                    elif freq == "weekly" and now.weekday() == 0 and target_time == now_hhmm:
                        fire_key = f"{now_date} {now_hhmm}"

                    if fire_key and last_fire.get(sid) != fire_key:
                        should_fire = True
                        last_fire[sid] = fire_key

                    if should_fire:
                        logger.info(f"⏰ [Schedule] 触发定时任务: {sid} — {task_text[:80]}")
                        self._fire_scheduled_task(task_text, session_id)

            except Exception as e:
                logger.warning(f"⚠️ [Schedule] 调度检查失败: {e}")

            time.sleep(60)

    def _fire_scheduled_task(self, task_text: str, session_id: str):
        """通过 Gateway HTTP API 触发定时任务。"""
        try:
            port = self._gateway_port
            payload = json.dumps({
                "message": task_text,
                "session_id": session_id or "scheduled",
            }).encode("utf-8")
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("POST", "/api/chat", body=payload, headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            logger.info(f"⏰ [Schedule] 任务触发完成: HTTP {resp.status}")
            conn.close()
        except Exception as e:
            logger.warning(f"⚠️ [Schedule] 任务触发失败: {e}")

if __name__ == "__main__":
    guardian = Guardian()
    guardian.start()
