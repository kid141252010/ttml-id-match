#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键：重建 exe → 启动 → 测 KR/CN → 打包 zip

用法（在你的 python 窗口里也行）：
    双击本文件，或在命令行运行：
    python 一键重建打包.py

做的事：
  1) 杀掉正在运行的 exe（否则 _internal 里的 DLL 被占用，构建会失败 ❌）
  2) 清掉 build / dist
  3) PyInstaller 重建（onedir + 两个 hidden-import ✅）
  4) 复制 exe 为「音乐元数据搜索.exe」，并把新页面覆盖进 _internal ✅
  5) 启动服务（端口 8900）→ 测 TW / KR / CN 三个区（含 ISRC 打印 ✅）
  6) 打包 音乐元数据搜索程序.zip（exe + _internal + 源码，根目录一层「音乐元数据搜索」文件夹 ✅）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # 通过 exec(open(...).read()) 运行时没有 __file__ ✅ → 用固定路径兜底 ✅
    HERE = r"C:\Users\Administrator\Documents\deepseek-harness\default-workspace\ttml-id-match"
HERE = HERE if os.path.isdir(HERE) else r"C:\Users\Administrator\Documents\deepseek-harness\default-workspace\ttml-id-match"
WS = os.path.dirname(HERE)
ZIP_PATH = os.path.join(WS, "音乐元数据搜索程序.zip")
PORT = 8900
APP = "MusicMetaSearchApp"


def step(msg: str) -> None:
    print("\n=== " + msg + " ===")


def kill_exes() -> None:
    step("1) 关掉正在运行的 exe")
    for name in ("音乐元数据搜索.exe", "MusicMetaSearchApp.exe"):
        r = subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True, text=True)
        print("   taskkill", name, "→", (r.stdout or r.stderr or "").strip()[:80] or "ok")
    time.sleep(2)


def clean() -> None:
    step("2) 清理 build / dist")
    for d in ("build", "dist"):
        p = os.path.join(HERE, d)
        shutil.rmtree(p, ignore_errors=True)
        print("   已删", p)
    time.sleep(1)


def build() -> bool:
    step("3) PyInstaller 重建（约 1 分钟）")
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
        "--name", APP, "--add-data", "search_metadata.html;.",
        "--hidden-import", "apple_isrc", "--hidden-import", "spotify_markets",
        "serve_search.py",
    ]
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0:
        print("   ❌ 构建失败 returncode =", r.returncode)
        return False
    print("   ✅ 构建成功")
    return True


def finalize() -> str:
    step("4) 改名 + 覆盖页面")
    app_dir = os.path.join(HERE, "dist", APP)
    exe_new = os.path.join(app_dir, "音乐元数据搜索.exe")
    shutil.copy(os.path.join(app_dir, APP + ".exe"), exe_new)
    shutil.copy(os.path.join(HERE, "search_metadata.html"),
                os.path.join(app_dir, "_internal", "search_metadata.html"))
    print("   ✅", exe_new)
    return exe_new


def test_regions(exe: str) -> None:
    step("5) 启动并测试 TW / KR / CN")
    subprocess.Popen([exe, "--port", str(PORT), "--no-open"], cwd=HERE)
    time.sleep(10)

    def q(term: str, countries: str):
        url = (f"http://127.0.0.1:{PORT}/api/search?term=" + urllib.parse.quote(term)
               + f"&countries={countries}&limit=3&sources=apple")
        try:
            with urllib.request.urlopen(url, timeout=180) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:
            return {"error": str(exc)[:120]}

    for term, cc in [("晴天 周杰伦", "TW"), ("BTS", "KR"), ("IU", "KR"),
                     ("周杰伦", "KR"), ("周杰伦", "CN"), ("晴天 周杰伦", "CN")]:
        r = q(term, cc)
        if r.get("error"):
            print(f"   [{cc}] {term} → 失败 {r['error']}")
            continue
        print(f"   [{cc}] {term} → count={r.get('count')}  warnings={r.get('warnings')}")
        for x in (r.get("results") or [])[:2]:
            print(f"        {x.get('name')} | {x.get('artist')} | ISRC {x.get('isrc') or '-'} | 命中区 {x.get('found_in')}")


def package() -> None:
    step("6) 打包 zip")
    app_dir = os.path.join(HERE, "dist", APP)
    tmp = os.path.join(WS, "_pkg")
    dest = os.path.join(tmp, "音乐元数据搜索")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)

    shutil.copy(os.path.join(app_dir, "音乐元数据搜索.exe"), dest)
    shutil.copytree(os.path.join(app_dir, "_internal"), os.path.join(dest, "_internal"))

    for f in ("search_metadata.py", "serve_search.py", "search_metadata.html", "apple_isrc.py",
              "spotify_markets.py", "搜索程序-使用说明.md", ".env.example", "README.md",
              "fill_ttml_metadata.py"):
        src = os.path.join(HERE, f)
        if os.path.exists(src):
            shutil.copy(src, dest)
    for d in ("ttml_metadata", "docs"):
        src = os.path.join(HERE, d)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dest, d), dirs_exist_ok=True)

    if os.path.exists(ZIP_PATH):
        os.remove(ZIP_PATH)
    shutil.make_archive(ZIP_PATH[:-4], "zip", tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    print("   ✅", ZIP_PATH, round(os.path.getsize(ZIP_PATH) / 1024 / 1024, 1), "MB")


def main() -> None:
    print("工作目录:", HERE)
    kill_exes()
    clean()
    if not build():
        print("\n构建失败 —— 请把上面的错误发给开发者 ✅")
        return
    exe = finalize()
    test_regions(exe)
    package()
    print("\n全部完成 ✅（记得关掉那个服务器窗口，或按 Ctrl+C 停止）")


if __name__ == "__main__":
    main()
