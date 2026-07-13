export function speakBrowser(text: string, language = 'zh-CN') {
  if (!('speechSynthesis' in window)) return { ok: false, reason: 'speech_synthesis_unavailable' }
  window.speechSynthesis.cancel()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = language
  window.speechSynthesis.speak(utterance)
  return { ok: true }
}
export function stopBrowserVoice() { if ('speechSynthesis' in window) window.speechSynthesis.cancel() }
