Purpose:
    This document contains instructions for copy and pasting FileMaker 
    scripts to and from cursor in a Windows OS

Created:
    May 2nd, 2026. FM version: 22.0.5.500

Instructions:

Copying from FileMaker:

-first paste this script into your Powershell terminal
Add-Type -AssemblyName System.Windows.Forms; $d=[System.Windows.Forms.Clipboard]::GetData("Mac-XMSS"); if($d -is [System.IO.MemoryStream]){$b=$d.ToArray()}else{$b=[System.Text.Encoding]::UTF8.GetBytes($d)}; if($b[4] -eq 0x3C){ $xml=[System.Text.Encoding]::UTF8.GetString($b, 4, $b.Length-4); $xml | Set-Clipboard; Write-Host "Dynamic Extraction Successful ($($b.Length-4) bytes). Ready for Cursor." -F Green } else { Write-Warning "Clipboard format mismatch. Ensure you copied a script step." }

-next copy the *lines* of FileMaker script you want to import

-lastly run the command in your Powershell terminal and you should be able to paste to cursor or another plaintext editor



Copying back to FileMaker:

-first paste this script into your Powershell terminal
$x=(Get-Clipboard -Raw)-replace'<\?xml.*?\?>',''-replace'[\u00A0\u202F\u2007\u200B]',' ';$c='[DllImport("user32.dll")] public static extern bool OpenClipboard(IntPtr h); [DllImport("user32.dll")] public static extern bool CloseClipboard(); [DllImport("user32.dll")] public static extern bool EmptyClipboard(); [DllImport("user32.dll")] public static extern IntPtr SetClipboardData(uint f, IntPtr h); [DllImport("user32.dll")] public static extern uint RegisterClipboardFormat(string l); [DllImport("kernel32.dll")] public static extern IntPtr GlobalAlloc(uint f, IntPtr s); [DllImport("kernel32.dll")] public static extern IntPtr GlobalLock(IntPtr h); [DllImport("kernel32.dll")] public static extern bool GlobalUnlock(IntPtr h);';if(-not([System.Management.Automation.PSTypeName]'Clipboard.W32').Type){Add-Type -MemberDefinition $c -Name "W32" -Namespace "Clipboard"};if([Clipboard.W32]::OpenClipboard(0)){[Clipboard.W32]::EmptyClipboard();$b=[System.Text.Encoding]::UTF8.GetBytes($x.Trim());$len=[System.BitConverter]::GetBytes($b.Length);$f=New-Object byte[]($b.Length+4);[Array]::Copy($len,0,$f,0,4);[Array]::Copy($b,0,$f,4,$b.Length);$h=[Clipboard.W32]::GlobalAlloc(0x42,$f.Length+1);[System.Runtime.InteropServices.Marshal]::Copy($f,0,[Clipboard.W32]::GlobalLock($h),$f.Length);[Clipboard.W32]::GlobalUnlock($h);[Clipboard.W32]::SetClipboardData([Clipboard.W32]::RegisterClipboardFormat("Mac-XMSS"),$h);[Clipboard.W32]::CloseClipboard();Write-Host "Dynamic Injection Success! Header set to $($b.Length) bytes. Paste now." -F Green}

-next copy the xml code to convert from cursor (or whatever editor)

-lastly run the script in Powershell and you should be able to paste the lines back into Filemaker


Special Notes:
these commands are designed to take advantage of the FileMaker scripts
using Mac-XMSS format

copy the lines you want rather than an entire script from the sidebar, that being said ensure your lines are a valid block (no missing end if ect.)