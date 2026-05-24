---
name: git-ops
description: "Git 版本控制操作 (Use when: 需要 clone/commit/push/diff/log 等 git 操作). NOT for: 非版本控制文件操作."
metadata:
  rooster:
    emoji: "🌿"
    platform: ["windows", "any"]
    category: "development"
    requires:
      python_packages: []
      bins: ["git"]
      env_vars: []
---

# Git Operations — Git 版本控制

通过 `python_interpreter` 工具执行 git 命令，完成版本控制相关操作。

## 常用场景

- **克隆仓库**: `git clone https://github.com/user/repo.git ./target_dir`
- **查看状态**: `git status`
- **提交变更**: `git add . && git commit -m "message"`
- **推送代码**: `git push origin main`
- **查看历史**: `git log --oneline -20`
- **查看差异**: `git diff HEAD~1`
- **创建分支**: `git checkout -b feature/new-feature`
- **合并分支**: `git merge feature/new-feature`

## 执行模板

```python
import subprocess
result = subprocess.run(
    ["git", "status"],
    cwd="/path/to/repo",     # 必须指定工作目录
    capture_output=True,
    text=True,
    encoding="utf-8"
)
print(result.stdout)
if result.returncode != 0:
    print("ERROR:", result.stderr)
```

## 注意事项

1. **始终指定 `cwd`**：不指定工作目录会在错误路径执行
2. **SSH vs HTTPS**：优先使用 HTTPS，避免 SSH key 问题
3. **身份配置**：首次提交前确认 `git config user.name/email`
4. **大文件**：超过 100MB 的文件需使用 git-lfs
5. **Windows 路径**：使用 `r"C:\path"` 或正斜杠 `"C:/path"`

## 批量操作示例

```python
import subprocess, os

repo_path = r"C:\Users\user\Desktop\my-project"
commands = [
    ["git", "add", "."],
    ["git", "commit", "-m", "Auto commit by Rooster"],
    ["git", "push", "origin", "main"],
]
for cmd in commands:
    r = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True, encoding="utf-8")
    print(f"$ {' '.join(cmd)}")
    print(r.stdout or r.stderr)
    if r.returncode != 0:
        print("⚠️ Command failed, stopping.")
        break
```
