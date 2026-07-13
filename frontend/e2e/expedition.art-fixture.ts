import { createHash } from 'node:crypto'
import { deflateRawSync, deflateSync } from 'node:zlib'

type FixtureOptions = { missing?: string; badHash?: string; traversal?: boolean }

function crc32(input: Buffer) {
  let crc = 0xffffffff
  for (const byte of input) {
    crc ^= byte
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0)
  }
  return (crc ^ 0xffffffff) >>> 0
}

function png(width: number, height: number) {
  const header = Buffer.alloc(13)
  header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 6
  const row = Buffer.alloc(1 + width * 4)
  const raw = Buffer.concat(Array.from({ length: height }, () => row))
  const chunk = (name: string, data: Buffer) => { const tag = Buffer.from(name); const output = Buffer.alloc(12); output.writeUInt32BE(data.length, 0); output.writeUInt32BE(crc32(Buffer.concat([tag, data])), 8); return Buffer.concat([output.subarray(0, 4), tag, data, output.subarray(8)]) }
  return Buffer.concat([Buffer.from('\x89PNG\r\n\x1a\n', 'binary'), chunk('IHDR', header), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))])
}

function zip(files: Map<string, Buffer>) {
  const local: Buffer[] = []
  const central: Buffer[] = []
  let offset = 0
  for (const [name, data] of files) {
    const filename = Buffer.from(name)
    const compressed = deflateRawSync(data, { level: 9 })
    const crc = crc32(data)
    const localHeader = Buffer.alloc(30)
    localHeader.writeUInt32LE(0x04034b50, 0); localHeader.writeUInt16LE(20, 4); localHeader.writeUInt16LE(8, 6); localHeader.writeUInt16LE(8, 8)
    localHeader.writeUInt32LE(crc, 14); localHeader.writeUInt32LE(compressed.length, 18); localHeader.writeUInt32LE(data.length, 22); localHeader.writeUInt16LE(filename.length, 26)
    local.push(Buffer.concat([localHeader, filename, compressed]))
    const centralHeader = Buffer.alloc(46)
    centralHeader.writeUInt32LE(0x02014b50, 0); centralHeader.writeUInt16LE(20, 4); centralHeader.writeUInt16LE(20, 6); centralHeader.writeUInt16LE(8, 10); centralHeader.writeUInt16LE(8, 12)
    centralHeader.writeUInt32LE(crc, 16); centralHeader.writeUInt32LE(compressed.length, 20); centralHeader.writeUInt32LE(data.length, 24); centralHeader.writeUInt16LE(filename.length, 28); centralHeader.writeUInt32LE(offset, 42)
    central.push(Buffer.concat([centralHeader, filename]))
    offset += local[local.length - 1].length
  }
  const localData = Buffer.concat(local); const centralData = Buffer.concat(central)
  const end = Buffer.alloc(22); end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(files.size, 8); end.writeUInt16LE(files.size, 10); end.writeUInt32LE(centralData.length, 12); end.writeUInt32LE(localData.length, 16)
  return Buffer.concat([localData, centralData, end])
}

export function makeDevArtPack(heroIds: string[], version: string, options: FixtureOptions = {}) {
  const root = 'expedition_art_pack_v1/'
  const files = new Map<string, Buffer>()
  const assets: Array<Record<string, string>> = []
  const sizes: Record<string, [number, number]> = { portrait: [256, 256], avatar: [128, 128], skill_cutin: [256, 128], skill_icon: [64, 64] }
  for (const heroId of heroIds) {
    const metadataPath = `heroes/${heroId}/metadata.json`
    const metadata = { hero_id: heroId, display_name: heroId, visual_archetype: 'DEV ART PACK FIXTURE', age_presentation: 'adult', portrait_focal_point: { x: 0.5, y: 0.4 }, avatar_crop: {}, cutin_crop: {}, dominant_colors: ['#5ec8a1'], battle_scale: 1, battle_offset: { x: 0, y: 0 }, facing: 'right', skill_fx_tags: ['fire'], source: 'DEV ART PACK FIXTURE', author: 'Playwright fixture', license: 'test-only', sha256: {} }
    files.set(root + metadataPath, Buffer.from(JSON.stringify(metadata)))
    assets.push({ hero_id: heroId, asset_kind: 'metadata', path: metadataPath })
    for (const [kind, [width, height]] of Object.entries(sizes)) {
      const key = `${heroId}:${kind}`
      const path = `heroes/${heroId}/${kind}.png`
      if (options.missing === key) continue
      files.set(root + path, png(width, height))
      assets.push({ hero_id: heroId, asset_kind: kind, path })
    }
  }
  if (options.traversal) assets.push({ hero_id: heroIds[0], asset_kind: 'portrait', path: '../escape.png' })
  const manifest = { pack_id: 'e2e-dev-art-pack', version, minimum_runtime_version: '1.0.0', created_at: '2026-07-13T00:00:00Z', hero_count: 24, required_hero_ids: heroIds, assets, style_name: 'DEV ART PACK FIXTURE', source: 'dev_art_fixture', checksum: '', license_notes: ['DEV ART PACK FIXTURE; never official art'] }
  files.set(root + 'manifest.json', Buffer.from(JSON.stringify(manifest)))
  files.set(root + 'attribution.json', Buffer.from(JSON.stringify({ source: 'DEV ART PACK FIXTURE', author: 'Playwright fixture', license: 'test-only' })))
  const checksums: Record<string, string> = {}
  for (const [path, data] of files) checksums[path.slice(root.length)] = createHash('sha256').update(data).digest('hex')
  if (options.badHash) checksums[options.badHash] = '0'.repeat(64)
  files.set(root + 'SHA256SUMS.json', Buffer.from(JSON.stringify({ files: checksums })))
  return zip(files)
}
