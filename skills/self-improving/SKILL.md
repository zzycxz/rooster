---
name: self-improving
description: "Capture learnings, errors, and corrections from conversations for continuous self-improvement. Use when: completing a complex task, encountering an error and fixing it, learning a user preference, or discovering a better approach."
metadata:
  rooster:
    emoji: "🧠"
    category: "meta"
    platform: ["any"]
    requires:
      python_packages: []
      bins: []
      env_vars: []
---

# Self-Improving Agent

Automatically capture insights from task execution to improve future performance.

## When to Record Learnings

Record a learning when:

1. **Error → Fix pattern**: You encountered an error and found a working solution
2. **User correction**: The user corrected your approach or output
3. **Preference discovery**: You learned the user prefers a specific style, format, or tool
4. **Optimization found**: You discovered a faster or better way to accomplish something
5. **Tool discovery**: You found a tool combination that works well for a specific domain

## How to Record

Use the `memory_add_fact` tool to store learnings as structured facts:

```
Type: learning
Content: "When doing X, prefer Y because Z"
Confidence: high|medium|low
```

## Categories

- **tool-pattern**: Effective tool combinations or sequences
- **error-recipe**: Common errors and their fixes
- **user-preference**: User-specific preferences (style, format, language)
- **performance**: Faster approaches or shortcuts discovered
- **domain-knowledge**: Facts learned about the user's project/domain

## Anti-Patterns (Do NOT Record)

- Obvious or trivial facts
- Temporary state (file paths, current date)
- Speculative guesses without evidence
- Duplicate or contradictory existing learnings
