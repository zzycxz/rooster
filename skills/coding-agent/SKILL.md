---
name: coding-agent
description: "自主编程与代码开发 (Use when: 需要编写/修改/调试代码、创建项目、重构). NOT for: 数据分析、文件格式转换."
metadata:
  rooster:
    emoji: "💻"
    platform: ["any"]
    category: "development"
    requires:
      python_packages: []
      bins: []
      env_vars: []
---

# Coding Agent — 自主编程与代码开发

通过 Executor 的 ReAct 循环和 `python_interpreter` 工具，自主完成代码编写、调试和项目管理任务。

## 适用场景

- 按需求编写新功能代码（Python、JavaScript、Shell 等）
- 修复 Bug：读取错误信息 → 定位代码 → 修改验证
- 代码重构：优化结构、提升可读性
- 创建新项目脚手架（目录结构、配置文件、入口文件）
- 编写和运行单元测试
- 依赖管理：安装、升级、解决冲突

## 编码工作流

```python
import subprocess

# 1. 读取目标文件
with open("src/module.py", "r", encoding="utf-8") as f:
    original = f.read()

# 2. 应用修改
modified = original.replace("old_code", "new_code")

# 3. 写回文件
with open("src/module.py", "w", encoding="utf-8") as f:
    f.write(modified)

# 4. 运行测试验证
result = subprocess.run(
    ["python", "-m", "pytest", "tests/", "-x"],
    capture_output=True, text=True, encoding="utf-8"
)
print(result.stdout)
if result.returncode != 0:
    print("TESTS FAILED:", result.stderr)
```

## 创建新项目模板

```python
import os

project_name = "my_project"
base = os.path.join(os.getcwd(), project_name)

structure = {
    "src/__init__.py": "",
    "src/main.py": 'def main():\n    print("Hello")\n\nif __name__ == "__main__":\n    main()\n',
    "tests/__init__.py": "",
    "tests/test_main.py": "from src.main import main\ndef test_main():\n    main()\n",
    "pyproject.toml": '[project]\nname = "' + project_name + '"\nversion = "0.1.0"\n',
    "README.md": "# " + project_name + "\n",
}

for path, content in structure.items():
    full = os.path.join(base, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
```

## 注意事项

1. **编码一致性**：始终使用 `encoding="utf-8"` 避免中文乱码
2. **原子写入**：先写临时文件再 rename，防止写入中断导致文件损坏
3. **测试先行**：修改代码后立即运行相关测试
4. **依赖检查**：安装新包后确认 `pyproject.toml` 同步更新
5. **路径安全**：不在用户未授权的目录外创建或删除文件
