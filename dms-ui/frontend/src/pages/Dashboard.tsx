import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Shield, LogOut, RefreshCw, CheckCircle2, AlertTriangle, Clock,
  Plus, Trash2, Save, Loader2, User, Phone, Globe, FileText,
  Bell, History, ChevronDown, ChevronUp, AlertCircle, Brain, Scale,
} from 'lucide-react'
import {
  profileApi, clearSession, isSessionValid, getMatrixId,
  SOCIAL_PLATFORMS, THRESHOLD_OPTIONS,
} from '../api'
import type { Profile, EmergencyContact, SocialMedia, ReleaseAction } from '../api'
import MemoryProfile from '../components/MemoryProfile'
import FOIADashboard from '../components/FOIADashboard'

// ── Helpers ──────────────────────────────────────────────────────────────────

function tsToRelative(ts: number | null): string {
  if (!ts) return 'Never'
  const diff = Date.now() / 1000 - ts
  const h = Math.floor(diff / 3600)
  if (h < 1) return 'Just now'
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function tsToAbsolute(ts: number | null): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString()
}

function nextTrigger(profile: Profile): string {
  if (!profile.last_active_ts) return 'Not started'
  return new Date((profile.last_active_ts + profile.missing_threshold_h * 3600) * 1000).toLocaleString()
}

// ── Section wrapper ───────────────────────────────────────────────────────────

function Section({ title, icon: Icon, children, defaultOpen = true }: {
  title: string; icon: React.ElementType; children: React.ReactNode; defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="card" style={{ marginBottom: '1rem', padding: 0, overflow: 'hidden' }}>
      <button
        onClick={() => setOpen(!open)}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '1rem 1.25rem', background: 'transparent', border: 'none', cursor: 'pointer',
          color: 'var(--text)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Icon size={17} style={{ color: 'var(--primary)' }} />
          <span style={{ fontWeight: 600, fontSize: '0.875rem' }}>{title}</span>
        </div>
        {open ? <ChevronUp size={15} style={{ color: 'var(--text-muted)' }} /> : <ChevronDown size={15} style={{ color: 'var(--text-muted)' }} />}
      </button>
      {open && (
        <div style={{ borderTop: '1px solid var(--border)', padding: '1.25rem 1.5rem' }}>
          {children}
        </div>
      )}
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: '1rem' }}>
      <label style={{ display: 'block', fontSize: '0.7rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '0.4rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        {label}
      </label>
      {children}
    </div>
  )
}

// ── Main ─────────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const matrixId = getMatrixId()

  // Guard: redirect if session expired
  useEffect(() => {
    if (!isSessionValid()) { clearSession(); navigate('/login') }
    const t = setInterval(() => {
      if (!isSessionValid()) { clearSession(); navigate('/login') }
    }, 60_000)
    return () => clearInterval(t)
  }, [navigate])

  // ── Data ──────────────────────────────────────────────────────────────────

  const { data: profile, isLoading, isError } = useQuery<Profile>({
    queryKey: ['profile'],
    queryFn: () => profileApi.get().then((r) => r.data),
    refetchInterval: 30_000,
  })

  const { data: audit } = useQuery<any[]>({
    queryKey: ['audit'],
    queryFn: () => profileApi.getAudit().then((r) => r.data),
  })

  // ── Local edit state ──────────────────────────────────────────────────────

  const [personal, setPersonal] = useState({ legal_name: '', date_of_birth: '', physical_address: '', location: '' })
  const [contacts, setContacts] = useState<EmergencyContact[]>([])
  const [social, setSocial] = useState<SocialMedia[]>([])
  const [vaultText, setVaultText] = useState('')
  const [threshold, setThreshold] = useState(72)
  const [releaseActions, setReleaseActions] = useState<ReleaseAction[]>([])
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (!profile) return
    setPersonal({
      legal_name: profile.legal_name || '',
      date_of_birth: profile.date_of_birth || '',
      physical_address: profile.physical_address || '',
      location: profile.location || '',
    })
    setContacts(profile.emergency_contacts || [])
    setSocial(profile.social_media || [])
    setVaultText(profile.vault_text || '')
    setThreshold(profile.missing_threshold_h)
    setReleaseActions(profile.release_actions || [])
    setDirty(false)
  }, [profile])

  const mark = () => setDirty(true)

  // ── Mutations ─────────────────────────────────────────────────────────────

  const saveMutation = useMutation({
    mutationFn: () => profileApi.update({
      ...personal,
      emergency_contacts: contacts,
      social_media: social,
      vault_text: vaultText,
      missing_threshold_h: threshold,
      release_actions: releaseActions,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['profile'] }); qc.invalidateQueries({ queryKey: ['audit'] }); setDirty(false) },
  })

  const checkinMutation = useMutation({
    mutationFn: () => profileApi.checkin(),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['profile'] }); qc.invalidateQueries({ queryKey: ['audit'] }) },
  })

  const handleLogout = () => { clearSession(); navigate('/login') }

  // ── Loading / error ───────────────────────────────────────────────────────

  if (isLoading) return (
    <div style={{ minHeight: '100vh', background: 'var(--bg)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <Loader2 size={32} className="animate-spin" style={{ color: 'var(--primary)' }} />
    </div>
  )

  if (isError || !profile) return (
    <div style={{ minHeight: '100vh', background: 'var(--bg)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 12 }}>
      <AlertCircle size={36} style={{ color: 'var(--danger)' }} />
      <p style={{ color: 'var(--text)' }}>Failed to load profile.</p>
      <button className="btn btn-ghost" onClick={handleLogout}>Back to Login</button>
    </div>
  )

  const isActive = profile.status === 'ACTIVE'

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div style={{ background: 'var(--bg)', minHeight: '100vh', paddingBottom: '4rem' }}>

      {/* Top bar */}
      <div style={{
        background: 'var(--surface)', borderBottom: '1px solid var(--border)',
        padding: '0 1.5rem', height: 60, display: 'flex', alignItems: 'center',
        justifyContent: 'space-between', position: 'sticky', top: 0, zIndex: 50,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 32, height: 32, borderRadius: 9, background: 'linear-gradient(135deg, #ef4444, #a855f7)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Shield size={16} color="white" />
          </div>
          <span style={{ fontWeight: 700, color: 'var(--text)' }}>Dead Man's Switch</span>
          <span className="badge" style={{
            background: isActive ? 'var(--success-dim)' : 'var(--danger-dim)',
            color: isActive ? 'var(--success)' : 'var(--danger)',
          }}>
            {profile.status}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{matrixId}</span>
          <button className="btn btn-ghost" onClick={handleLogout} style={{ padding: '0.4rem 0.75rem' }}>
            <LogOut size={14} /> Sign Out
          </button>
        </div>
      </div>

      <div style={{ maxWidth: 840, margin: '0 auto', padding: '1.75rem 1.5rem' }}>

        {/* Status banner */}
        <div className="card" style={{
          marginBottom: '1.5rem', padding: '1.25rem 1.5rem',
          background: isActive ? 'linear-gradient(135deg, rgba(34,197,94,0.06), rgba(99,102,241,0.06))' : 'var(--danger-dim)',
          borderColor: isActive ? 'rgba(34,197,94,0.25)' : 'rgba(239,68,68,0.3)',
        }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: '1rem' }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                {isActive
                  ? <CheckCircle2 size={18} style={{ color: 'var(--success)' }} />
                  : <AlertTriangle size={18} style={{ color: 'var(--danger)' }} />}
                <span style={{ fontWeight: 600, color: 'var(--text)' }}>
                  {isActive ? 'Switch is active and monitoring' : `Status: ${profile.status}`}
                </span>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1.25rem', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                <span><Clock size={12} style={{ display: 'inline', marginRight: 4 }} />Last check-in: <strong style={{ color: 'var(--text)' }}>{tsToRelative(profile.last_active_ts)}</strong></span>
                <span>Next trigger: <strong style={{ color: 'var(--text)' }}>{nextTrigger(profile)}</strong></span>
                <span>Threshold: <strong style={{ color: 'var(--text)' }}>{profile.missing_threshold_h}h</strong></span>
              </div>
            </div>
            <button
              className="btn btn-primary"
              style={{ background: 'var(--success)', minWidth: 130 }}
              onClick={() => checkinMutation.mutate()}
              disabled={checkinMutation.isPending}
            >
              {checkinMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : <><RefreshCw size={14} /> Check In Now</>}
            </button>
          </div>
        </div>

        {/* Unsaved changes bar */}
        {dirty && (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '0.6rem 1rem', borderRadius: 10, marginBottom: '1rem',
            background: 'var(--primary-dim)', border: '1px solid rgba(99,102,241,0.3)',
          }}>
            <span style={{ fontSize: '0.85rem', color: 'var(--primary)' }}>You have unsaved changes.</span>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-ghost" style={{ fontSize: '0.8rem', padding: '0.35rem 0.75rem' }}
                onClick={() => {
                  if (!profile) return
                  setPersonal({ legal_name: profile.legal_name || '', date_of_birth: profile.date_of_birth || '', physical_address: profile.physical_address || '', location: profile.location || '' })
                  setContacts(profile.emergency_contacts || [])
                  setSocial(profile.social_media || [])
                  setVaultText(profile.vault_text || '')
                  setThreshold(profile.missing_threshold_h)
                  setReleaseActions(profile.release_actions || [])
                  setDirty(false)
                }}>Discard</button>
              <button className="btn btn-primary" style={{ fontSize: '0.8rem', padding: '0.35rem 0.75rem' }}
                onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
                {saveMutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <><Save size={13} /> Save</>}
              </button>
            </div>
          </div>
        )}

        {saveMutation.isError && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '0.6rem 0.85rem', borderRadius: 8, background: 'var(--danger-dim)', color: 'var(--danger)', marginBottom: '1rem', fontSize: '0.85rem' }}>
            <AlertCircle size={14} />
            {(saveMutation.error as any)?.response?.data?.detail || 'Save failed. Please try again.'}
          </div>
        )}

        {/* ── Personal Details ── */}
        <Section title="Personal Details" icon={User}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 1.5rem' }}>
            <Field label="Legal Name">
              <input className="input" value={personal.legal_name} placeholder="Full legal name"
                onChange={(e) => { setPersonal({ ...personal, legal_name: e.target.value }); mark() }} />
            </Field>
            <Field label="Date of Birth">
              <input className="input" type="date" value={personal.date_of_birth}
                onChange={(e) => { setPersonal({ ...personal, date_of_birth: e.target.value }); mark() }} />
            </Field>
          </div>
          <Field label="Physical Address">
            <textarea className="input" rows={2} value={personal.physical_address} placeholder="Street, City, State, Country"
              onChange={(e) => { setPersonal({ ...personal, physical_address: e.target.value }); mark() }} />
          </Field>
          <Field label="General Location (used by OSINT scanner)">
            <input className="input" value={personal.location} placeholder="City, Country"
              onChange={(e) => { setPersonal({ ...personal, location: e.target.value }); mark() }} />
          </Field>
        </Section>

        {/* ── Emergency Contacts ── */}
        <Section title="Emergency Contacts" icon={Phone}>
          {contacts.map((c, i) => (
            <div key={i} style={{ marginBottom: '1rem', padding: '1rem', borderRadius: 10, background: 'var(--bg)', border: '1px solid var(--border)' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
                <span style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Contact #{i + 1}</span>
                <button className="btn btn-ghost" style={{ padding: '4px 8px', color: 'var(--danger)', border: 'none' }}
                  onClick={() => { setContacts(contacts.filter((_, j) => j !== i)); mark() }}>
                  <Trash2 size={13} />
                </button>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 1.25rem' }}>
                {(['name', 'relationship', 'phone', 'email'] as const).map((field) => (
                  <Field key={field} label={field.charAt(0).toUpperCase() + field.slice(1)}>
                    <input className="input" value={c[field]}
                      onChange={(e) => { const u = [...contacts]; u[i] = { ...u[i], [field]: e.target.value }; setContacts(u); mark() }} />
                  </Field>
                ))}
                <Field label="Matrix ID">
                  <input className="input" value={c.matrix_id} placeholder="@name:homeserver.tld"
                    onChange={(e) => { const u = [...contacts]; u[i] = { ...u[i], matrix_id: e.target.value }; setContacts(u); mark() }} />
                </Field>
              </div>
            </div>
          ))}
          <button className="btn btn-ghost" style={{ fontSize: '0.8rem' }}
            onClick={() => { setContacts([...contacts, { name: '', relationship: '', phone: '', matrix_id: '', email: '' }]); mark() }}>
            <Plus size={13} /> Add Emergency Contact
          </button>
        </Section>

        {/* ── Social Media ── */}
        <Section title="Social Media Profiles" icon={Globe}>
          <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '1rem', marginTop: 0 }}>
            These are used by the OSINT scanner to verify your activity when the switch is triggered.
          </p>
          {social.map((s, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: '0.75rem' }}>
              <select className="input" style={{ width: 170, flexShrink: 0 }} value={s.platform}
                onChange={(e) => { const u = [...social]; u[i] = { ...u[i], platform: e.target.value }; setSocial(u); mark() }}>
                {SOCIAL_PLATFORMS.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
              <input className="input" placeholder="https://... or @handle" value={s.url}
                onChange={(e) => { const u = [...social]; u[i] = { ...u[i], url: e.target.value }; setSocial(u); mark() }} />
              <button className="btn btn-ghost" style={{ padding: '8px', color: 'var(--danger)', border: 'none', flexShrink: 0 }}
                onClick={() => { setSocial(social.filter((_, j) => j !== i)); mark() }}>
                <Trash2 size={13} />
              </button>
            </div>
          ))}
          <button className="btn btn-ghost" style={{ fontSize: '0.8rem' }}
            onClick={() => { setSocial([...social, { platform: SOCIAL_PLATFORMS[0], url: '' }]); mark() }}>
            <Plus size={13} /> Add Social Media Profile
          </button>
        </Section>

        {/* ── Vault ── */}
        <Section title="Vault — Final Message" icon={FileText}>
          <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '1rem', marginTop: 0 }}>
            This message will be released to your emergency contacts and group room when the switch triggers. Markdown is supported.
          </p>
          {profile.vault_created_ts && (
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
              Original vault created via bot: <strong style={{ color: 'var(--text)' }}>{tsToAbsolute(profile.vault_created_ts)}</strong>
              {profile.vault_released_ts && <span style={{ color: 'var(--danger)', marginLeft: 8 }}>Released: {tsToAbsolute(profile.vault_released_ts)}</span>}
            </div>
          )}
          <Field label="Final Message (Markdown)">
            <textarea className="input" rows={10} value={vaultText}
              placeholder="Write your final message here. This will be sent to your emergency contacts and the group room when the switch triggers."
              style={{ fontFamily: 'monospace', fontSize: '0.82rem' }}
              onChange={(e) => { setVaultText(e.target.value); mark() }} />
          </Field>
        </Section>

        {/* ── Trigger Configuration ── */}
        <Section title="Trigger Configuration" icon={Bell}>
          <Field label="Missing Threshold (how long before the switch triggers)">
            <select className="input" value={threshold}
              onChange={(e) => { setThreshold(parseInt(e.target.value)); mark() }}>
              {THRESHOLD_OPTIONS.map((o) => <option key={o.hours} value={o.hours}>{o.label}</option>)}
            </select>
          </Field>

          <div style={{ marginTop: '1.25rem' }}>
            <p style={{ fontSize: '0.7rem', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.5rem' }}>
              Release Actions — where to send your vault when the switch triggers
            </p>
            {releaseActions.map((a, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: '0.75rem' }}>
                <select className="input" style={{ width: 160, flexShrink: 0 }} value={a.type}
                  onChange={(e) => { const u = [...releaseActions]; u[i] = { ...u[i], type: e.target.value as ReleaseAction['type'] }; setReleaseActions(u); mark() }}>
                  <option value="matrix_dm">Matrix DM</option>
                  <option value="matrix_room">Matrix Room</option>
                  <option value="webhook">Webhook URL</option>
                </select>
                <input className="input" value={a.target}
                  placeholder={a.type === 'matrix_dm' ? '@contact:homeserver.tld' : a.type === 'matrix_room' ? '!roomid:homeserver.tld' : 'https://...'}
                  onChange={(e) => { const u = [...releaseActions]; u[i] = { ...u[i], target: e.target.value }; setReleaseActions(u); mark() }} />
                <button className="btn btn-ghost" style={{ padding: '8px', color: 'var(--danger)', border: 'none', flexShrink: 0 }}
                  onClick={() => { setReleaseActions(releaseActions.filter((_, j) => j !== i)); mark() }}>
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
            <button className="btn btn-ghost" style={{ fontSize: '0.8rem' }}
              onClick={() => { setReleaseActions([...releaseActions, { type: 'matrix_dm', target: '' }]); mark() }}>
              <Plus size={13} /> Add Release Action
            </button>
          </div>
        </Section>

        {/* ── FOIA Request Tracker ── */}
        <Section title="FOIA Request Tracker" icon={Scale} defaultOpen={false}>
          <FOIADashboard />
        </Section>

        {/* ── AI Memory Profile ── */}
        <Section title="AI Memory Profile" icon={Brain} defaultOpen={false}>
          <MemoryProfile />
        </Section>

        {/* ── Audit Log ── */}
        <Section title="Audit Log" icon={History} defaultOpen={false}>
          {!audit || audit.length === 0 ? (
            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>No activity recorded yet.</p>
          ) : (
            <div style={{ maxHeight: 300, overflowY: 'auto' }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>Event</th>
                    <th>Note</th>
                    <th>Time</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.map((e: any) => (
                    <tr key={e.id}>
                      <td>
                        <span className="badge" style={{ background: 'var(--primary-dim)', color: 'var(--primary)' }}>
                          {e.event_type}
                        </span>
                      </td>
                      <td style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>{e.note || '—'}</td>
                      <td style={{ color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{tsToAbsolute(e.event_ts)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>

      </div>
    </div>
  )
}
