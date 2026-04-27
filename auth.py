import json
import time
import asyncio
from pathlib import Path

from bilibili_api import Credential, login_v2
from rich.console import Console

from config import CREDENTIAL_DIR, CREDENTIAL_FILE, CREDENTIAL_TTL_DAYS

console = Console()


def _load_saved_credential() -> Credential | None:
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
    CREDENTIAL_DIR.mkdir(parents=True, exist_ok=True)
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


async def _qr_login() -> Credential:
    console.print("[cyan]启动QR码登录...[/cyan]")
    qr = login_v2.QrCodeLogin(login_v2.QrCodeLoginChannel.WEB)
    await qr.generate_qrcode()
    console.print("[cyan]请使用B站手机APP扫描以下二维码登录：[/cyan]")
    print(qr.get_qrcode_terminal())

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
            print(qr.get_qrcode_terminal())
        elif status == login_v2.QrCodeLoginEvents.CONF:
            console.print("[cyan]已扫描，请在手机上确认登录...[/cyan]")
        else:
            console.print("[dim]等待扫描...[/dim]")


async def ensure_credential() -> Credential:
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
