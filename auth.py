"""
认证模块

负责B站登录凭据的加载、保存和QR码登录。
凭据保存在 ~/.bilibili-cli/credential.json，有效期由 CREDENTIAL_TTL_DAYS 控制。
QR码使用小尺寸（box_size=1, border=1）输出，适配终端显示。
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
    - 文件是否存在
    - saved_at 时间戳是否在有效期内
    - sessdata 是否非空
    任一检查失败返回 None。
    """
    if not CREDENTIAL_FILE.exists():
        return None
    try:
        data = json.loads(CREDENTIAL_FILE.read_text(encoding="utf-8"))
        saved_at = data.get("saved_at", 0)
        if time.time() - saved_at > CREDENTIAL_TTL_DAYS * 86400:
            console.print("[yellow]凭据已过期，需要重新登录[/yellow]")
            return None
        cred = Credential(
            sessdata=data.get("sessdata", ""),
            bili_jct=data.get("bili_jct", ""),
            buvid3=data.get("buvid3", ""),
            buvid4=data.get("buvid4", ""),
            dedeuserid=data.get("dedeuserid", ""),
            ac_time_value=data.get("ac_time_value", ""),
        )
        if not cred.has_sessdata():
            return None
        return cred
    except Exception as e:
        console.print(f"[yellow]读取凭据失败: {e}[/yellow]")
        return None


def _save_credential(cred: Credential):
    """将凭据序列化写入本地文件，同时记录保存时间戳。"""
    # 创建目录并设置权限（仅所有者可读写执行）
    CREDENTIAL_DIR.mkdir(parents=True, exist_ok=True)
    try:
        # Unix系统上设置目录权限为700（仅所有者可访问）
        import stat
        CREDENTIAL_DIR.chmod(stat.S_IRWXU)  # 0o700
    except (OSError, AttributeError):
        # Windows系统或权限设置失败时跳过
        pass
    
    data = {
        "sessdata": cred.sessdata or "",
        "bili_jct": cred.bili_jct or "",
        "buvid3": cred.buvid3 or "",
        "buvid4": cred.buvid4 or "",
        "dedeuserid": cred.dedeuserid or "",
        "ac_time_value": cred.ac_time_value or "",
        "saved_at": time.time(),
    }
    CREDENTIAL_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    
    # 设置文件权限为600（仅所有者可读写）
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
    """
    qr = qrcode_lib.QRCode(box_size=1, border=1)
    qr.add_data(url)
    qr.print_ascii(invert=True)


async def _qr_login() -> Credential:
    """
    执行QR码登录流程。
    
    流程：生成二维码 → 轮询扫码状态（每5秒） →
    - DONE: 返回凭据
    - TIMEOUT: 重新生成二维码
    - CONF: 提示用户确认
    - 其他: 继续等待
    
    注意：QR URL 通过名称改写属性 qr._QrCodeLogin__qr_link 访问，
    因为 bilibili_api 的 QrCodeLogin 类没有公开的 URL getter。
    """
    console.print("[cyan]启动QR码登录...[/cyan]")
    qr = login_v2.QrCodeLogin(login_v2.QrCodeLoginChannel.WEB)
    await qr.generate_qrcode()
    console.print("[cyan]请使用B站手机APP扫描以下二维码登录：[/cyan]")
    _print_small_qr(qr._QrCodeLogin__qr_link)

    while True:
        await asyncio.sleep(5)
        status = await qr.check_state()
        if status == login_v2.QrCodeLoginEvents.DONE:
            cred = qr.get_credential()
            console.print("[green]登录成功！[/green]")
            return cred
        elif status == login_v2.QrCodeLoginEvents.TIMEOUT:
            console.print("[yellow]二维码已过期，重新生成...[/yellow]")
            await qr.generate_qrcode()
            _print_small_qr(qr._QrCodeLogin__qr_link)
        elif status == login_v2.QrCodeLoginEvents.CONF:
            console.print("[cyan]已扫描，请在手机上确认登录...[/cyan]")
        else:
            console.print("[dim]等待扫描...[/dim]")


async def ensure_credential() -> Credential:
    """
    获取有效凭据，优先使用已保存的凭据，过期或不存在则触发QR码登录。
    
    如果已保存凭据缺少 buvid3/buvid4，会自动调用 get_buvid_cookies() 补充。
    """
    cred = _load_saved_credential()
    if cred:
        console.print("[green]使用已保存的凭据[/green]")
        if not cred.has_buvid3() or not cred.has_buvid4():
            try:
                await cred.get_buvid_cookies()
            except Exception:
                pass
        return cred

    cred = await _qr_login()
    _save_credential(cred)
    return cred
