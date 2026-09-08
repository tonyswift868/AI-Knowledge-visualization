# Optional production-only Authenticode hook. No certificate generation/import.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PackageRoot,
    [Parameter(Mandatory)][ValidatePattern('^[0-9A-Fa-f]{40}$')][string]$CertificateThumbprint,
    [Parameter(Mandatory)][string]$TimestampServer,
    [Parameter(Mandatory)][string]$ReceiptPath
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$signRoot = (Resolve-Path -LiteralPath $PackageRoot).ProviderPath
$signTimestamp = [Uri]$TimestampServer
if (-not $signTimestamp.IsAbsoluteUri -or $signTimestamp.Scheme -ne 'https' -or $signTimestamp.UserInfo -or $signTimestamp.Fragment) {
    throw 'Explicit HTTPS timestamp server required'
}
$signCertificates = @(Get-ChildItem -Path Cert:\CurrentUser\My,Cert:\LocalMachine\My |
    Where-Object { $_.Thumbprint -eq $CertificateThumbprint -and $_.HasPrivateKey })
if ($signCertificates.Count -eq 0) { throw 'PRODUCTION_SIGNING=NOT_CONFIGURED: matching private certificate unavailable' }
$signCertificate = $signCertificates[0]
$signCodeUsage = @($signCertificate.Extensions |
    Where-Object { $_ -is [System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension] } |
    ForEach-Object { $_.EnhancedKeyUsages } |
    Where-Object { $_.Value -eq '1.3.6.1.5.5.7.3.3' })
if ($signCodeUsage.Count -eq 0) {
    throw 'Certificate is not authorized for code signing'
}
$signChain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
try {
    if (-not $signChain.Build($signCertificate)) { throw 'Certificate trust chain invalid; self-signed test certificates are not production trust' }
} finally { $signChain.Dispose() }
$signFiles = @(Get-ChildItem -LiteralPath $signRoot -Recurse -File |
    Where-Object { $_.Extension.ToLowerInvariant() -in @('.exe','.dll','.pyd') })
if ($signFiles.Count -eq 0) { throw 'No PE files to sign' }
$signRows = @()
foreach ($signFile in $signFiles) {
    if (($signFile.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse point refused' }
    $signResult = Set-AuthenticodeSignature -LiteralPath $signFile.FullName -Certificate $signCertificate -HashAlgorithm SHA256 -TimestampServer $TimestampServer
    $signVerified = Get-AuthenticodeSignature -LiteralPath $signFile.FullName
    if ($signResult.Status -ne 'Valid' -or $signVerified.Status -ne 'Valid' -or
        $signVerified.SignerCertificate.Thumbprint -ne $CertificateThumbprint -or $null -eq $signVerified.TimeStamperCertificate) {
        throw "Authenticode verification failed: $($signFile.Name)"
    }
    $signRows += [ordered]@{ path = $signFile.FullName.Substring($signRoot.Length + 1).Replace('\','/');
        sha256 = (Get-FileHash -LiteralPath $signFile.FullName -Algorithm SHA256).Hash.ToLowerInvariant();
        status = 'Valid'; signer_thumbprint = $signVerified.SignerCertificate.Thumbprint;
        timestamp_thumbprint = $signVerified.TimeStamperCertificate.Thumbprint }
}
$signReceipt = [ordered]@{ PRODUCTION_SIGNING = 'CONFIGURED_AND_VERIFIED'; signed_files = $signRows }
# Machine-generated receipt is consumed before manifest hashing, not a trust root.
[IO.File]::WriteAllText($ReceiptPath, ($signReceipt | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))
