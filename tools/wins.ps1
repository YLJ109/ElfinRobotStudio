Add-Type @"
using System;using System.Text;using System.Runtime.InteropServices;
public class W{ [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc l, IntPtr p);
 public delegate bool EnumWindowsProc(IntPtr h, IntPtr p);
 [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
}
"@
$cb = [W+EnumWindowsProc]{
  param($h,$p)
  $sb = New-Object Text.StringBuilder 256
  [void][W]::GetWindowText($h,$sb,256)
  $procid = 0
  [void][W]::GetWindowThreadProcessId($h,[ref]$procid)
  if ([W]::IsWindowVisible($h) -and $sb.Length -gt 0) {
    Write-Output ("pid={0}  title={1}" -f $procid, $sb.ToString())
  }
  return $true
}
[void][W]::EnumWindows($cb,[IntPtr]::Zero)
