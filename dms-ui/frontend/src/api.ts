import axios from 'axios'

const TOKEN_KEY = 'dms_token'
const MATRIX_ID_KEY = 'dms_matrix_id'
const SESSION_START_KEY = 'dms_session_start'
const SESSION_MS = 8 * 60 * 60 * 1000 // 8 hours

// ── Session helpers ──────────────────────────────────────────────────────────

export function storeSession(token: string, matrixId: string) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(MATRIX_ID_KEY, matrixId)
  localStorage.setItem(SESSION_START_KEY, String(Date.now()))
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(MATRIX_ID_KEY)
  localStorage.removeItem(SESSION_START_KEY)
}

export function isSessionValid(): boolean {
  const token = localStorage.getItem(TOKEN_KEY)
  const start = localStorage.getItem(SESSION_START_KEY)
  if (!token || !start) return false
  return Date.now() - parseInt(start, 10) < SESSION_MS
}

export function getMatrixId(): string | null {
  return localStorage.getItem(MATRIX_ID_KEY)
}

// ── Axios client ─────────────────────────────────────────────────────────────

const client = axios.create({ baseURL: '/api' })

client.interceptors.request.use((config) => {
  if (!isSessionValid()) {
    clearSession()
    throw new axios.Cancel('Session expired')
  }
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) config.headers['Authorization'] = `Bearer ${token}`
  return config
})

client.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err?.response?.status === 401) clearSession()
    return Promise.reject(err)
  }
)

// ── Auth ─────────────────────────────────────────────────────────────────────

export const authApi = {
  requestOtp: (matrixId: string) =>
    axios.post('/api/auth/request', { matrix_id: matrixId }),
  verifyOtp: (matrixId: string, otp: string) =>
    axios.post('/api/auth/verify', { matrix_id: matrixId, otp }),
}

// ── Profile ──────────────────────────────────────────────────────────────────

export const profileApi = {
  get: () => client.get('/profile'),
  update: (data: Partial<ProfilePayload>) => client.put('/profile', data),
  checkin: () => client.post('/checkin'),
  getAudit: () => client.get('/audit'),
}

// ── Dream Memory ──────────────────────────────────────────────────────────────

export const memoryApi = {
  /** List all AI memories for the current user, optionally including deleted ones. */
  list: (includeDeleted = false) =>
    client.get<MemoryListResponse>(`/memories?include_deleted=${includeDeleted}`),

  /** Get a single memory with its full version history. */
  get: (id: number) =>
    client.get<MemoryDetailResponse>(`/memories/${id}`),

  /** Edit the text of a memory (user-initiated correction). */
  edit: (id: number, memoryText: string) =>
    client.put<{ status: string; memory: UserMemory }>(`/memories/${id}`, {
      memory_text: memoryText,
    }),

  /** Soft-delete a memory (preserves version history). */
  delete: (id: number) =>
    client.delete<{ status: string; memory_id: number }>(`/memories/${id}`),

  /** Restore a previously soft-deleted memory. */
  restore: (id: number) =>
    client.post<{ status: string; memory_id: number }>(`/memories/${id}/restore`),

  /** Get Dream Engine status and last cycle info. */
  getDreamStatus: () =>
    client.get<DreamStatus>('/dream/status'),
}

// ── Types ────────────────────────────────────────────────────────────────────

export interface EmergencyContact {
  name: string
  relationship: string
  phone: string
  matrix_id: string
  email: string
}

export interface SocialMedia {
  platform: string
  url: string
}

export interface ReleaseAction {
  type: 'matrix_dm' | 'matrix_room' | 'webhook'
  target: string
}

export interface Profile {
  matrix_id: string
  display_name: string | null
  status: string
  missing_threshold_h: number
  last_active_ts: number | null
  registration_ts: number | null
  location: string
  legal_name: string | null
  date_of_birth: string | null
  physical_address: string | null
  emergency_contacts: EmergencyContact[]
  social_media: SocialMedia[]
  vault_text: string | null
  release_actions: ReleaseAction[]
  vault_created_ts: number | null
  vault_released_ts: number | null
}

export type ProfilePayload = Omit<Profile, 'matrix_id' | 'display_name' | 'status' | 'registration_ts' | 'vault_created_ts' | 'vault_released_ts'>

export interface UserMemory {
  id: number
  matrix_id: string
  category: string
  memory_text: string
  version: number
  created_ts: number
  updated_ts: number
  is_deleted: number  // 0 or 1
  last_edited_by: string  // 'dream_engine' | 'user'
}

export interface MemoryHistoryEntry {
  id: number
  memory_id: number
  matrix_id: string
  version: number
  memory_text: string
  archived_ts: number
  archived_by: string
}

export interface MemoryListResponse {
  matrix_id: string
  total: number
  memories: UserMemory[]
  by_category: Record<string, UserMemory[]>
}

export interface MemoryDetailResponse {
  memory: UserMemory
  history: MemoryHistoryEntry[]
}

export interface DreamCycle {
  id: number
  started_ts: number
  completed_ts: number | null
  status: string  // 'running' | 'completed' | 'failed' | 'skipped'
  messages_processed: number
  user_memories_created: number
  user_memories_updated: number
  op_memories_created: number
  op_memories_updated: number
  error_message: string | null
}

export interface DreamStatus {
  last_cycle: DreamCycle | null
  recent_cycles: DreamCycle[]
  next_scheduled_utc: string
  engine_status: string  // 'active' | 'never_run'
}

export const SOCIAL_PLATFORMS = [
  'Twitter / X', 'Mastodon', 'Bluesky', 'Facebook', 'Instagram',
  'LinkedIn', 'YouTube', 'TikTok', 'Telegram', 'Signal', 'GitHub',
  'Reddit', 'Other',
]

export const THRESHOLD_OPTIONS = [
  { label: '24 hours', hours: 24 },
  { label: '48 hours', hours: 48 },
  { label: '72 hours (default)', hours: 72 },
  { label: '7 days', hours: 168 },
  { label: '14 days', hours: 336 },
  { label: '30 days', hours: 720 },
]

/** Human-readable labels for memory categories */
export const MEMORY_CATEGORY_LABELS: Record<string, string> = {
  symptoms: 'Symptoms & Health',
  legal_status: 'Legal Status',
  history: 'Personal History',
  contacts: 'Key Contacts',
  preferences: 'Preferences',
  threat_profile: 'Threat Profile',
  notes: 'General Notes',
}
