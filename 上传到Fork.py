#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键：把改造后的源码上传到你的 GitHub **fork**

用法（在 python 窗口里跑这一行）：
    exec(open(r"C:\\Users\\Administrator\\Documents\\deepseek-harness\\default-workspace\\ttml-id-match\\上传到Fork.py", encoding="utf-8").read())

它会：
  1) 探测你的 fork 地址（试 ttml-id-match- 与 ttml-id-match 两种，也接受你手动输入）
  2) clone 到一个临时目录 ttml-fork（若已存在则直接复用）
  3) 把改造后的源码覆盖进去（**排除** .env / dist / build / token / __pycache__ ✅）
  4) git add → 打印清单 → 若发现敏感文件则**中止** ❌
  5) commit
  6) 询问 Personal Access Token → push
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    SRC = os.path.dirname(os.path.abspath(__file__))
except NameError:
    SRC = r"C:\Users\Administrator\Documents\deepseek-harness\default-workspace\ttml-id-match"
if not os.path.isdir(SRC):
    SRC = r"C:\Users\Administrator\Documents\deepseek-harness\default-workspace\ttml-id-match"

WS = os.path.dirname(SRC)
WORK = os.path.join(WS, "ttml-fork")

COPY_FILES = [
    "serve_search.py", "search_metadata.html", "search_metadata.py",
    "apple_isrc.py", "spotify_markets.py", "一键重建打包.py", "上传到GitHub.py",
    "fill_ttml_metadata.py", "README-搜索程序.md", ".env.example", "requirements.txt",
]
COPY_DIRS = ["ttml_metadata", "docs"]
SKIP_NAMES = {".env", ".spotify-token.json", "dist", "build", "__pycache__", "_pkg", "_tmp"}


def sh(args, cwd=None):
    p = subprocess.run(args, cwd=cwd or WORK, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def find_remote() -> str | None:
    user = "zhr166"
    for name in ("ttml-id-match-", "ttml-id-match"):
        url = f"https://github.com/{user}/{name}.git"
        print("  试:", url)
        rc, out = sh(["git", "ls-remote", "--heads", url], cwd=WS)
        if rc == 0:
            print("  ✅ 可访问:", url)
            return url
        print("   ✗", out.splitlines()[0][:90] if out else "失败")
    return None


def main() -> None:
    print("源码目录:", SRC, "\nclone 到 :", WORK)
    rc, out = sh(["git", "--version"], cwd=WS)
    if rc != 0:
        print("❌ 没有 git ✅ —— 请改用网页上传（Add file → Upload files）")
        return
    print("git:", out)

    remote = find_remote()
    if not remote:
        remote = input("自动探测失败，请手动粘贴你的 fork 地址（https://github.com/…/xxx.git）：").strip()
        if not remote:
            print("未提供地址，退出 ✅")
            return

    if not os.path.isdir(os.path.join(WORK, ".git")):
        print("\n正在 clone …")
        rc, out = sh(["git", "clone", remote, WORK], cwd=WS)
        print(out[:600] or f"rc={rc}")
        if rc != 0:
            print("❌ clone 失败 —— 检查地址/网络 ✅")
            return
    else:
        print("复用已有目录 ✅")
        sh(["git", "remote", "set-url", "origin", remote])
        sh(["git", "pull", "--ff-only"])

    print("\n覆盖源码 …")
    n = 0
    for f in COPY_FILES:
        s = os.path.join(SRC, f)
        if os.path.exists(s):
            shutil.copy(s, os.path.join(WORK, f)); n += 1
    for d in COPY_DIRS:
        s = os.path.join(SRC, d)
        if os.path.isdir(s):
            shutil.copytree(s, os.path.join(WORK, d), dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(*SKIP_NAMES)); n += 1
    # README：新版作为仓库首页，原版保留
    new_readme = os.path.join(SRC, "README-搜索程序.md")
    if os.path.exists(new_readme):
        cur = os.path.join(WORK, "README.md")
        if os.path.exists(cur) and "kid141252010" not in open(cur, encoding="utf-8", errors="replace").read()[:3000]:
            old = os.path.join(WORK, "README-原版.md")
            if not os.path.exists(old):
                shutil.copy(cur, old)
        shutil.copy(new_readme, cur)
        n += 1
    gi = os.path.join(SRC, ".gitignore")
    if os.path.exists(gi):
        shutil.copy(gi, os.path.join(WORK, ".gitignore")); n += 1
    print(f"   已覆盖 {n} 项 ✅")

    sh(["git", "add", "."])
    rc, status = sh(["git", "status", "--short"])
    print("\n--- 将提交（前 60 行）---")
    print("\n".join(status.splitlines()[:60]) or "(无改动 ✅)")
    bad = [ln for ln in status.splitlines()
           if any(x in ln for x in (".env", ".spotify-token.json", "dist/", "build/", "_pkg/", ".spec", "__pycache__"))]
    if bad:
        print("\n❌ 发现不该提交的文件，已中止：")
        for b in bad:
            print("   ", b)
        return
    print("\n✅ 无敏感文件 ✅")

    rc, out = sh(["git", "commit", "-m", "feat: 基于 kid141252010/ttml-id-match 的可视化改造（多地区搜索 + Apple 官方 ISRC + Web UI）"])
    print("\n--- commit ---")
    print(out[:600] or f"rc={rc}")

    token = input("\nPersonal Access Token（classic，勾 repo 权限；回车则用系统已存凭据）：").strip()
    if token:
        push_url = re.sub(r"^https://", f"https://{token}@", remote)
        sh(["git", "remote", "set-url", "origin", push_url])
    rc, out = sh(["git", "push", "-u", "origin", "main"])
    if token:
        out = out.replace(token, "***")
    print("\n--- push ---")
    print(out[:1500] or f"rc={rc}")
    if rc == 0:
        print("\n🎉 上传成功 ✅ →", remote.replace(".git", ""))
        print("下一步：仓库页 → Releases → Draft a new release → 拖入 音乐元数据搜索程序.zip → Publish ✅")
    else:
        print("\n❌ push 失败 —— 常见：token 权限不足 / 分支名不是 main / 需先 pull")


if __name__ == "__main__":
    main()
