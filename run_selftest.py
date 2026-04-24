import sys
import os
sys.path.append(r'c:\Users\LENOVO\Desktop\新建文件夹 (2)\新建文件夹 (2)')

# 设置自测参数
sys.argv = ['文字冒险游戏.py', '--selftest']

# 执行脚本
with open(r'c:\Users\LENOVO\Desktop\新建文件夹 (2)\新建文件夹 (2)\文字冒险游戏.py', 'r', encoding='utf-8') as f:
    code = f.read()

try:
    exec(code)
except Exception as e:
    print(f"执行出错: {e}")
    import traceback
    traceback.print_exc()