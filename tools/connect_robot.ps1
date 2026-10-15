# 关掉 Realtek 千兆口节能 -> 设 192.168.0.100/24 -> 连机器人。需管理员权限。
$ErrorActionPreference = 'Continue'
$nicDesc = 'Realtek PCIe GbE Family Controller'
$log = Join-Path (Split-Path $PSScriptRoot -Parent) 'tools\connect.log'
$adapter = Get-NetAdapter -InterfaceDescription $nicDesc
$idx = $adapter.ifIndex

"=== $(Get-Date -Format 'HH:mm:ss') start, ifIndex=$idx ===" | Tee-Object $log

foreach ($kw in '*EEE', 'EnableGreenEthernet', 'PowerSavingMode') {
    try {
        Set-NetAdapterAdvancedProperty -Name $adapter.Name -RegistryKeyword $kw -RegistryValue 0 -ErrorAction Stop
        "energy  $kw -> 0 (关闭)" | Tee-Object $log -Append
    } catch {
        "energy  $kw FAILED: $($_.Exception.Message)" | Tee-Object $log -Append
    }
}

Start-Sleep -Seconds 3
$link = Get-NetAdapter -InterfaceDescription $nicDesc
"link after props: $($link.Status) / $($link.LinkSpeed)" | Tee-Object $log -Append

Set-NetIPInterface -InterfaceIndex $idx -Dhcp Disabled -ErrorAction SilentlyContinue | Out-Null
Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
New-NetIPAddress -InterfaceIndex $idx -IPAddress 192.168.0.100 -PrefixLength 24 | Out-Null
Set-DnsClientServerAddress -InterfaceIndex $idx -ResetServerAddresses
"ip set: 192.168.0.100/24, dhcp off" | Tee-Object $log -Append

Start-Sleep -Seconds 4
foreach ($target in '192.168.0.10', '192.168.10.10', '192.168.152.2') {
    $r = Test-Connection -ComputerName $target -Count 2 -Quiet -ErrorAction SilentlyContinue
    "ping $target => $r" | Tee-Object $log -Append
}

"--- ARP on this NIC ---" | Tee-Object $log -Append
Get-NetNeighbor -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.State -ne 'Invalid' } |
    ForEach-Object { "  $($_.IPAddress)  $($_.MacAddress)  $($_.State)" } | Tee-Object $log -Append

"--- TCP ports on responders ---" | Tee-Object $log -Append
foreach ($h in '192.168.0.10', '192.168.10.10', '192.168.152.2') {
    foreach ($p in 80, 10001, 10003, 10004, 10005, 10006, 10502, 1883) {
        $c = New-Object Net.Sockets.TcpClient
        try {
            $c.Connect($h, $p); "  $h : $p  OPEN" | Tee-Object $log -Append
        } catch {
        } finally { $c.Close() }
    }
}
"=== done $(Get-Date -Format 'HH:mm:ss') ===" | Tee-Object $log -Append
