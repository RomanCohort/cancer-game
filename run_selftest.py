"""跑一次游戏自检（任何机器上都可用）。

原来这份把路径写死成作者桌面（c:\\Users\\LENOVO\\Desktop\\新建文件夹 (2)\\...），
换台机器必然 FileNotFoundError。改成按脚本自身位置找游戏，并把退出码透传出去，
这样 CI / 网页宿主可以直接看返回码。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = os.path.join(HERE, "文字冒险游戏.py")

if not os.path.exists(GAME):
    print(f"[FAIL] 找不到游戏脚本：{GAME}")
    sys.exit(2)

sys.exit(subprocess.run([sys.executable, GAME, "--selftest"]).returncode)
