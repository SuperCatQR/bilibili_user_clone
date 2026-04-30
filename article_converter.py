"""
专栏 HTML → Markdown 异步转换器

将B站专栏的 HTML 内容递归转换为 Markdown 格式，
并将文章中的图片下载到本地 images/ 目录，替换为相对路径引用。

转换原理：
- 递归遍历HTML DOM树
- 根据标签类型映射到Markdown语法
- 图片标签触发异步下载，替换为本地路径
- 纯文本节点直接返回文本内容

支持的HTML元素映射：
- 标题: h1~h6 → # ~ ######
- 段落: p → 空行包裹的文本
- 引用: blockquote → > 引用
- 代码块: pre/code → ```代码```
- 行内代码: code → `代码`
- 图片: img → ![alt](本地路径)
- 链接: a → [文本](href)
- 粗体: strong/b → **粗体**
- 斜体: em/i → *斜体*
- 无序列表: ul/li → - 列表项
- 有序列表: ol/li → 1. 列表项
- 换行: br → 换行符
- 容器: figure/figcaption/span/div等 → 递归处理子元素
"""

import re
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

from downloader import download_file
from bilibili_api import Credential


async def _process_element(el: Tag | NavigableString, output_dir: Path, credential: Credential, image_idx: list, retries: int = 3) -> str:
    """
    递归处理单个 HTML 元素，转换为 Markdown 文本。

    处理逻辑：
    1. 纯文本节点（NavigableString）：直接返回文本
    2. 标签节点（Tag）：根据标签类型进行转换
    3. 未知标签：递归处理子元素

    Args:
        el: BeautifulSoup元素（Tag或NavigableString）
        output_dir: 输出目录（用于保存图片）
        credential: 认证凭据（用于下载图片）
        image_idx: 可变列表 [counter]，用于为图片递增编号
                   使用列表而非整数，因为列表是可变对象，可以在递归中共享状态
        retries: 下载重试次数

    Returns:
        转换后的Markdown文本
    """
    # 处理纯文本节点
    # NavigableString是BeautifulSoup中的文本节点类型
    if isinstance(el, NavigableString):
        text = str(el)
        # 忽略纯空白文本
        if text.strip() == "":
            return ""
        return text

    # 非Tag类型（如Comment），忽略
    if not isinstance(el, Tag):
        return ""

    # 获取标签名
    tag = el.name

    # 处理标题标签 h1~h6
    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        # 提取标题级别（1~6）
        level = int(tag[1])
        # 递归处理子元素
        inner = await _process_children(el, output_dir, credential, image_idx, retries)
        # Markdown标题语法：# 标题内容
        return f"\n\n{'#' * level} {inner.strip()}\n\n"

    # 处理段落标签 p
    if tag == "p":
        inner = await _process_children(el, output_dir, credential, image_idx, retries)
        # 段落前后各加两个换行符
        return f"\n\n{inner.strip()}\n\n"

    # 处理引用标签 blockquote
    if tag == "blockquote":
        inner = await _process_children(el, output_dir, credential, image_idx, retries)
        lines = inner.strip().split("\n")
        # 每行前面加 > 前缀
        return "\n\n" + "\n".join(f"> {line}" for line in lines) + "\n\n"

    # 处理代码块标签 pre
    if tag in ("pre",):
        # 尝试获取 code 子元素（标准写法：<pre><code>...</code></pre>）
        code_el = el.find("code")
        if code_el:
            # 提取语言标记（如 class="language-python"）
            lang = code_el.get("class", [""])
            # 去除 "language-" 前缀
            lang_str = lang[0].replace("language-", "") if lang else ""
            code_text = code_el.get_text()
        else:
            # 没有 code 子元素，直接取 pre 的文本
            lang_str = ""
            code_text = el.get_text()
        # Markdown代码块语法：```语言\n代码\n```
        return f"\n\n```{lang_str}\n{code_text.strip()}\n```\n\n"

    # 处理行内代码标签 code（不在 pre 内的 code）
    if tag == "code" and el.parent and el.parent.name != "pre":
        # Markdown行内代码语法：`代码`
        return f"`{el.get_text()}`"

    # 处理图片标签 img
    if tag == "img":
        # 优先使用 data-src（B站懒加载图片），回退到 src
        src = el.get("data-src") or el.get("src", "")
        alt = el.get("alt", "")
        if src:
            # 创建images目录
            image_dir = output_dir / "images"
            image_dir.mkdir(parents=True, exist_ok=True)

            # 递增图片编号
            image_idx[0] += 1

            # 从URL中提取扩展名
            ext_match = re.search(r"\.(jpg|jpeg|png|gif|webp|bmp)", src, re.IGNORECASE)
            ext = ext_match.group(1) if ext_match else "jpg"

            # 生成本地文件名
            filename = f"img_{image_idx[0]}.{ext}"
            local_path = image_dir / filename

            # 下载图片
            ok = await download_file(src, local_path, credential, retries=retries)
            if ok:
                # 下载成功，使用本地相对路径
                return f"\n\n![{alt}](images/{filename})\n\n"
            else:
                # 下载失败，保留原始URL
                return f"\n\n![{alt}]({src})\n\n"
        return ""

    # 处理链接标签 a
    if tag == "a":
        href = el.get("href", "")
        inner = await _process_children(el, output_dir, credential, image_idx, retries)
        # Markdown链接语法：[文本](URL)
        return f"[{inner.strip()}]({href})"

    # 处理粗体标签 strong/b
    if tag == "strong" or tag == "b":
        inner = await _process_children(el, output_dir, credential, image_idx, retries)
        # Markdown粗体语法：**文本**
        return f"**{inner.strip()}**"

    # 处理斜体标签 em/i
    if tag == "em" or tag == "i":
        inner = await _process_children(el, output_dir, credential, image_idx, retries)
        # Markdown斜体语法：*文本*
        return f"*{inner.strip()}*"

    # 处理无序列表标签 ul
    if tag == "ul":
        items = []
        # 只处理直接子li元素（recursive=False）
        for li in el.find_all("li", recursive=False):
            inner = await _process_children(li, output_dir, credential, image_idx, retries)
            items.append(f"- {inner.strip()}")
        return "\n\n" + "\n".join(items) + "\n\n"

    # 处理有序列表标签 ol
    if tag == "ol":
        items = []
        for i, li in enumerate(el.find_all("li", recursive=False), 1):
            inner = await _process_children(li, output_dir, credential, image_idx, retries)
            items.append(f"{i}. {inner.strip()}")
        return "\n\n" + "\n".join(items) + "\n\n"

    # 处理换行标签 br
    if tag == "br":
        return "\n"

    # 处理figure标签（图片容器）
    if tag == "figure":
        # 直接递归处理子元素（通常包含img和figcaption）
        return await _process_children(el, output_dir, credential, image_idx, retries)

    # 处理figcaption标签（图片说明）
    if tag == "figcaption":
        inner = await _process_children(el, output_dir, credential, image_idx, retries)
        # 用斜体显示说明文字
        return f"\n*{inner.strip()}*\n"

    # 处理容器标签（span/div/section/article等）
    # 这些标签本身没有Markdown对应，直接递归处理子元素
    if tag in ("span", "div", "section", "article", "main", "header", "footer", "nav"):
        return await _process_children(el, output_dir, credential, image_idx, retries)

    # 其他未知标签，递归处理子元素
    return await _process_children(el, output_dir, credential, image_idx, retries)


async def _process_children(el: Tag, output_dir: Path, credential: Credential, image_idx: list, retries: int = 3) -> str:
    """
    递归处理元素的所有子节点，拼接返回。

    遍历el的所有直接子节点，对每个子节点调用_process_element，
    将结果拼接成完整的Markdown文本。

    Args:
        el: 父元素
        output_dir: 输出目录
        credential: 认证凭据
        image_idx: 图片编号计数器
        retries: 下载重试次数

    Returns:
        所有子元素转换后的Markdown文本拼接
    """
    result = []
    for child in el.children:
        result.append(await _process_element(child, output_dir, credential, image_idx, retries))
    return "".join(result)


async def html_to_markdown(html: str, output_dir: Path, credential: Credential, retries: int = 3) -> str:
    """
    将专栏 HTML 正文转换为 Markdown。

    处理流程：
    1. 使用BeautifulSoup解析HTML
    2. 定位正文根节点（优先级：.article-content > article > body > soup）
    3. 递归处理所有子元素
    4. 合并多余空行（3个以上连续换行合并为2个）
    5. 去除首尾空白

    Args:
        html: 原始HTML字符串
        output_dir: 输出目录
        credential: 认证凭据
        retries: 下载重试次数

    Returns:
        转换后的Markdown文本
    """
    # 使用lxml解析器（速度快，容错性好）
    soup = BeautifulSoup(html, "lxml")

    # 定位正文根节点
    # B站专栏的正文通常在 .article-content 类的div中
    # 回退到 article 标签，再回退到 body 标签
    root = soup.find("div", class_="article-content") or soup.find("article") or soup.body or soup

    # 图片编号计数器（使用列表以便在递归中共享状态）
    image_idx = [0]

    # 递归处理所有子元素
    md = await _process_children(root, output_dir, credential, image_idx, retries)

    # 合并多余空行（3个以上连续换行合并为2个）
    md = re.sub(r"\n{3,}", "\n\n", md)

    return md.strip()
