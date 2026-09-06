[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Launcher)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput([IO.File]::ReadAllText($Launcher, [Text.Encoding]::UTF8), [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Launcher parse failure' }
$function = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Test-SupportedEndpoint'}, $true)
if ($null -eq $function) { throw 'Probe missing' }
$source = $function.Extent.Text
if ($source -notmatch '--cacert' -or $source -notmatch '\$probe.ExitCode' -or $source -match '\$LASTEXITCODE -ne') { throw 'Probe security/exit contract regression' }
# Replace only process creation in the extracted function; do not execute the launcher.
$source = $source.Replace('[Diagnostics.Process]::Start($probeInfo)', '(Start-ProbeFixture)')
Invoke-Expression $source
$certificate = 'D:\test-public-ca.crt'
function New-CleanProcess($path) {
    return [pscustomobject]@{Arguments='';RedirectStandardOutput=$false;RedirectStandardError=$false}
}
function Start-ProbeFixture {
    if ($script:fixtureThrow) { throw 'Synthetic start failure' }
    $reader = [pscustomobject]@{Body=$script:fixtureBody}
    $reader | Add-Member ScriptMethod ReadToEndAsync { return [pscustomobject]@{Result=$this.Body} }
    $p = [pscustomobject]@{ExitCode=$script:fixtureCode;StandardOutput=$reader;StandardError=$reader}
    $p | Add-Member ScriptMethod WaitForExit { param($timeout) return $script:fixtureWait }
    $p | Add-Member ScriptMethod Kill { $script:killed=$true }
    $p | Add-Member ScriptMethod Dispose { $script:disposed=$true }
    return $p
}
$cases = @(
    @(0,'{"issuer":"https://localhost:18080/realms/mentorflow"}',$true,$false,$true),
    @(22,'{"issuer":"https://localhost:18080/realms/mentorflow"}',$true,$false,$false),
    @(0,'{"issuer":"https://wrong.invalid"}',$true,$false,$false),
    @(0,'not-json',$true,$false,$false),
    @(0,'{}',$true,$false,$false),
    @(0,'{}',$false,$false,$false),
    @(0,'{}',$true,$true,$false)
)
foreach ($case in $cases) {
    $script:fixtureCode=$case[0]; $script:fixtureBody=$case[1]
    $script:fixtureWait=$case[2]; $script:fixtureThrow=$case[3]
    $script:killed=$false; $script:disposed=$false
    $actual=Test-SupportedEndpoint
    if ($actual -ne $case[4]) { throw 'Endpoint acceptance mismatch' }
    if (-not $script:fixtureThrow -and -not $script:disposed) { throw 'Probe process not disposed' }
    if (-not $script:fixtureWait -and -not $script:killed) { throw 'Timed-out probe not stopped' }
}
'ENDPOINT_FOCUSED_CASES=7 PASS; NO_APP_OR_NETWORK_STARTED'
