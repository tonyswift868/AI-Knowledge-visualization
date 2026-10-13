/** Remove only paired prose highlight markers from the classroom's TTS input.
 * Hold an unfinished highlighted span until it closes; code/math stay literal
 * for the existing speech preprocessor. No changes to displayed or saved text.
 */
export function focusSpeech(text: string, final = false) {
  const literals = /(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:\1|$)|(`+)[^`]*?(?:\2|$)|\$\$[\s\S]*?(?:\$\$|$)|\$[^$\n]*(?:\$|$)/g
  const prose = (value: string) => {
    if (!final) {
      // Delimiters may be split into one-character on_text callbacks.
      value = value.replace(/(?<![=\\])=$/, '')
      const markers = [...value.matchAll(/(?<![=\\])==(?!=)/g)]
      if (markers.length % 2) value = value.slice(0, markers.at(-1)!.index)
    }
    return value.replace(/(?<![=\\])==(?!=)([^\n]*?)(?<![=\\])==(?!=)/g, '$1')
  }
  let result = '', end = 0
  for (const match of text.matchAll(literals)) {
    result += prose(text.slice(end, match.index)) + match[0]
    end = match.index! + match[0].length
  }
  return result + prose(text.slice(end))
}
