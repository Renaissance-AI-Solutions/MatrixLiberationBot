/**
 * MemoryProfile.tsx
 * =================
 * AI Memory Profile section for the DMS Dashboard.
 *
 * Displays the long-term memories that Liberation Bot's Dream Engine has
 * consolidated about the current user. Allows the user to:
 *   - View all memories grouped by category
 *   - See the full version history of any memory
 *   - Edit a memory (user-initiated correction)
 *   - Soft-delete a memory (with confirmation)
 *   - Restore a deleted memory
 *   - View Dream Engine status (last run, next run, stats)
 */

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Brain, Clock, ChevronDown, ChevronUp, Edit3, Trash2, RotateCcw,
  History, CheckCircle2, AlertCircle, Loader2, Sparkles, RefreshCw,
  Eye, EyeOff, Save, X,
} from 'lucide-react'
import { memoryApi, MEMORY_CATEGORY_LABELS } from '../api'
import type { UserMemory, DreamCycle } from '../api'

// ── Helpers ──────────────────────────────────────────────────────────────────

function tsToAbsolute(ts: number | null | undefined): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString()
}

function tsToRelative(ts: number | null | undefined): string {
  if (!ts) return 'Never'
  const diff = Date.now() / 1000 - ts
  const h = Math.floor(diff / 3600)
  if (h < 1) return 'Just now'
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function categoryLabel(cat: string): string {
  return MEMORY_CATEGORY_LABELS[cat] || cat.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function categoryColor(cat: string): string {
  const colors: Record<string, string> = {
    symptoms: 'rgba(239,68,68,0.15)',
    legal_status: 'rgba(99,102,241,0.15)',
    history: 'rgba(34,197,94,0.12)',
    contacts: 'rgba(59,130,246,0.15)',
    preferences: 'rgba(245,158,11,0.15)',
    threat_profile: 'rgba(239,68,68,0.22)',
    notes: 'rgba(148,163,184,0.15)',
  }
  return colors[cat] || 'rgba(148,163,184,0.12)'
}

function categoryTextColor(cat: string): string {
  const colors: Record<string, string> = {
    symptoms: '#ef4444',
    legal_status: '#818cf8',
    history: '#22c55e',
    contacts: '#3b82f6',
    preferences: '#f59e0b',
    threat_profile: '#ef4444',
    notes: '#94a3b8',
  }
  return colors[cat] || '#94a3b8'
}

// ── Dream Status Banner ───────────────────────────────────────────────────────

function DreamStatusBanner() {
  const { data: status, isLoading } = useQuery({
    queryKey: ['dream-status'],
    queryFn: () => memoryApi.getDreamStatus().then((r) => r.data),
    refetchInterval: 60_000,
  })

  if (isLoading) return null

  const last = status?.last_cycle
  const nextRun = status?.next_scheduled_utc
    ? new Date(status.next_scheduled_utc).toLocaleString()
    : '—'

  return (
    <div style={{
      padding: '0.85rem 1.1rem',
      borderRadius: 10,
      background: 'linear-gradient(135deg, rgba(99,102,241,0.08), rgba(168,85,247,0.08))',
      border: '1px solid rgba(99,102,241,0.2)',
      marginBottom: '1.25rem',
      display: 'flex',
      flexWrap: 'wrap',
      gap: '0.75rem',
      alignItems: 'center',
      justifyContent: 'space-between',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Sparkles size={15} style={{ color: '#a855f7' }} />
        <span style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text)' }}>
          Dream Engine
        </span>
        <span className="badge" style={{
          background: last ? 'rgba(34,197,94,0.15)' : 'rgba(148,163,184,0.15)',
          color: last ? '#22c55e' : '#94a3b8',
          fontSize: '0.65rem',
        }}>
          {status?.engine_status === 'active' ? 'Active' : 'Never Run'}
        </span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1.25rem', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
        {last && (
          <>
            <span>
              <Clock size={11} style={{ display: 'inline', marginRight: 3 }} />
              Last run: <strong style={{ color: 'var(--text)' }}>{tsToRelative(last.started_ts)}</strong>
            </span>
            <span>
              Memories created: <strong style={{ color: 'var(--text)' }}>
                {(last.user_memories_created || 0) + (last.op_memories_created || 0)}
              </strong>
            </span>
            <span>
              Updated: <strong style={{ color: 'var(--text)' }}>
                {(last.user_memories_updated || 0) + (last.op_memories_updated || 0)}
              </strong>
            </span>
          </>
        )}
        <span>
          Next: <strong style={{ color: 'var(--text)' }}>{nextRun}</strong>
        </span>
      </div>
    </div>
  )
}

// ── Memory Version History Drawer ─────────────────────────────────────────────

function MemoryHistoryDrawer({ memoryId, onClose }: { memoryId: number; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ['memory-detail', memoryId],
    queryFn: () => memoryApi.get(memoryId).then((r) => r.data),
  })

  return (
    <div style={{
      marginTop: '0.75rem',
      padding: '0.85rem 1rem',
      borderRadius: 8,
      background: 'var(--bg)',
      border: '1px solid var(--border)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.6rem' }}>
        <span style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Version History
        </span>
        <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', padding: 2 }}>
          <X size={13} />
        </button>
      </div>
      {isLoading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '0.5rem' }}>
          <Loader2 size={16} className="animate-spin" style={{ color: 'var(--primary)' }} />
        </div>
      ) : !data?.history?.length ? (
        <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: 0 }}>
          No previous versions. This is the original version.
        </p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          {[...data.history].reverse().map((h) => (
            <div key={h.id} style={{
              padding: '0.5rem 0.7rem',
              borderRadius: 6,
              background: 'var(--surface)',
              border: '1px solid var(--border)',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={{ fontSize: '0.65rem', fontWeight: 600, color: 'var(--primary)' }}>v{h.version}</span>
                <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>
                  {tsToAbsolute(h.archived_ts)} · by {h.archived_by}
                </span>
              </div>
              <p style={{ margin: 0, fontSize: '0.78rem', color: 'var(--text)', lineHeight: 1.5 }}>{h.memory_text}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Memory Card ───────────────────────────────────────────────────────────────

function MemoryCard({ memory }: { memory: UserMemory }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [editText, setEditText] = useState(memory.memory_text)
  const [showHistory, setShowHistory] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [editError, setEditError] = useState('')

  const editMutation = useMutation({
    mutationFn: (text: string) => memoryApi.edit(memory.id, text),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['memories'] })
      qc.invalidateQueries({ queryKey: ['memory-detail', memory.id] })
      setEditing(false)
      setEditError('')
    },
    onError: (err: any) => {
      setEditError(err?.response?.data?.detail || 'Save failed.')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: () => memoryApi.delete(memory.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['memories'] })
      setConfirmDelete(false)
    },
  })

  const restoreMutation = useMutation({
    mutationFn: () => memoryApi.restore(memory.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['memories'] }),
  })

  const isDeleted = memory.is_deleted === 1

  const handleSave = () => {
    const trimmed = editText.trim()
    if (!trimmed) { setEditError('Memory text cannot be empty.'); return }
    if (trimmed.length > 500) { setEditError('Memory text cannot exceed 500 characters.'); return }
    setEditError('')
    editMutation.mutate(trimmed)
  }

  return (
    <div style={{
      padding: '0.9rem 1rem',
      borderRadius: 10,
      background: isDeleted ? 'var(--bg)' : 'var(--surface)',
      border: `1px solid ${isDeleted ? 'var(--border)' : 'rgba(99,102,241,0.15)'}`,
      opacity: isDeleted ? 0.6 : 1,
      transition: 'opacity 0.2s',
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8, marginBottom: editing ? '0.6rem' : 0 }}>
        <div style={{ flex: 1 }}>
          {!editing && (
            <p style={{ margin: 0, fontSize: '0.85rem', color: 'var(--text)', lineHeight: 1.6 }}>
              {memory.memory_text}
            </p>
          )}
          {editing && (
            <div>
              <textarea
                className="input"
                rows={4}
                value={editText}
                onChange={(e) => setEditText(e.target.value)}
                style={{ fontSize: '0.85rem', lineHeight: 1.6, resize: 'vertical' }}
                maxLength={500}
              />
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 4 }}>
                <span style={{ fontSize: '0.7rem', color: editText.length > 450 ? 'var(--danger)' : 'var(--text-muted)' }}>
                  {editText.length}/500
                </span>
                {editError && (
                  <span style={{ fontSize: '0.7rem', color: 'var(--danger)' }}>{editError}</span>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Action buttons */}
        {!isDeleted && (
          <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
            {!editing ? (
              <>
                <button
                  title="Edit memory"
                  onClick={() => { setEditing(true); setEditText(memory.memory_text) }}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', padding: '4px 5px', borderRadius: 5 }}
                >
                  <Edit3 size={13} />
                </button>
                <button
                  title="View version history"
                  onClick={() => setShowHistory(!showHistory)}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: showHistory ? 'var(--primary)' : 'var(--text-muted)', padding: '4px 5px', borderRadius: 5 }}
                >
                  <History size={13} />
                </button>
                <button
                  title="Delete memory"
                  onClick={() => setConfirmDelete(true)}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--danger)', padding: '4px 5px', borderRadius: 5 }}
                >
                  <Trash2 size={13} />
                </button>
              </>
            ) : (
              <>
                <button
                  title="Save changes"
                  onClick={handleSave}
                  disabled={editMutation.isPending}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--success)', padding: '4px 5px', borderRadius: 5 }}
                >
                  {editMutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                </button>
                <button
                  title="Cancel edit"
                  onClick={() => { setEditing(false); setEditText(memory.memory_text); setEditError('') }}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', padding: '4px 5px', borderRadius: 5 }}
                >
                  <X size={13} />
                </button>
              </>
            )}
          </div>
        )}

        {isDeleted && (
          <button
            title="Restore memory"
            onClick={() => restoreMutation.mutate()}
            disabled={restoreMutation.isPending}
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--primary)', padding: '4px 5px', borderRadius: 5, flexShrink: 0 }}
          >
            {restoreMutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <RotateCcw size={13} />}
          </button>
        )}
      </div>

      {/* Metadata row */}
      {!editing && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem', marginTop: '0.5rem', fontSize: '0.68rem', color: 'var(--text-muted)' }}>
          <span>v{memory.version}</span>
          <span>Updated {tsToRelative(memory.updated_ts)}</span>
          <span style={{ color: memory.last_edited_by === 'user' ? '#f59e0b' : 'var(--text-muted)' }}>
            {memory.last_edited_by === 'user' ? '✏ Edited by you' : '🤖 Dream Engine'}
          </span>
          {isDeleted && (
            <span style={{ color: 'var(--danger)' }}>Deleted · not shown to AI</span>
          )}
        </div>
      )}

      {/* Delete confirmation */}
      {confirmDelete && (
        <div style={{
          marginTop: '0.75rem',
          padding: '0.6rem 0.85rem',
          borderRadius: 8,
          background: 'var(--danger-dim)',
          border: '1px solid rgba(239,68,68,0.3)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
        }}>
          <span style={{ fontSize: '0.78rem', color: 'var(--danger)' }}>
            Remove this memory from the AI's context?
          </span>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="btn btn-ghost" style={{ fontSize: '0.75rem', padding: '0.25rem 0.6rem' }}
              onClick={() => setConfirmDelete(false)}>Cancel</button>
            <button
              style={{ fontSize: '0.75rem', padding: '0.25rem 0.6rem', background: 'var(--danger)', color: 'white', border: 'none', borderRadius: 6, cursor: 'pointer' }}
              onClick={() => deleteMutation.mutate()}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : 'Delete'}
            </button>
          </div>
        </div>
      )}

      {/* Version history drawer */}
      {showHistory && !editing && (
        <MemoryHistoryDrawer memoryId={memory.id} onClose={() => setShowHistory(false)} />
      )}
    </div>
  )
}

// ── Category Group ────────────────────────────────────────────────────────────

function CategoryGroup({ category, memories }: { category: string; memories: UserMemory[] }) {
  const [open, setOpen] = useState(true)
  const activeCount = memories.filter((m) => m.is_deleted === 0).length
  const deletedCount = memories.filter((m) => m.is_deleted === 1).length

  return (
    <div style={{ marginBottom: '1rem' }}>
      <button
        onClick={() => setOpen(!open)}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          padding: '0.4rem 0',
          marginBottom: open ? '0.5rem' : 0,
        }}
      >
        <span style={{
          display: 'inline-block',
          padding: '0.2rem 0.65rem',
          borderRadius: 20,
          background: categoryColor(category),
          color: categoryTextColor(category),
          fontSize: '0.72rem',
          fontWeight: 700,
          letterSpacing: '0.04em',
        }}>
          {categoryLabel(category)}
        </span>
        <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
          {activeCount} active{deletedCount > 0 ? `, ${deletedCount} deleted` : ''}
        </span>
        <span style={{ marginLeft: 'auto', color: 'var(--text-muted)' }}>
          {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        </span>
      </button>

      {open && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          {memories.map((m) => (
            <MemoryCard key={m.id} memory={m} />
          ))}
        </div>
      )}
    </div>
  )
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function MemoryProfile() {
  const qc = useQueryClient()
  const [showDeleted, setShowDeleted] = useState(false)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['memories', showDeleted],
    queryFn: () => memoryApi.list(showDeleted).then((r) => r.data),
    refetchInterval: 120_000,
  })

  const categories = data ? Object.keys(data.by_category) : []
  const totalActive = data?.memories.filter((m) => m.is_deleted === 0).length ?? 0
  const totalDeleted = data?.memories.filter((m) => m.is_deleted === 1).length ?? 0

  return (
    <div>
      <DreamStatusBanner />

      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1rem', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <p style={{ margin: 0, fontSize: '0.82rem', color: 'var(--text-muted)' }}>
            {isLoading ? 'Loading memories…' : (
              <>
                <strong style={{ color: 'var(--text)' }}>{totalActive}</strong> active memor{totalActive === 1 ? 'y' : 'ies'}
                {totalDeleted > 0 && <>, <strong style={{ color: 'var(--text-muted)' }}>{totalDeleted}</strong> deleted</>}
              </>
            )}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className="btn btn-ghost"
            style={{ fontSize: '0.78rem', padding: '0.3rem 0.7rem' }}
            onClick={() => setShowDeleted(!showDeleted)}
          >
            {showDeleted ? <><EyeOff size={12} /> Hide Deleted</> : <><Eye size={12} /> Show Deleted</>}
          </button>
          <button
            className="btn btn-ghost"
            style={{ fontSize: '0.78rem', padding: '0.3rem 0.7rem' }}
            onClick={() => refetch()}
          >
            <RefreshCw size={12} /> Refresh
          </button>
        </div>
      </div>

      {/* Content */}
      {isLoading && (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '2rem' }}>
          <Loader2 size={24} className="animate-spin" style={{ color: 'var(--primary)' }} />
        </div>
      )}

      {isError && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '0.75rem 1rem', borderRadius: 8, background: 'var(--danger-dim)', color: 'var(--danger)', fontSize: '0.85rem' }}>
          <AlertCircle size={15} />
          Failed to load memories. Please refresh the page.
        </div>
      )}

      {!isLoading && !isError && categories.length === 0 && (
        <div style={{
          textAlign: 'center',
          padding: '2.5rem 1rem',
          borderRadius: 12,
          background: 'var(--surface)',
          border: '1px dashed var(--border)',
        }}>
          <Brain size={32} style={{ color: 'var(--text-muted)', marginBottom: '0.75rem' }} />
          <p style={{ color: 'var(--text)', fontWeight: 600, marginBottom: 4 }}>No memories yet</p>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.82rem', margin: 0 }}>
            The Dream Engine runs nightly at 3:00 AM UTC and will consolidate memories from your conversations.
            After your first few interactions with Liberation Bot, memories will begin to appear here.
          </p>
        </div>
      )}

      {!isLoading && !isError && categories.length > 0 && (
        <div>
          {categories.map((cat) => (
            <CategoryGroup
              key={cat}
              category={cat}
              memories={data!.by_category[cat]}
            />
          ))}
        </div>
      )}

      {/* Explanation footer */}
      {!isLoading && !isError && (
        <div style={{
          marginTop: '1.5rem',
          padding: '0.85rem 1rem',
          borderRadius: 8,
          background: 'var(--bg)',
          border: '1px solid var(--border)',
          fontSize: '0.75rem',
          color: 'var(--text-muted)',
          lineHeight: 1.6,
        }}>
          <strong style={{ color: 'var(--text)' }}>About AI Memory</strong> — Liberation Bot's Dream Engine
          reviews your conversations each night and consolidates important information into these long-term memory
          stores. This allows the bot to remember your situation, symptoms, and history across sessions without
          you having to repeat yourself. You can edit any memory to correct inaccuracies, or delete memories you
          don't want the AI to retain. Deleted memories are preserved in version history but are no longer shown
          to the AI.
        </div>
      )}
    </div>
  )
}
