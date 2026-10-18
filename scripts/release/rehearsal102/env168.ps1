# Dot-source before every 168 rehearsal command. All mutable state stays in gate168.
$taskRoot = 'D:\MentorFlow\.local\test-tmp\gate168'
New-Item -ItemType Directory -Force -Path `
    "$taskRoot\local", "$taskRoot\launcher-local", "$taskRoot\temp", `
    "$taskRoot\logs", "$taskRoot\screenshots", "$taskRoot\webroot\updates\app\stable" | Out-Null
$env:MF_UAT_ROOT = $taskRoot
$env:MF_UAT_APP_ID = 'MentorFlow.UAT.gate168'
$env:LOCALAPPDATA = "$taskRoot\local"
$env:TEMP = "$taskRoot\temp"
$env:TMP = "$taskRoot\temp"
$env:TMPDIR = "$taskRoot\temp"
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'UTF-8'
$env:PYTHONPATH = 'D:\MentorFlow\.local\test-tmp\installer\build-tools;D:\MentorFlow-office\runtime\repo'
$env:QTWEBENGINE_REMOTE_DEBUGGING = '127.0.0.1:18861'
$env:QTWEBENGINE_CHROMIUM_FLAGS = '--disable-gpu'
$env:MF_CDP_ENDPOINT = 'http://127.0.0.1:18861'
foreach ($name in @('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')) {
    Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue
}
Write-Output "168 UAT paths configured: $taskRoot"
