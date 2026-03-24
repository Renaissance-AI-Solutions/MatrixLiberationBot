import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Shield, ArrowRight, KeyRound, Loader2, AlertCircle, CheckCircle2 } from 'lucide-react'
import { authApi, storeSession } from '../api'

type Step = 'matrix_id' | 'otp'

export default function Login() {
  const navigate = useNavigate()
  const [step, setStep] = useState<Step>('matrix_id')
  const [matrixId, setMatrixId] = useState('')
  const [otp, setOtp] = useState(['', '', '', '', '', ''])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const otpRefs = useRef<(HTMLInputElement | null)[]>([])

  useEffect(() => {
    if (step === 'otp') setTimeout(() => otpRefs.current[0]?.focus(), 80)
  }, [step])

  const handleRequestOtp = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    const id = matrixId.trim().toLowerCase()
    if (!id.startsWith('@') || !id.includes(':')) {
      setError('Please enter a valid Matrix ID, e.g. @alice:matrix.org')
      return
    }
    setLoading(true)
    try {
      await authApi.requestOtp(id)
      setMatrixId(id)
      setStep('otp')
    } catch (err: any) {
      const msg = err?.response?.data?.detail
      setError(msg || 'Could not send OTP. Make sure Liberation Bot is running.')
    } finally {
      setLoading(false)
    }
  }

  const handleOtpChange = (i: number, val: string) => {
    if (!/^\d*$/.test(val)) return
    const next = [...otp]
    next[i] = val.slice(-1)
    setOtp(next)
    if (val && i < 5) otpRefs.current[i + 1]?.focus()
  }

  const handleOtpKey = (i: number, e: React.KeyboardEvent) => {
    if (e.key === 'Backspace' && !otp[i] && i > 0) otpRefs.current[i - 1]?.focus()
  }

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    const code = otp.join('')
    if (code.length !== 6) { setError('Please enter the full 6-digit code.'); return }
    setLoading(true)
    try {
      const { data } = await authApi.verifyOtp(matrixId, code)
      storeSession(data.access_token, data.matrix_id)
      setSuccess(true)
      setTimeout(() => navigate('/'), 500)
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Incorrect or expired code.')
      setOtp(['', '', '', '', '', ''])
      otpRefs.current[0]?.focus()
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        background: 'var(--bg)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '1.5rem',
      }}
    >
      <div style={{ width: '100%', maxWidth: 420 }} className="animate-in">
        {/* Header */}
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <div
            style={{
              width: 64, height: 64, borderRadius: 18,
              background: 'linear-gradient(135deg, #ef4444, #a855f7)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              margin: '0 auto 1rem',
            }}
          >
            <Shield size={30} color="white" />
          </div>
          <h1 style={{ margin: 0, fontSize: '1.5rem', fontWeight: 700, color: 'var(--text)' }}>
            Dead Man's Switch
          </h1>
          <p style={{ margin: '0.4rem 0 0', color: 'var(--text-muted)', fontSize: '0.875rem' }}>
            {step === 'matrix_id'
              ? 'Enter your Matrix ID to receive a login code'
              : `Check your Matrix client for a message from Liberation Bot`}
          </p>
        </div>

        {/* Card */}
        <div className="card" style={{ padding: '2rem' }}>
          {step === 'matrix_id' ? (
            <form onSubmit={handleRequestOtp}>
              <label style={{ display: 'block', fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '0.5rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Matrix ID
              </label>
              <input
                className="input"
                style={{ marginBottom: '1rem' }}
                type="text"
                placeholder="@alice:matrix.org"
                value={matrixId}
                onChange={(e) => setMatrixId(e.target.value)}
                autoFocus
                disabled={loading}
              />
              {error && <ErrorBanner msg={error} />}
              <button
                type="submit"
                className="btn btn-primary"
                style={{ width: '100%', justifyContent: 'center', background: 'linear-gradient(135deg, #ef4444, #a855f7)' }}
                disabled={loading}
              >
                {loading ? <Loader2 size={16} className="animate-spin" /> : <><span>Send Code</span><ArrowRight size={15} /></>}
              </button>
            </form>
          ) : (
            <form onSubmit={handleVerify}>
              <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '1.25rem' }}>
                Code sent to <strong style={{ color: 'var(--text)' }}>{matrixId}</strong>
              </p>

              {/* 6-digit OTP boxes */}
              <div style={{ display: 'flex', gap: 10, justifyContent: 'center', marginBottom: '1.5rem' }}>
                {otp.map((digit, i) => (
                  <input
                    key={i}
                    ref={(el) => { otpRefs.current[i] = el }}
                    type="text"
                    inputMode="numeric"
                    maxLength={1}
                    value={digit}
                    onChange={(e) => handleOtpChange(i, e.target.value)}
                    onKeyDown={(e) => handleOtpKey(i, e)}
                    disabled={loading || success}
                    style={{
                      width: 50, height: 58,
                      textAlign: 'center',
                      fontSize: '1.4rem',
                      fontWeight: 700,
                      background: 'var(--bg)',
                      border: `2px solid ${digit ? 'var(--primary)' : 'var(--border)'}`,
                      borderRadius: 10,
                      color: 'var(--text)',
                      outline: 'none',
                      transition: 'border-color 0.15s',
                    }}
                  />
                ))}
              </div>

              {error && <ErrorBanner msg={error} />}
              {success && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '0.6rem 0.85rem', borderRadius: 8, background: 'var(--success-dim)', color: 'var(--success)', marginBottom: '1rem', fontSize: '0.85rem' }}>
                  <CheckCircle2 size={15} /> Verified — redirecting…
                </div>
              )}

              <button
                type="submit"
                className="btn btn-primary"
                style={{ width: '100%', justifyContent: 'center', marginBottom: '0.6rem', background: 'linear-gradient(135deg, #ef4444, #a855f7)' }}
                disabled={loading || success}
              >
                {loading ? <Loader2 size={16} className="animate-spin" /> : <><KeyRound size={15} /><span>Verify Code</span></>}
              </button>

              <button
                type="button"
                className="btn btn-ghost"
                style={{ width: '100%', justifyContent: 'center', fontSize: '0.8rem' }}
                onClick={() => { setStep('matrix_id'); setError(null); setOtp(['', '', '', '', '', '']) }}
                disabled={loading}
              >
                Use a different Matrix ID
              </button>
            </form>
          )}
        </div>

        <p style={{ textAlign: 'center', marginTop: '1rem', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
          Session expires automatically after 8 hours.
        </p>
      </div>
    </div>
  )
}

function ErrorBanner({ msg }: { msg: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '0.6rem 0.85rem', borderRadius: 8, background: 'var(--danger-dim)', color: 'var(--danger)', marginBottom: '1rem', fontSize: '0.85rem' }}>
      <AlertCircle size={15} style={{ flexShrink: 0 }} />
      {msg}
    </div>
  )
}
