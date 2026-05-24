---
name: github
description: "GitHub 仓库操作与协作 (Use when: 需要 PR/Issue/Release 操作、CI 查询、仓库管理). NOT for: 本地 git 操作（用 git-ops）."
metadata:
  rooster:
    emoji: "🐙"
    platform: ["any"]
    category: "development"
    requires:
      python_packages: []
      bins: ["gh"]
      env_vars: []
---

# GitHub — 仓库操作与协作

通过 `python_interpreter` 工具调用 `gh` CLI，完成 GitHub 平台级别的操作。

## 适用场景

- **PR 管理**：创建、查看、合并 Pull Request
- **Issue 跟踪**：创建、搜索、关闭 Issue
- **Release 发布**：创建版本发布、上传资产
- **CI/CD 查询**：查看 Actions 运行状态和日志
- **仓库浏览**：查看文件、分支、标签
- **团队协作**：Review 请求、评论、标签管理

## PR 操作模板

```python
import subprocess, json

repo = "owner/repo"

# 创建 PR
result = subprocess.run([
    "gh", "pr", "create",
    "--repo", repo,
    "--title", "feat: add new feature",
    "--body", "## Summary\n- Added X\n- Fixed Y\n\n## Test plan\n- [ ] Unit tests pass",
    "--base", "main",
    "--head", "feature-branch",
], capture_output=True, text=True, encoding="utf-8")
print(result.stdout)  # PR URL

# 查看 PR 列表
result = subprocess.run([
    "gh", "pr", "list", "--repo", repo, "--state", "open", "--json",
    "number,title,author,createdAt", "--limit", "20"
], capture_output=True, text=True, encoding="utf-8")
prs = json.loads(result.stdout)
for pr in prs:
    print(f"#{pr['number']} {pr['title']} (@{pr['author']['login']})")
```

## Issue 操作模板

```python
import subprocess, json

repo = "owner/repo"

# 创建 Issue
result = subprocess.run([
    "gh", "issue", "create",
    "--repo", repo,
    "--title", "Bug: description of the issue",
    "--body", "## Steps to reproduce\n1. ...\n\n## Expected\n...\n\n## Actual\n...",
    "--label", "bug",
    "--assignee", "@me",
], capture_output=True, text=True, encoding="utf-8")
print(result.stdout)

# 搜索 Issue
result = subprocess.run([
    "gh", "issue", "list", "--repo", repo,
    "--search", "is:open label:bug",
    "--json", "number,title,labels", "--limit", "10"
], capture_output=True, text=True, encoding="utf-8")
print(result.stdout)
```

## CI/Actions 查询

```python
import subprocess, json

repo = "owner/repo"

# 查看最近运行
result = subprocess.run([
    "gh", "run", "list", "--repo", repo, "--limit", "5",
    "--json", "status,conclusion,headBranch,createdAt,name"
], capture_output=True, text=True, encoding="utf-8")
runs = json.loads(result.stdout)
for run in runs:
    status = run.get("conclusion") or run.get("status")
    print(f"[{status}] {run['name']} ({run['headBranch']}) - {run['createdAt']}")
```

## 注意事项

1. **认证前提**：需要 `gh auth login` 已完成（检查 `gh auth status`）
2. **JSON 输出**：始终使用 `--json` 参数获取结构化数据，避免解析纯文本
3. **分页控制**：大量数据时使用 `--limit` 控制，默认 30 条
4. **权限范围**：某些操作需要 `repo` 或 `workflow` scope 授权
5. **速率限制**：GitHub API 有频率限制，批量操作需加延迟
