# Open Windows Firewall for the local LiveKit-SIP testing stack.
# Run as Administrator: right-click -> Run with PowerShell (elevated).

$rules = @(
    @{ Name = "AloEgy-Local-LiveKit-WS";  Port = 7880;          Proto = "TCP" },
    @{ Name = "AloEgy-Local-LiveKit-RTC"; Port = "50000-50020"; Proto = "UDP" },
    @{ Name = "AloEgy-Local-SIP";         Port = 5060;          Proto = "UDP" },
    @{ Name = "AloEgy-Local-RTP";         Port = "10000-10100"; Proto = "UDP" }
)

foreach ($rule in $rules) {
    Remove-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue
    New-NetFirewallRule `
        -DisplayName $rule.Name `
        -Direction Inbound `
        -Protocol $rule.Proto `
        -LocalPort $rule.Port `
        -Action Allow `
        -Profile Any `
        -ErrorAction Stop
    Write-Host "allowed $($rule.Proto)/$($rule.Port) - $($rule.Name)"
}

Write-Host ""
Write-Host "Done. Issabel can now reach this Windows host on the LAN."
