"""Host Deck 密码存储：Windows 凭据库，测试时可用目录后端。

密码不进 hosts.toml、不进 ~/.ssh/config、不进 Git。
"""

import os
import re
import shutil
import subprocess
import sys

SERVICE = "HostDeck"
TARGET_PREFIX = "HostDeck/ssh/"
_HAS_CACHE = {}

_PS_HELPER = r"""
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
if (-not $user) { $user = 'host-deck' }
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class HostDeckCred {
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
  public struct NativeCredential {
    public uint Flags;
    public uint Type;
    public IntPtr TargetName;
    public IntPtr Comment;
    public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
    public uint CredentialBlobSize;
    public IntPtr CredentialBlob;
    public uint Persist;
    public uint AttributeCount;
    public IntPtr Attributes;
    public IntPtr TargetAlias;
    public IntPtr UserName;
  }
  [DllImport("advapi32.dll", EntryPoint = "CredWriteW", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern bool CredWrite(ref NativeCredential userCredential, uint flags);
  [DllImport("advapi32.dll", EntryPoint = "CredReadW", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern bool CredRead(string target, uint type, int reservedFlag, out IntPtr credentialPtr);
  [DllImport("advapi32.dll", SetLastError = true)]
  public static extern bool CredFree(IntPtr cred);
  [DllImport("advapi32.dll", EntryPoint = "CredDeleteW", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern bool CredDelete(string target, uint type, int flags);
}
"@
function Get-Password {
  $ptr = [IntPtr]::Zero
  if (-not [HostDeckCred]::CredRead($target, 1, 0, [ref]$ptr)) { return $null }
  try {
    $cred = [Runtime.InteropServices.Marshal]::PtrToStructure($ptr, [type][HostDeckCred+NativeCredential])
    if ($cred.CredentialBlob -eq [IntPtr]::Zero -or $cred.CredentialBlobSize -le 0) { return '' }
    return [Runtime.InteropServices.Marshal]::PtrToStringUni($cred.CredentialBlob, [int]($cred.CredentialBlobSize / 2))
  } finally {
    [HostDeckCred]::CredFree($ptr) | Out-Null
  }
}
if ($mode -eq 'has') {
  $ptr = [IntPtr]::Zero
  if ([HostDeckCred]::CredRead($target, 1, 0, [ref]$ptr)) {
    [HostDeckCred]::CredFree($ptr) | Out-Null
    exit 0
  }
  exit 1
}
if ($mode -eq 'get') {
  $secret = Get-Password
  if ($null -eq $secret) { exit 1 }
  [Console]::Out.Write($secret)
  exit 0
}
if ($mode -eq 'delete') {
  [HostDeckCred]::CredDelete($target, 1, 0) | Out-Null
  exit 0
}
if ($mode -eq 'set') {
  $secret = [Console]::In.ReadToEnd()
  if ($secret.EndsWith("`r`n")) { $secret = $secret.Substring(0, $secret.Length - 2) }
  elseif ($secret.EndsWith("`n")) { $secret = $secret.Substring(0, $secret.Length - 1) }
  $targetPtr = [Runtime.InteropServices.Marshal]::StringToCoTaskMemUni($target)
  $userPtr = [Runtime.InteropServices.Marshal]::StringToCoTaskMemUni($user)
  $bytes = [Text.Encoding]::Unicode.GetBytes($secret)
  $pinned = [Runtime.InteropServices.GCHandle]::Alloc($bytes, [Runtime.InteropServices.GCHandleType]::Pinned)
  try {
    $cred = New-Object HostDeckCred+NativeCredential
    $cred.Type = 1
    $cred.TargetName = $targetPtr
    $cred.UserName = $userPtr
    $cred.CredentialBlob = $pinned.AddrOfPinnedObject()
    $cred.CredentialBlobSize = [uint32]$bytes.Length
    $cred.Persist = 2
    if (-not [HostDeckCred]::CredWrite([ref]$cred, 0)) {
      throw "CredWrite failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    }
  } finally {
    $pinned.Free()
    [Array]::Clear($bytes, 0, $bytes.Length)
    [Runtime.InteropServices.Marshal]::FreeCoTaskMem($targetPtr)
    [Runtime.InteropServices.Marshal]::FreeCoTaskMem($userPtr)
  }
  exit 0
}
exit 2
"""


def cred_target(alias: str) -> str:
    return TARGET_PREFIX + alias


def _secrets_dir():
    path = os.environ.get("HOST_DECK_SECRETS_DIR")
    return path if path else None


def _safe_name(alias: str) -> str:
    if re.fullmatch(r"[\w.-]+", alias):
        return alias
    import hashlib
    return hashlib.sha256(alias.encode("utf-8")).hexdigest()[:32]


def _file_path(alias: str) -> str:
    return os.path.join(_secrets_dir(), _safe_name(alias))


def _powershell():
    return shutil.which("powershell.exe") or shutil.which("pwsh.exe")


def _ps_quote(value: str) -> str:
    return "'" + (value or "").replace("'", "''") + "'"


def _run_windows(mode: str, alias: str, password: str | None = None, user: str = ""):
    exe = _powershell()
    if not exe:
        raise RuntimeError("找不到 powershell.exe，无法使用 Windows 凭据库")
    header = (
        f"$mode = {_ps_quote(mode)}\n"
        f"$target = {_ps_quote(cred_target(alias))}\n"
        f"$user = {_ps_quote(user or 'host-deck')}\n"
    )
    encoded = __import__("base64").b64encode(
        (header + _PS_HELPER).encode("utf-16le")
    ).decode("ascii")
    stdin = password.encode("utf-8") if password is not None else None
    result = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        input=stdin,
        capture_output=True,
        timeout=12,
    )
    return result


def has_password(alias: str) -> bool:
    if not alias:
        return False
    if alias in _HAS_CACHE:
        return _HAS_CACHE[alias]
    found = False
    directory = _secrets_dir()
    if directory:
        found = os.path.isfile(_file_path(alias))
    else:
        try:
            found = _run_windows("has", alias).returncode == 0
        except Exception:
            found = False
    _HAS_CACHE[alias] = found
    return found


def get_password(alias: str) -> str | None:
    if not alias:
        return None
    directory = _secrets_dir()
    if directory:
        path = _file_path(alias)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    try:
        result = _run_windows("get", alias)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace")


def store_password(alias: str, password: str, user: str = "") -> None:
    if not alias:
        raise RuntimeError("缺少 Host 别名，无法保存密码")
    if not password:
        delete_password(alias)
        return
    directory = _secrets_dir()
    if directory:
        os.makedirs(directory, exist_ok=True)
        path = _file_path(alias)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(password)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        _HAS_CACHE[alias] = True
        return
    result = _run_windows("set", alias, password=password, user=user)
    if result.returncode != 0:
        err = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        raise RuntimeError(err or "写入 Windows 凭据库失败")
    _HAS_CACHE[alias] = True


def delete_password(alias: str) -> None:
    if not alias:
        return
    directory = _secrets_dir()
    if directory:
        path = _file_path(alias)
        if os.path.isfile(path):
            os.remove(path)
        _HAS_CACHE.pop(alias, None)
        return
    try:
        _run_windows("delete", alias)
    except Exception:
        pass
    _HAS_CACHE.pop(alias, None)


def askpass_prompt_is_host_key(prompt: str) -> bool:
    text = (prompt or "").lower()
    needles = (
        "yes/no", "fingerprint", "authenticity",
        "continue connecting", "are you sure",
    )
    return any(item in text for item in needles)


def run_askpass(argv=None) -> int:
    """给 OpenSSH SSH_ASKPASS 用：只在密码提示时输出密码。"""
    argv = sys.argv[1:] if argv is None else argv
    prompt = " ".join(argv)
    if askpass_prompt_is_host_key(prompt):
        return 1
    alias = os.environ.get("HOST_DECK_ASKPASS_ALIAS") or ""
    password = get_password(alias)
    if not password:
        return 1
    sys.stdout.write(password if password.endswith("\n") else password + "\n")
    sys.stdout.flush()
    return 0
