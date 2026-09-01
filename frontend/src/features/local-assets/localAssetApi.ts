export type LocalAssetAvailability =
  | 'AVAILABLE_LOCAL'
  | 'MISSING_ON_DEVICE'
  | 'LINKED_FILE_MISSING'
  | 'REDOWNLOAD_AVAILABLE'
  | 'RESTORABLE_FROM_PACKAGE'

export type LocalAssetAction = 'relink' | 'redownload' | 'restore'

export type LocalAssetRecord = {
  local_asset_id: string
  filename: string
  mime_type: string
  size_bytes: number
  sha256: string
  source_kind: 'MANAGED' | 'LINKED'
  availability: LocalAssetAvailability
  portable: boolean
  verified_source_url?: string | null
  actions: LocalAssetAction[]
  labels: string[]
}

export type ServerAssetManifestDto = {
  local_asset_id: string
  filename: string
  mime_type: string
  size_bytes: number
  sha256: string
  import_mode: 'MANAGED' | 'LINKED'
  portability: 'PORTABLE' | 'NONPORTABLE'
  availability: 'AVAILABLE' | 'MISSING'
  verified_source_url?: string
  device_id?: string
}

export interface LocalAssetClient {
  list(): Promise<LocalAssetRecord[]>
  importManaged(file: File): Promise<LocalAssetRecord>
  importLinked(file: File): Promise<LocalAssetRecord>
  relink(localAssetId: string, file: File): Promise<LocalAssetRecord>
  redownload(localAssetId: string): Promise<LocalAssetRecord>
  restore(localAssetId: string, packageFile: File): Promise<LocalAssetRecord>
  serverDto(localAssetId: string): Promise<ServerAssetManifestDto>
}

declare global {
  interface Window {
    mentorflowLocalAssetBridge?: LocalAssetClient
  }
}

const unavailable = (): never => {
  throw new Error('WINDOWS_DESKTOP_REQUIRED')
}

const unavailableClient: LocalAssetClient = {
  list: async () => [],
  importManaged: async () => unavailable(),
  importLinked: async () => unavailable(),
  relink: async () => unavailable(),
  redownload: async () => unavailable(),
  restore: async () => unavailable(),
  serverDto: async () => unavailable(),
}

export function createLocalAssetClient(): LocalAssetClient {
  return window.mentorflowLocalAssetBridge || unavailableClient
}

export const SERVER_DTO_KEYS = new Set([
  'local_asset_id',
  'filename',
  'mime_type',
  'size_bytes',
  'sha256',
  'import_mode',
  'portability',
  'availability',
  'verified_source_url',
  'device_id',
])

export function assertServerDtoBoundary(dto: ServerAssetManifestDto): void {
  for (const key of Object.keys(dto)) {
    if (!SERVER_DTO_KEYS.has(key)) throw new Error('SERVER_DTO_FIELD_REJECTED')
  }
  const values: unknown[] = Object.values(dto as unknown as Record<string, unknown>)
  if (values.some(value => value instanceof Uint8Array || value instanceof ArrayBuffer || value instanceof Blob)) {
    throw new Error('SERVER_DTO_RAW_BYTES_REJECTED')
  }
  const windowsAbsolute = /^[a-zA-Z]:[\\/]/
  if (values.some(value => typeof value === 'string' && windowsAbsolute.test(value))) {
    throw new Error('SERVER_DTO_ABSOLUTE_PATH_REJECTED')
  }
}
