import { useState, useEffect } from 'react';
import { Form, Input, Button, Alert } from 'antd';
import { useAuthStore } from '../../stores/authStore';
import { loginApi } from '../../lib/api';
import { useNavigate } from 'react-router-dom';

export default function LoginPage() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [mounted, setMounted] = useState(false);
  const login = useAuthStore((s) => s.login);
  const navigate = useNavigate();

  useEffect(() => {
    const t = setTimeout(() => setMounted(true), 60);
    return () => clearTimeout(t);
  }, []);

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true);
    setError('');
    try {
      const data = await loginApi(values.username, values.password);
      login(data.token, data.refresh_token, data.user);
      navigate('/analytics');
    } catch (e: any) {
      setError(e.response?.data?.detail || 'Invalid credentials');
    }
    setLoading(false);
  };

  return (
    <div style={{
      minHeight: '100vh',
      background: '#060a0f',
      display: 'flex',
      position: 'relative',
      overflow: 'hidden',
    }}>
      {/* Background glow */}
      <div style={{
        position: 'absolute', top: '-10%', left: '50%',
        transform: 'translateX(-50%)',
        width: 900, height: 600,
        background: 'radial-gradient(ellipse at center, rgba(99,102,241,.18) 0%, rgba(56,189,248,.10) 40%, transparent 70%)',
        pointerEvents: 'none',
      }} />
      {/* Dot grid */}
      <div style={{
        position: 'absolute', inset: 0,
        backgroundImage: 'radial-gradient(circle, rgba(255,255,255,.04) 1px, transparent 1px)',
        backgroundSize: '32px 32px',
        WebkitMaskImage: 'radial-gradient(ellipse 80% 60% at 50% 40%, black 0%, transparent 80%)',
        maskImage: 'radial-gradient(ellipse 80% 60% at 50% 40%, black 0%, transparent 80%)',
        pointerEvents: 'none',
      }} />

      {/* Left panel — branding */}
      <div style={{
        flex: 1, display: 'flex', flexDirection: 'column',
        justifyContent: 'center', padding: '48px 64px',
        position: 'relative', zIndex: 1,
      }} className="login-left-panel">
        {/* Logo */}
        <a href="/landing" style={{ textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: 10, marginBottom: 64 }}>
          <div style={{
            width: 34, height: 34, borderRadius: 9,
            background: 'linear-gradient(135deg, #0ea5e9, #6366f1)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 14, fontWeight: 900, color: '#fff',
            fontFamily: '"JetBrains Mono", monospace',
          }}>A</div>
          <span style={{ fontSize: 16, fontWeight: 700, color: '#e2eaf3', fontFamily: 'Inter, system-ui, sans-serif' }}>
            Atlas <span style={{ color: '#5a6a7e', fontWeight: 400 }}>by Alpheric</span>
          </span>
        </a>

        {/* Headline */}
        <div style={{
          opacity: mounted ? 1 : 0,
          transform: mounted ? 'translateY(0)' : 'translateY(20px)',
          transition: 'opacity .5s ease, transform .5s ease',
        }}>
          <div style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            background: '#111820', border: '1px solid #2a3545',
            borderRadius: 999, padding: '4px 12px',
            fontSize: 11, fontWeight: 600, color: '#8b9ab0',
            letterSpacing: 1, textTransform: 'uppercase',
            fontFamily: 'Inter, system-ui, sans-serif',
            marginBottom: 20,
          }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#38bdf8', boxShadow: '0 0 6px #38bdf8', display: 'inline-block' }} />
            Enterprise AI Platform
          </div>

          <h1 style={{
            fontSize: 'clamp(2rem, 4vw, 3.25rem)',
            fontWeight: 900, lineHeight: 1.08, letterSpacing: -2,
            margin: '0 0 18px',
            fontFamily: 'Inter, system-ui, sans-serif',
            color: '#e2eaf3',
          }}>
            Your AI team,<br />
            <span style={{
              background: 'linear-gradient(135deg, #fff 0%, #38bdf8 50%, #6366f1 100%)',
              WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
              backgroundClip: 'text',
            }}>always ready.</span>
          </h1>

          <p style={{
            fontSize: 16, color: '#8b9ab0', lineHeight: 1.7,
            maxWidth: 400, margin: '0 0 48px',
            fontFamily: 'Inter, system-ui, sans-serif',
          }}>
            Seven specialized models routing, learning, and distilling every request — so your team ships faster every day.
          </p>

          {/* Model badges */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {[
              { label: 'atlas-code', color: '#38bdf8' },
              { label: 'atlas-plan', color: '#c084fc' },
              { label: 'atlas-secure', color: '#f87171' },
              { label: 'atlas-infra', color: '#fb923c' },
              { label: 'atlas-data', color: '#fbbf24' },
              { label: 'atlas-books', color: '#4ade80' },
              { label: 'atlas-audit', color: '#2dd4bf' },
            ].map(m => (
              <span key={m.label} style={{
                fontSize: 11, fontWeight: 700,
                fontFamily: '"JetBrains Mono", monospace',
                color: m.color,
                background: '#0b1018',
                border: `1px solid ${m.color}33`,
                borderRadius: 6, padding: '3px 10px',
                letterSpacing: .3,
              }}>{m.label}</span>
            ))}
          </div>
        </div>
      </div>

      {/* Right panel — login form */}
      <div style={{
        width: 480, flexShrink: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: 40,
        position: 'relative', zIndex: 1,
      }} className="login-right-panel">
        <div style={{
          width: '100%', maxWidth: 400,
          background: 'rgba(11,16,24,.85)',
          border: '1px solid #1e2733',
          borderRadius: 20,
          padding: '40px 36px',
          backdropFilter: 'blur(24px)',
          boxShadow: '0 32px 80px rgba(0,0,0,.5), 0 0 0 1px rgba(255,255,255,.04)',
          opacity: mounted ? 1 : 0,
          transform: mounted ? 'translateY(0)' : 'translateY(16px)',
          transition: 'opacity .5s ease .1s, transform .5s ease .1s',
        }}>
          {/* Form header */}
          <div style={{ marginBottom: 32 }}>
            <div style={{
              width: 44, height: 44, borderRadius: 12,
              background: 'linear-gradient(135deg, #0ea5e9, #6366f1)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 18, fontWeight: 900, color: '#fff',
              fontFamily: '"JetBrains Mono", monospace',
              marginBottom: 20,
            }}>A</div>
            <h2 style={{
              fontSize: 22, fontWeight: 800, letterSpacing: -.5,
              color: '#e2eaf3', margin: '0 0 6px',
              fontFamily: 'Inter, system-ui, sans-serif',
            }}>Sign in to Atlas</h2>
            <p style={{
              fontSize: 14, color: '#5a6a7e', margin: 0,
              fontFamily: 'Inter, system-ui, sans-serif',
            }}>Enter your credentials to access your dashboard</p>
          </div>

          {error && (
            <Alert
              message={error}
              type="error"
              showIcon
              style={{
                marginBottom: 20,
                background: 'rgba(248,113,113,.08)',
                border: '1px solid rgba(248,113,113,.25)',
                borderRadius: 10,
                color: '#f87171',
              }}
            />
          )}

          <Form onFinish={onFinish} layout="vertical" requiredMark={false}>
            <Form.Item
              name="username"
              style={{ marginBottom: 14 }}
              rules={[{ required: true, message: 'Username is required' }]}
            >
              <Input
                placeholder="Username"
                size="large"
                autoComplete="username"
                style={{
                  background: '#0b1018',
                  border: '1px solid #1e2733',
                  borderRadius: 10,
                  color: '#e2eaf3',
                  fontFamily: 'Inter, system-ui, sans-serif',
                  height: 46,
                }}
                prefix={
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#5a6a7e" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 6 }}>
                    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                    <circle cx="12" cy="7" r="4"/>
                  </svg>
                }
              />
            </Form.Item>

            <Form.Item
              name="password"
              style={{ marginBottom: 24 }}
              rules={[{ required: true, message: 'Password is required' }]}
            >
              <Input.Password
                placeholder="Password"
                size="large"
                autoComplete="current-password"
                style={{
                  background: '#0b1018',
                  border: '1px solid #1e2733',
                  borderRadius: 10,
                  color: '#e2eaf3',
                  fontFamily: 'Inter, system-ui, sans-serif',
                  height: 46,
                }}
                prefix={
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#5a6a7e" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 6 }}>
                    <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
                    <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                  </svg>
                }
              />
            </Form.Item>

            <Form.Item style={{ marginBottom: 0 }}>
              <Button
                type="primary"
                htmlType="submit"
                loading={loading}
                block
                size="large"
                style={{
                  height: 46,
                  borderRadius: 10,
                  background: loading ? undefined : 'linear-gradient(135deg, #0ea5e9, #6366f1)',
                  border: 'none',
                  fontWeight: 600,
                  fontSize: 15,
                  fontFamily: 'Inter, system-ui, sans-serif',
                  boxShadow: '0 0 28px rgba(56,189,248,.25)',
                  letterSpacing: -.2,
                }}
              >
                {loading ? 'Signing in…' : 'Sign in →'}
              </Button>
            </Form.Item>
          </Form>

          {/* Footer */}
          <div style={{
            marginTop: 28,
            paddingTop: 20,
            borderTop: '1px solid #1e2733',
            textAlign: 'center',
          }}>
            <a href="/landing" style={{
              fontSize: 13, color: '#5a6a7e', textDecoration: 'none',
              fontFamily: 'Inter, system-ui, sans-serif',
              transition: 'color .15s',
            }}
              onMouseEnter={e => (e.currentTarget.style.color = '#8b9ab0')}
              onMouseLeave={e => (e.currentTarget.style.color = '#5a6a7e')}
            >
              ← Back to atlas.alpheric.ai
            </a>
          </div>
        </div>
      </div>

      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@700&display=swap');

        .login-left-panel { display: flex !important; }

        @media (max-width: 900px) {
          .login-left-panel { display: none !important; }
          .login-right-panel { width: 100% !important; }
        }

        /* Ant Design input overrides */
        .ant-input, .ant-input-password {
          background: #0b1018 !important;
          color: #e2eaf3 !important;
        }
        .ant-input-affix-wrapper {
          background: #0b1018 !important;
          border-color: #1e2733 !important;
        }
        .ant-input-affix-wrapper:hover,
        .ant-input-affix-wrapper-focused {
          border-color: #38bdf8 !important;
          box-shadow: 0 0 0 2px rgba(56,189,248,.12) !important;
        }
        .ant-input-password-icon { color: #5a6a7e !important; }
        .ant-input-password-icon:hover { color: #8b9ab0 !important; }
        .ant-form-item-explain-error { color: #f87171 !important; font-size: 12px; }
        .ant-btn-loading-icon { color: #fff !important; }
      `}</style>
    </div>
  );
}
