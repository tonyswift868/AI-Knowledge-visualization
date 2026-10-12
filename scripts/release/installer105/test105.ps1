$ErrorActionPreference='Stop'
$root='D:/MentorFlow/.local/test-tmp/dir105'
$env:TEMP=Join-Path $root 'temp';$env:TMP=$env:TEMP;$env:TMPDIR=$env:TEMP
$env:PYTHONDONTWRITEBYTECODE='1';$env:PYTHONUTF8='1'
$env:QT_QPA_PLATFORM='offscreen'
& D:/MentorFlow/.venv/Scripts/python.exe -B -m pytest tests/refactor -q -p no:cacheprovider --basetemp (Join-Path $root 'pytest') *> (Join-Path $root 'logs/full.log')
$code=$LASTEXITCODE
Get-Content (Join-Path $root 'logs/full.log') -Tail 35
exit $code
