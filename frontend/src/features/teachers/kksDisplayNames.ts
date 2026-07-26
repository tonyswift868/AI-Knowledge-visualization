// Keep the source card name as the fallback. Only aliases that are already
// unambiguous in the local roster are translated; unknown names are never
// guessed from kana/romaji.
const KKS_DISPLAY_ALIASES: Record<string, string> = {
  "Takamaki Ann": "高卷杏",
  "高巻 杏": "高卷杏",
  "高卷杏": "高卷杏",
  "Nagatoro": "长瀞",
  "長瀞": "长瀞",
  "长瀞": "长瀞",
  "ラム": "拉姆",
  "拉姆": "拉姆",
  "小鳥遊 六花": "小鸟游六花",
  "六花 小鳥遊": "小鸟游六花",
  "小鸟游六花": "小鸟游六花",
  "早坂 愛": "早坂爱",
  "愛 早坂": "早坂爱",
  "早坂爱": "早坂爱",
  "高木さん": "高木同学",
  "高木同学": "高木同学",
  "比嘉 ひかり": "比嘉光",
  "比嘉光": "比嘉光",
}

export function friendlyKksDisplayName(value?: string | null, category?: string | null): string {
  const source = String(value || "").trim()
  const translated = KKS_DISPLAY_ALIASES[source] || source
  const categoryLeaf = String(category || "").split("/").pop()?.trim()
  if (categoryLeaf && translated.startsWith(`${categoryLeaf} - `)) {
    return translated.slice(categoryLeaf.length + 3).trim() || translated
  }
  return translated
}
