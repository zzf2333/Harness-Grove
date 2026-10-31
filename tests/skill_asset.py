"""R3a 合成 Skill 素材：目录型 Skill（`SKILL.md` + 两个引用文件）与多文件 patch。

产品入口（`test_personalize_skill.py`）和旧沙箱闭环（`test_change_skill.py`）共用这一份手写合成内容；
不来自用户会话、真实配置或本机路径。P1 一次覆盖四类改动：改正文、改引用文件、加引用文件、删引用文件。
"""

import hashlib
import os
from pathlib import Path

SKILL_MD = """\
---
name: demo
description: 合成演示技能：验证多文件更新与恢复。
---

# demo

回答前先读 references/notes.md。
"""
NOTES = "笔记：先给结论。\n"
DRAFT = "草稿：这条引用没有被 SKILL.md 用到。\n"
EXTRA = "补充笔记：先复述问题。\n\n结论放在最前面。\n"

APPLIED_SKILL_MD = SKILL_MD.replace("references/notes.md。", "references/notes.md 与 references/extra.md。")
APPLIED_NOTES = "笔记：先给结论，再复述问题。\n"

BASE_FILES = {"SKILL.md": SKILL_MD, "references/notes.md": NOTES, "references/draft.md": DRAFT}

PATCH_P1 = """--- a/SKILL.md
+++ b/SKILL.md
@@ -6,3 +6,3 @@
 # demo
 
-回答前先读 references/notes.md。
+回答前先读 references/notes.md 与 references/extra.md。
--- a/references/notes.md
+++ b/references/notes.md
@@ -1,1 +1,1 @@
-笔记：先给结论。
+笔记：先给结论，再复述问题。
--- /dev/null
+++ b/references/extra.md
@@ -0,0 +1,3 @@
+补充笔记：先复述问题。
+
+结论放在最前面。
--- a/references/draft.md
+++ /dev/null
@@ -1,1 +0,0 @@
-草稿：这条引用没有被 SKILL.md 用到。
"""

# 另一条只改引用文件的条目：只批准 P1 时它不该生效。
PATCH_P2 = """--- a/references/notes.md
+++ b/references/notes.md
@@ -1,1 +1,1 @@
-笔记：先给结论。
+笔记：先给结论，再复述问题，并给出依据。
"""


def write_skill(root: Path) -> None:
    for name, text in BASE_FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def files_of(root: Path) -> dict[str, str]:
    """目录内容的 {相对路径: 文件 sha256}；软链不跟随，和资产内容口径一致。"""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def link_tree(root: Path, entry: Path, relative: bool = False) -> None:
    entry.parent.mkdir(parents=True, exist_ok=True)
    target: str | Path = os.path.relpath(root, entry.parent) if relative else root
    os.symlink(target, entry)
