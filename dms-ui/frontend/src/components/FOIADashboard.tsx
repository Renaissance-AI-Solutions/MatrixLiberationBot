/**
 * FOIADashboard.tsx
 * =================
 * Liberation Bot — FOIA Request Tracker
 *
 * Displays all FOIA requests for the authenticated user with:
 *   - Status badges (DRAFT, FINALIZED, SUBMITTED, RESPONDED, APPEALED, CLOSED)
 *   - Deadline countdown with overdue highlighting
 *   - Inline status update controls
 *   - Copy-to-clipboard and plain-text download for draft/appeal letters
 *   - Jurisdiction and agency metadata
 *
 * Integrated into Dashboard.tsx as a collapsible Section component.
 */

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  FileText, Download, Copy, CheckCircle2, Clock, AlertTriangle,
  ChevronDown, ChevronUp, Loader2, RefreshCw, ExternalLink,
} from 'lucide-react'
import { foiaApi } from '../api'
import type { FoiaRequest } from '../api'

// ── Helpers ───────────────────────────────────────────────────────────────────

function tsToDate(ts: number | null): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleDateString('en-US', {
    year: 'numeric', month: 'short', day: 'numeric',
  })
}

function daysUntil(ts: number | null): { label: string; overdue: boolean; urgent: boolean } {
  if (!ts) return { label: '—', overdue: false, urgent: false }
  const now = Date.now() / 1000
  const days = (ts - now) / 86400
  if (days < 0) return { label: `${Math.abs(Math.floor(days))}d overdue`, overdue: true, urgent: false }
  if (days < 3) return { label: `${days.toFixed(1)}d left`, overdue: false, urgent: true }
  return { label: `${Math.floor(days)}d left`, overdue: false, urgent: false }
}

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  DRAFT:      { bg: 'var(--surface)',      color: 'var(--text-muted)' },
  FINALIZED:  { bg: 'var(--primary-dim)',  color: 'var(--primary)' },
  SUBMITTED:  { bg: 'rgba(234,179,8,0.12)', color: '#ca8a04' },
  RESPONDED:  { bg: 'rgba(34,197,94,0.12)', color: 'var(--success)' },
  APPEALED:   { bg: 'rgba(249,115,22,0.12)', color: '#ea580c' },
  CLOSED:     { bg: 'var(--surface)',      color: 'var(--text-muted)' },
}

const VALID_NEXT_STATUSES: Record<string, string[]> = {
  SUBMITTED: ['RESPONDED', 'APPEALED', 'CLOSED'],
  RESPONDED: ['APPEALED', 'CLOSED'],
  APPEALED:  ['RESPONDED', 'CLOSED'],
  FINALIZED: ['SUBMITTED'],
}

// ── Subcomponents ─────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const s = STATUS_COLORS[status] ?? STATUS_COLORS.DRAFT
  return (
    <span style={{
      display: 'inline-block', padding: '2px 8px', borderRadius: 6,
      fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.04em',
      background: s.bg, color: s.color, textTransform: 'uppercase',
    }}>
      {status}
    </span>
  )
}

function DeadlinePill({ ts }: { ts: number | null }) {
  if (!ts) return <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>No deadline tracked</span>
  const { label, overdue, urgent } = daysUntil(ts)
  const color = overdue ? 'var(--danger)' : urgent ? '#ca8a04' : 'var(--success)'
  const Icon = overdue ? AlertTriangle : urgent ? Clock : CheckCircle2
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: '0.75rem', color, fontWeight: 600 }}>
      <Icon size={12} />
      {label} &middot; Due {tsToDate(ts)}
    </span>
  )
}

function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = async () => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }
  return (
    <button className="btn btn-ghost" style={{ fontSize: '0.75rem', padding: '4px 10px' }} onClick={handleCopy}>
      {copied ? <CheckCircle2 size={12} style={{ color: 'var(--success)' }} /> : <Copy size={12} />}
      {copied ? 'Copied!' : label}
    </button>
  )
}

function DownloadButton({ text, filename }: { text: string; filename: string }) {
  const handleDownload = () => {
    const blob = new Blob([text], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  }
  return (
    <button className="btn btn-ghost" style={{ fontSize: '0.75rem', padding: '4px 10px' }} onClick={handleDownload}>
      <Download size={12} /> Download
    </button>
  )
}

// ── FOIA Request Card ─────────────────────────────────────────────────────────

function FOIACard({ req }: { req: FoiaRequest }) {
  const qc = useQueryClient()
  const [expanded, setExpanded] = useState(false)
  const [selectedStatus, setSelectedStatus] = useState('')
  const [letterData, setLetterData] = useState<{ draft_letter: string; appeal_letter: string | null } | null>(null)
  const [letterLoading, setLetterLoading] = useState(false)

  const nextStatuses = VALID_NEXT_STATUSES[req.status] ?? []

  const statusMutation = useMutation({
    mutationFn: (status: string) => foiaApi.updateStatus(req.id, status),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['foia'] })
      setSelectedStatus('')
    },
  })

  const loadLetter = async () => {
    if (letterData) return
    setLetterLoading(true)
    try {
      const res = await foiaApi.getLetter(req.id)
      setLetterData({ draft_letter: res.data.draft_letter, appeal_letter: res.data.appeal_letter })
    } finally {
      setLetterLoading(false)
    }
  }

  const handleExpand = () => {
    const next = !expanded
    setExpanded(next)
    if (next) loadLetter()
  }

  const agencySlug = req.target_agency.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '')

  return (
    <div style={{
      border: '1px solid var(--border)', borderRadius: 10,
      marginBottom: '0.75rem', overflow: 'hidden',
      background: 'var(--surface)',
    }}>
      {/* Header row */}
      <button
        onClick={handleExpand}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '0.9rem 1.1rem', background: 'transparent', border: 'none', cursor: 'pointer',
          color: 'var(--text)', textAlign: 'left',
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
            <span style={{ fontWeight: 600, fontSize: '0.875rem' }}>#{req.id}</span>
            <StatusBadge status={req.status} />
            {req.appeal_status === 'FILED' && (
              <span style={{ fontSize: '0.7rem', fontWeight: 700, color: '#ea580c', background: 'rgba(249,115,22,0.12)', padding: '2px 7px', borderRadius: 5 }}>
                APPEAL FILED
              </span>
            )}
            <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
              <code style={{ background: 'var(--bg)', padding: '1px 5px', borderRadius: 4, fontSize: '0.7rem' }}>
                {req.jurisdiction_code}
              </code>
            </span>
          </div>
          <div style={{ fontSize: '0.8rem', color: 'var(--text)', fontWeight: 500, marginBottom: 3 }}>
            {req.target_agency}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            {req.subject_summary?.slice(0, 100)}{req.subject_summary?.length > 100 ? '…' : ''}
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4, marginLeft: 12, flexShrink: 0 }}>
          <DeadlinePill ts={req.expected_response_date} />
          <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
            Created {tsToDate(req.created_ts)}
          </span>
          {expanded
            ? <ChevronUp size={14} style={{ color: 'var(--text-muted)', marginTop: 4 }} />
            : <ChevronDown size={14} style={{ color: 'var(--text-muted)', marginTop: 4 }} />}
        </div>
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div style={{ borderTop: '1px solid var(--border)', padding: '1rem 1.1rem' }}>

          {/* Metadata grid */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: '0.75rem', marginBottom: '1rem' }}>
            {[
              { label: 'Jurisdiction', value: req.jurisdiction_code },
              { label: 'Submitted', value: tsToDate(req.submitted_ts) },
              { label: 'Response Deadline', value: tsToDate(req.expected_response_date) },
              { label: 'Appeal Status', value: req.appeal_status ?? 'None' },
            ].map(({ label, value }) => (
              <div key={label}>
                <div style={{ fontSize: '0.65rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 2 }}>
                  {label}
                </div>
                <div style={{ fontSize: '0.8rem', color: 'var(--text)', fontWeight: 500 }}>{value}</div>
              </div>
            ))}
          </div>

          {/* Status update */}
          {nextStatuses.length > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: '1rem', flexWrap: 'wrap' }}>
              <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 600 }}>Update status:</span>
              <select
                className="input"
                style={{ width: 'auto', fontSize: '0.8rem', padding: '4px 8px' }}
                value={selectedStatus}
                onChange={(e) => setSelectedStatus(e.target.value)}
              >
                <option value="">— Select —</option>
                {nextStatuses.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
              <button
                className="btn btn-primary"
                style={{ fontSize: '0.75rem', padding: '4px 12px' }}
                disabled={!selectedStatus || statusMutation.isPending}
                onClick={() => selectedStatus && statusMutation.mutate(selectedStatus)}
              >
                {statusMutation.isPending
                  ? <Loader2 size={12} className="animate-spin" />
                  : <><RefreshCw size={12} /> Apply</>}
              </button>
            </div>
          )}

          {/* Letter section */}
          {letterLoading && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--text-muted)', fontSize: '0.8rem', marginBottom: '0.75rem' }}>
              <Loader2 size={13} className="animate-spin" /> Loading letter...
            </div>
          )}

          {letterData && letterData.draft_letter && (
            <div style={{ marginBottom: '0.75rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.4rem' }}>
                <span style={{ fontSize: '0.7rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  <FileText size={11} style={{ display: 'inline', marginRight: 4 }} />
                  FOIA Request Letter
                </span>
                <div style={{ display: 'flex', gap: 6 }}>
                  <CopyButton text={letterData.draft_letter} label="Copy Letter" />
                  <DownloadButton
                    text={letterData.draft_letter}
                    filename={`foia-request-${req.id}-${agencySlug}.txt`}
                  />
                </div>
              </div>
              <pre style={{
                background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 8,
                padding: '0.75rem', fontSize: '0.72rem', lineHeight: 1.6,
                maxHeight: 280, overflowY: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                color: 'var(--text)', fontFamily: 'ui-monospace, monospace',
              }}>
                {letterData.draft_letter}
              </pre>
            </div>
          )}

          {letterData && letterData.appeal_letter && (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.4rem' }}>
                <span style={{ fontSize: '0.7rem', fontWeight: 700, color: '#ea580c', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  <FileText size={11} style={{ display: 'inline', marginRight: 4 }} />
                  Appeal Letter
                </span>
                <div style={{ display: 'flex', gap: 6 }}>
                  <CopyButton text={letterData.appeal_letter} label="Copy Appeal" />
                  <DownloadButton
                    text={letterData.appeal_letter}
                    filename={`foia-appeal-${req.id}-${agencySlug}.txt`}
                  />
                </div>
              </div>
              <pre style={{
                background: 'rgba(249,115,22,0.04)', border: '1px solid rgba(249,115,22,0.2)', borderRadius: 8,
                padding: '0.75rem', fontSize: '0.72rem', lineHeight: 1.6,
                maxHeight: 280, overflowY: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                color: 'var(--text)', fontFamily: 'ui-monospace, monospace',
              }}>
                {letterData.appeal_letter}
              </pre>
            </div>
          )}

          {/* Bot command hint */}
          <div style={{ marginTop: '0.75rem', padding: '0.6rem 0.8rem', background: 'var(--primary-dim)', borderRadius: 8 }}>
            <p style={{ fontSize: '0.72rem', color: 'var(--primary)', margin: 0 }}>
              <strong>Bot commands:</strong>{' '}
              {req.status === 'FINALIZED' && <><code>!foia_submit {req.id}</code> to start deadline tracking · </>}
              {['SUBMITTED', 'RESPONDED'].includes(req.status) && <><code>!foia_appeal {req.id}</code> to draft an appeal · </>}
              <code>!foia_status {req.id} &lt;STATUS&gt;</code> to update via Matrix DM
              <a
                href="https://element.io"
                target="_blank"
                rel="noopener noreferrer"
                style={{ marginLeft: 8, color: 'var(--primary)', textDecoration: 'none' }}
              >
                <ExternalLink size={10} style={{ display: 'inline', marginRight: 2 }} />Open Matrix
              </a>
            </p>
          </div>

        </div>
      )}
    </div>
  )
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function FOIADashboard() {
  const qc = useQueryClient()
  const [filterStatus, setFilterStatus] = useState<string>('ALL')

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['foia'],
    queryFn: () => foiaApi.list().then((r) => r.data),
    staleTime: 60_000,
  })

  const requests: FoiaRequest[] = data?.requests ?? []

  const filtered = filterStatus === 'ALL'
    ? requests
    : requests.filter((r) => r.status === filterStatus)

  const overdue = requests.filter(
    (r) => r.expected_response_date && r.status === 'SUBMITTED' && r.expected_response_date < Date.now() / 1000
  )

  if (isLoading) return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-muted)', padding: '1rem 0' }}>
      <Loader2 size={16} className="animate-spin" />
      <span style={{ fontSize: '0.85rem' }}>Loading FOIA requests...</span>
    </div>
  )

  if (isError) return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--danger)', padding: '1rem 0' }}>
      <AlertTriangle size={16} />
      <span style={{ fontSize: '0.85rem' }}>Failed to load FOIA requests.</span>
      <button className="btn btn-ghost" style={{ fontSize: '0.75rem' }} onClick={() => refetch()}>Retry</button>
    </div>
  )

  if (requests.length === 0) return (
    <div style={{ textAlign: 'center', padding: '2rem 1rem' }}>
      <FileText size={32} style={{ color: 'var(--text-muted)', marginBottom: 12 }} />
      <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem', marginBottom: 8 }}>
        No FOIA requests yet.
      </p>
      <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>
        Use <code>!foia_start</code> in a Matrix DM with Liberation Bot to draft your first request.
      </p>
    </div>
  )

  return (
    <div>
      {/* Summary bar */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem', marginBottom: '1rem', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          {['ALL', 'SUBMITTED', 'RESPONDED', 'APPEALED', 'FINALIZED', 'CLOSED'].map((s) => {
            const count = s === 'ALL' ? requests.length : requests.filter((r) => r.status === s).length
            if (s !== 'ALL' && count === 0) return null
            return (
              <button
                key={s}
                className={`btn ${filterStatus === s ? 'btn-primary' : 'btn-ghost'}`}
                style={{ fontSize: '0.72rem', padding: '3px 10px' }}
                onClick={() => setFilterStatus(s)}
              >
                {s} {count > 0 && <span style={{ marginLeft: 4, opacity: 0.7 }}>({count})</span>}
              </button>
            )
          })}
        </div>
        <button className="btn btn-ghost" style={{ fontSize: '0.75rem', padding: '4px 10px' }}
          onClick={() => qc.invalidateQueries({ queryKey: ['foia'] })}>
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {/* Overdue alert */}
      {overdue.length > 0 && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8, padding: '0.6rem 0.9rem',
          background: 'var(--danger-dim)', border: '1px solid rgba(239,68,68,0.3)',
          borderRadius: 8, marginBottom: '0.75rem',
        }}>
          <AlertTriangle size={14} style={{ color: 'var(--danger)', flexShrink: 0 }} />
          <span style={{ fontSize: '0.8rem', color: 'var(--danger)', fontWeight: 600 }}>
            {overdue.length} request{overdue.length > 1 ? 's are' : ' is'} overdue.
            Consider using <code>!foia_appeal &lt;id&gt;</code> to draft a constructive denial appeal.
          </span>
        </div>
      )}

      {/* Request cards */}
      {filtered.length === 0 ? (
        <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>No requests match this filter.</p>
      ) : (
        filtered.map((req) => <FOIACard key={req.id} req={req} />)
      )}
    </div>
  )
}
