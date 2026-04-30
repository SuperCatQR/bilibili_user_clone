"""
认证模块

负责B站登录凭据的加载、保存和QR码登录。
凭据保存在 ~/.bilibili-cli/credential.json，有效期由 CREDENTIAL_TTL_DAYS 控制。
QR码使用小尺寸（box_size=1, border=1）输出，适配终端显示。

认证流程：
1. 尝试从本地文件加载已保存的凭据
2. 检查凭据是否在有效期内（7天）
3. 如果凭据有效，直接使用
4. 如果凭据过期或不存在，启动QR码登录
5. 登录成功后保存凭据到本地文件

安全设计：
- 凭据文件权限设置为600（仅所有者可读写）
- 凭据目录权限设置为700（仅所有者可访问）
- 仅在Unix系统上设置权限，Windows系统跳过
"""

import json
import time
import asyncio
from pathlib import Path

import qrcode as qrcode_lib
from bilibili_api import Credential, login_v2
from rich.console import Console

from config import CREDENTIAL_DIR, CREDENTIAL_FILE, CREDENTIAL_TTL_DAYS

console = Console()


def _load_saved_credential() -> Credential | None:
    """
    从本地文件加载已保存的凭据。
    
    检查项：
    - 文件是否存在：不存在则返回None
    - saved_at 时间戳是否在有效期内：过期则返回None
    - sessdata 是否非空：空则返回None（sessdata是B站会话的核心凭证）
    
    Returns:
        Credential对象，或None（加载失败时）
    """
    # 检查凭据文件是否存在
    if not CREDENTIAL_FILE.exists():
        return None

    try:
        # 读取并解析JSON文件
        data = json.loads(CREDENTIAL_FILE.read_text(encoding="utf-8"))

        # 检查时间戳是否在有效期内
        # CREDENTIAL_TTL_DAYS * 86400 将天数转换为秒数
        saved_at = data.get("saved_at", 0)
        if time.time() - saved_at > CREDENTIAL_TTL_DAYS * 86400:
            console.print("[yellow]凭据已过期，需要重新登录[/yellow]")
            return None

        # 构造Credential对象
        # Credential是bilibili_api库提供的认证凭据类
        # 包含sessdata、bili_jct等B站会话必需的Cookie值
        cred = Credential(
            sessdata=data.get("sessdata", ""),
            bili_jct=data.get("bili_jct", ""),
            buvid3=data.get("buvid3", ""),
            buvid4=data.get("buvid4", ""),
            dedeuserid=data.get("dedeuserid", ""),
            ac_time_value=data.get("ac_time_value", ""),
        )

        # 检查sessdata是否非空（sessdata是核心会话标识）
        if not cred.has_sessdata():
            return None

        return cred

    except Exception as e:
        console.print(f"[yellow]读取凭据失败: {e}[/yellow]")
        return None


def _save_credential(cred: Credential):
    """
    将凭据序列化写入本地文件，同时记录保存时间戳。
    
    安全措施：
    - 创建目录并设置权限为700（仅所有者可读写执行）
    - 设置文件权限为600（仅所有者可读写）
    - Windows系统或权限设置失败时静默跳过
    """
    # 创建目录并设置权限（仅所有者可读写执行）
    CREDENTIAL_DIR.mkdir(parents=True, exist_ok=True)
    try:
        # Unix系统上设置目录权限为700（仅所有者可访问）
        # stat.S_IRWXU = 0o700 = 所有者可读+写+执行
        import stat
        CREDENTIAL_DIR.chmod(stat.S_IRWXU)  # 0o700
    except (OSError, AttributeError):
        # Windows系统或权限设置失败时跳过
        # Windows不支持chmod，或文件系统不支持权限设置
        pass

    # 构造凭据数据字典
    # 保存所有Cookie字段，空值用空字符串代替
    data = {
        "sessdata": cred.sessdata or "",
        "bili_jct": cred.bili_jct or "",
        "buvid3": cred.buvid3 or "",
        "buvid4": cred.buvid4 or "",
        "dedeuserid": cred.dedeuserid or "",
        "ac_time_value": cred.ac_time_value or "",
        "saved_at": time.time(),  # 记录保存时间，用于过期判断
    }

    # 写入JSON文件
    # indent=2: 格式化输出便于人工查看
    # ensure_ascii=False: 允许非ASCII字符（中文等）
    CREDENTIAL_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # 设置文件权限为600（仅所有者可读写）
    # stat.S_IRUSR = 0o400 = 所有者可读
    # stat.S_IWUSR = 0o200 = 所有者可写
    try:
        CREDENTIAL_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    except (OSError, AttributeError):
        # Windows系统或权限设置失败时跳过
        pass


def _print_small_qr(url: str):
    """
    用小尺寸ASCII方式在终端打印二维码。
    
    使用 qrcode 库的 print_ascii 替代 bilibili_api 默认的 qrcode_terminal 输出，
    box_size=1, border=1 使二维码更紧凑，适配窄终端。
    
    Args:
        url: 二维码内容URL（B站扫码登录URL）
    """
    # QRCode参数说明：
    # box_size=1: 每个二维码模块占1个字符宽度（最小尺寸）
    # border=1: 边框宽度为1个模块（最小安全边框）
    qr = qrcode_lib.QRCode(box_size=1, border=1)
    qr.add_data(url)

    # invert=True: 反转黑白（终端通常是深色背景，反转后更清晰）
    qr.print_ascii(invert=True)


async def _qr_login() -> Credential:
    """
    执行QR码登录流程。
    
    流程：
    1. 创建QrCodeLogin对象（WEB渠道）
    2. 生成二维码并显示
    3. 轮询扫码状态（每5秒检查一次）
    4. 根据状态做出响应：
       - DONE: 登录成功，返回凭据
       - TIMEOUT: 二维码过期，重新生成
       - CONF: 已扫描，等待用户确认
       - 其他: 继续等待
    
    注意：QR URL 通过名称改写属性 qr._QrCodeLogin__qr_link 访问，
    因为 bilibili_api 的 QrCodeLogin 类没有公开的 URL getter。
    这是Python的名称改写（name mangling）机制，双下划线前缀的属性会被改写。
    """
    console.print("[cyan]启动QR码登录...[/cyan]")

    # 创建QR码登录对象
    # QrCodeLoginChannel.WEB 表示使用Web渠道的扫码登录
    qr = login_v2.QrCodeLogin(login_v2.QrCodeLoginChannel.WEB)

    # 生成二维码（会请求B站API获取扫码URL）
    await qr.generate_qrcode()

    console.print("[cyan]请使用B站手机APP扫描以下二维码登录：[/cyan]")

    # 打印二维码
    # qr._QrCodeLogin__qr_link 是Python名称改写后的属性
    # 实际访问的是 qr.__qr_link（私有属性）
    _print_small_qr(qr._QrCodeLogin__qr_link)

    # 轮询扫码状态
    while True:
        # 每5秒检查一次扫码状态
        await asyncio.sleep(5)
        status = await qr.check_state()

        if status == login_v2.QrCodeLoginEvents.DONE:
            # 登录成功，获取凭据
            cred = qr.get_credential()
            console.print("[green]登录成功！[/green]")
            return cred

        elif status == login_v2.QrCodeLoginEvents.TIMEOUT:
            # 二维码过期（通常30秒有效期），重新生成
            console.print("[yellow]二维码已过期，重新生成...[/yellow]")
            await qr.generate_qrcode()
            _print_small_qr(qr._QrCodeLogin__qr_link)

        elif status == login_v2.QrCodeLoginEvents.CONF:
            # 已扫描，等待用户在手机上确认
            console.print("[cyan]已扫描，请在手机上确认登录...[/cyan]")

        else:
            # 其他状态（如INIT），继续等待
            console.print("[dim]等待扫描...[/dim]")


async def ensure_credential() -> Credential:
    """
    获取有效凭据，优先使用已保存的凭据，过期或不存在则触发QR码登录。
    
    如果已保存凭据缺少 buvid3/buvid4，会自动调用 get_buvid_cookies() 补充。
    buvid3/buvid4 是B站用于设备标识的Cookie，某些API调用需要。
    
    Returns:
        有效的Credential对象
    """
    # 尝试加载已保存的凭据
    cred = _load_saved_credential()
    if cred:
        console.print("[green]使用已保存的凭据[/green]")

        # 补充缺失的buvid3/buvid4
        # buvid3/buvid4是B站的设备标识Cookie
        # 某些API（如获取用户信息）需要这些Cookie
        if not cred.has_buvid3() or not cred.has_buvid4():
            try:
                await cred.get_buvid_cookies()
            except Exception:
                # 补充失败不影响主流程，静默忽略
                pass

        return cred

    # 凭据不存在或已过期，启动QR码登录
    cred = await _qr_login()

    # 保存凭据到本地文件
    _save_credential(cred)

    return cred
