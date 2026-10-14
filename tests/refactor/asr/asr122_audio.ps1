# Explicit opt-in SAPI corpus generation. No downloads, microphone or product writes.
param([Parameter(Mandatory=$true)][string]$Root)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Speech
$rootPath=[IO.Path]::GetFullPath($Root)
if (-not $rootPath.StartsWith('D:\MentorFlow\.local\test-tmp\asr122', [StringComparison]::OrdinalIgnoreCase)) {throw 'Use the task directory'}
$synth=New-Object System.Speech.Synthesis.SpeechSynthesizer
$voices=@($synth.GetInstalledVoices() | Where-Object {$_.Enabled -and $_.VoiceInfo.Culture.Name -like 'zh*'} | Select-Object -First 2)
if ($voices.Count -eq 0) {throw 'No installed Chinese SAPI voice'}
$format=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono)
$sentences=Get-Content -LiteralPath (Join-Path $rootPath 'sentences-input.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$meta=@();$vi=0
try {
 foreach($v in $voices) {
  $synth.SelectVoice($v.VoiceInfo.Name)
  foreach($rate in @(0,2)) {
   $synth.Rate=$rate
   foreach($sentence in $sentences) {
    $id=$sentence.id+'-v'+$vi+'-r'+$rate
    $path=Join-Path $rootPath ('wav/'+$id+'.wav')
    New-Item -ItemType Directory -Force (Split-Path $path) | Out-Null
    $synth.SetOutputToWaveFile($path,$format)
    $synth.Speak($sentence.text)
    $synth.SetOutputToNull()
    $meta+=@{id=$id;sentence_id=$sentence.id;voice=$v.VoiceInfo.Name;rate=$rate;path=$path;noise='clean'}
   }
  }
  $vi++
 }
 $meta | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $rootPath 'audio-index.json') -Encoding UTF8
 # Controlled internal pauses: concatenate these two SAPI fragments later,
 # rather than relying on punctuation-dependent synthesized silence.
 $synth.SelectVoice($voices[0].VoiceInfo.Name);$synth.Rate=0
 foreach($row in (Get-Content -LiteralPath (Join-Path $rootPath 'pause-input.json') -Raw -Encoding UTF8 | ConvertFrom-Json)) {
  for($i=0;$i -lt 2;$i++) {
   $synth.SetOutputToWaveFile((Join-Path $rootPath ('wav/'+$row.id+'-'+$i+'.wav')),$format)
   $synth.Speak($row.fragments[$i]);$synth.SetOutputToNull()
  }
 }
} finally {$synth.Dispose()}
