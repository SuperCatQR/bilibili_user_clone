"""
工具函数模块

提供文件名清理等通用工具函数。
所有无副作用的纯函数放在此模块中。
"""

import re
from config import MAX_FILENAME_LENGTH


def sanitize_filename(name: str) -> str:
    r"""
    清理文件名，使其在文件系统中安全可用。

    处理步骤：
    1. 将 Windows/Linux 非法字符替换为下划线
       - Windows非法字符: \ / : * ? " < > |
       - Linux非法字符: / (仅此一个，但 \ 在某些shell中有特殊含义)
       - 统一替换为下划线，保持视觉连续性
    2. 压缩连续空白为单个空格并去除首尾空白
       - 避免文件名中出现多个连续空格
    3. 去除首尾的点号和空格
       - Windows上以点号开头的文件名可能被当作隐藏文件
       - 首尾空格在某些文件系统上会导致访问困难
    4. 截断超长文件名至 MAX_FILENAME_LENGTH
       - 避免超过文件系统限制（NTFS/ext4/APFS均为255字符）
    5. 空名回退为 "unnamed"
       - 防止空文件名导致的文件系统错误
    """"
    # 步骤1: 替换非法字符为下划线
    # 正则 [\\/:*?"<>|] 匹配所有Windows/Linux非法字符
    name = re.sub(r'[\\/:*?"<>|]', "_", name)

    # 步骤2: 压缩连续空白
    # \s+ 匹配一个或多个空白字符（空格、制表符、换行等）
    # strip() 去除首尾空白
    name = re.sub(r"\s+", " ", name).strip()

    # 步骤3: 去除首尾点号和空格
    # 避免 ".hidden" 或 "file." 这样的文件名
    name = name.strip(". ")

    # 步骤4: 截断超长文件名
    if len(name) > MAX_FILENAME_LENGTH:
        name = name[:MAX_FILENAME_LENGTH]

    # 步骤5: 空名回退
    if not name:
        name = "unnamed"

    return name
