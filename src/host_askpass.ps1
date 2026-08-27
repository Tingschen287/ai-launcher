$ProgressPreference = 'SilentlyContinue'
$prompt = ($args -join ' ').ToLower()
if ($prompt -match 'yes/no|fingerprint|authenticity|continue connecting|are you sure') {
    exit 1
}
$alias = $env:HOST_DECK_ASKPASS_ALIAS
if (-not $alias) { exit 1 }
$target = "HostDeck/ssh/$alias"
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class HostDeckAskPass {
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
  [DllImport("advapi32.dll", EntryPoint = "CredReadW", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern bool CredRead(string target, uint type, int reservedFlag, out IntPtr credentialPtr);
  [DllImport("advapi32.dll", SetLastError = true)]
  public static extern bool CredFree(IntPtr cred);
}
"@
$ptr = [IntPtr]::Zero
if (-not [HostDeckAskPass]::CredRead($target, 1, 0, [ref]$ptr)) { exit 1 }
try {
    $cred = [Runtime.InteropServices.Marshal]::PtrToStructure($ptr, [type][HostDeckAskPass+NativeCredential])
    if ($cred.CredentialBlob -eq [IntPtr]::Zero -or $cred.CredentialBlobSize -le 0) { exit 1 }
    $n = [int]$cred.CredentialBlobSize
    $bytes = New-Object byte[] $n
    [Runtime.InteropServices.Marshal]::Copy($cred.CredentialBlob, $bytes, 0, $n)
    $utf8 = [Text.Encoding]::UTF8.GetString($bytes)
    if ($utf8.IndexOf([char]0) -ge 0) {
        $secret = [Text.Encoding]::Unicode.GetString($bytes).Trim([char]0)
    } else {
        $secret = $utf8.Trim([char]0)
    }
    [Console]::Out.Write($secret)
    if (-not $secret.EndsWith("`n")) { [Console]::Out.Write("`n") }
} finally {
    [HostDeckAskPass]::CredFree($ptr) | Out-Null
}
exit 0
