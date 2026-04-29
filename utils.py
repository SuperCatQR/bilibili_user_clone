"""
工具函数模块

提供文件名清理等通用工具函数。
"""

import re
from config import MAX_FILENAME_LENGTH


def sanitize_filename(name: str) -> str:
    """
    清理文件名，使其在文件系统中安全可用。
    
    处理步骤：
    1. 将 Windows/Linux 非法字符替换为下划线
    2. 压缩连续空白为单个空格并去除首尾空白
    3. 去除首尾的点号和空格（避免隐藏文件或意外行为）
    4. 截断超长文件名至 MAX_FILENAME_LENGTH
    5. 空名回退为 "unnamed"
    """
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.strip(". ")
    if len(name) > MAX_FILENAME_LENGTH:
        name = name[:MAX_FILENAME_LENGTH]
    if not name:
        name = "unnamed"
    return name
