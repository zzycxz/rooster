# -*- coding: utf-8 -*-
import os
import sys

# Set up python paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from toolset.registry import global_tool_registry
from agents.prompt_builder import PromptBuilder


def main():
    loader = PromptBuilder().skill_loader
    skills = loader.skills
    tools = global_tool_registry._tools

    print(f"📊 Total High-Level Skills Loaded: {len(skills)}")
    print(f"🛠️ Total Low-Level Atomic Tools Registered: {len(tools)}")
    print("-" * 60)

    print("\n--- SKILLS LIST ---")
    for name, skill in skills.items():
        print(f"🧩 [{skill.name}] (Category: {skill.category})")
        print(f"   Description: {skill.description}")
        print(f"   File: {skill.full_path}")

    print("\n--- TOOLS LIST ---")
    for name, tool in tools.items():
        print(f"🔧 [{tool.name}] (Kit: {getattr(tool, 'kit', 'General')})")
        print(f"   Description: {tool.description}")


if __name__ == "__main__":
    main()
