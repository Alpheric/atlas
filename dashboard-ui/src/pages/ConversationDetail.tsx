import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  Card, Tag, Button, Descriptions, Typography, Space, App, Tooltip,
  Result, Collapse, Progress, Alert, Row, Col, Statistic, Spin,
} from 'antd';
import {
  ArrowLeftOutlined, LikeOutlined, DislikeOutlined, UserOutlined,
  RobotOutlined, ToolOutlined, CopyOutlined, CodeOutlined,
  ExperimentOutlined, ReloadOutlined,
  WarningOutlined,
  HeartOutlined, BugOutlined, ClockCircleOutlined, DatabaseOutlined,
} from '@ant-design/icons';
import { getConversation, addFeedback, regenerateMessage } from '../lib/api';
import PageSkeleton from '../components/shared/PageSkeleton';
import dayjs from 'dayjs';

const { Text } = Typography;

// ── Role styling ────────────────────────────────────────────────────────────
const ROLE_CONFIG: Record<string, { icon: any; color: string; label: string }> = {
  user:      { icon: <UserOutlined />,   color: '#3b82f6', label: 'User' },
  assistant: { icon: <RobotOutlined />,  color: '#8b5cf6', label: 'Assistant' },
  system:    { icon: <ToolOutlined />,   color: '#6b7280', label: 'System' },
  tool:      { icon: <ToolOutlined />,   color: '#f59e0b', label: 'Tool' },
};

// ── Quality score → colour band ─────────────────────────────────────────────
function qualityColor(score: number): string {
  if (score >= 0.7) return '#10b981';  // green
  if (score >= 0.4) return '#f59e0b';  // amber
  return '#ef4444';                     // red
}
function qualityLabel(score: number): string {
  if (score >= 0.7) return 'Good';
  if (score >= 0.4) return 'Fair';
  return 'Poor';
}

// ── Health score banner ──────────────────────────────────────────────────────
function HealthBanner({ health }: { health: any }) {
  if (!health) return null;

  const score: number = health.score ?? 1;
  const flags = health.flags ?? {};
  const color = score >= 0.7 ? 'success' : score >= 0.4 ? 'warning' : 'error';
  const flagItems = [
    flags.stuck      && <Tag key="stuck"      color="red"    icon={<BugOutlined />}>Stuck</Tag>,
    flags.abandoned  && <Tag key="abandoned"  color="orange" icon={<ClockCircleOutlined />}>Abandoned</Tag>,
    flags.low_quality&& <Tag key="lq"         color="gold"   icon={<WarningOutlined />}>Low Quality</Tag>,
    flags.self_healed&& <Tag key="sh"         color="purple" icon={<ExperimentOutlined />}>Auto-Healed</Tag>,
  ].filter(Boolean);

  return (
    <Alert
      type={color}
      style={{ marginBottom: 16 }}
      icon={<HeartOutlined />}
      showIcon
      message={
        <Space size={12} wrap>
          <Text strong>Conversation Health</Text>
          <Space size={4}>
            <Progress
              type="circle"
              percent={Math.round(score * 100)}
              size={32}
              strokeColor={qualityColor(score)}
              format={(p) => <span style={{ fontSize: 9, color: qualityColor(score) }}>{p}</span>}
            />
            <Text style={{ fontSize: 12 }}>{Math.round(score * 100)}% — {qualityLabel(score)}</Text>
          </Space>
          {health.avg_quality != null && (
            <Text type="secondary" style={{ fontSize: 11 }}>
              Avg quality: {(health.avg_quality * 100).toFixed(0)}%
            </Text>
          )}
          {flagItems.length > 0 && <Space size={4}>{flagItems}</Space>}
        </Space>
      }
    />
  );
}

// ── Quality badge pill ───────────────────────────────────────────────────────
function QualityBadge({ score }: { score: number | null }) {
  if (score == null) return null;
  const pct = Math.round(score * 100);
  return (
    <Tooltip title={`Auto-eval quality: ${pct}% (${qualityLabel(score)})`}>
      <Tag
        style={{
          fontSize: 10,
          padding: '0 5px',
          borderColor: qualityColor(score),
          color: qualityColor(score),
          background: `${qualityColor(score)}18`,
          cursor: 'default',
        }}
      >
        ✦ {pct}%
      </Tag>
    </Tooltip>
  );
}

// ── Routing decision footer ──────────────────────────────────────────────────
function RoutingFooter({ rd }: { rd: any }) {
  if (!rd) return null;
  return (
    <div style={{ marginTop: 10, padding: '6px 8px', background: 'rgba(0,0,0,0.12)', borderRadius: 6 }}>
      <Space wrap size={4}>
        {rd.self_healed && (
          <Tag color="purple" icon={<ExperimentOutlined />} style={{ fontSize: 10 }}>
            Auto-Healed
            {rd.heal_score_before != null && (
              <span style={{ opacity: 0.7 }}> (was {Math.round(rd.heal_score_before * 100)}%)</span>
            )}
          </Tag>
        )}
        {rd.is_local
          ? <Tag color="green"  icon={<DatabaseOutlined />} style={{ fontSize: 10 }}>Local</Tag>
          : <Tag color="blue"   style={{ fontSize: 10 }}>{rd.provider}</Tag>
        }
        <Tag color="purple" style={{ fontSize: 10 }}>{rd.model}</Tag>
        {rd.task_type && <Tag color="gold" style={{ fontSize: 10 }}>{rd.task_type}</Tag>}
        {rd.cache_hit && <Tag color="cyan" style={{ fontSize: 10 }}>Cache Hit</Tag>}
        {rd.error && <Tag color="red" style={{ fontSize: 10 }}>Error</Tag>}
        <Text type="secondary" style={{ fontSize: 11 }}>
          <ClockCircleOutlined style={{ marginRight: 3 }} />{rd.latency_ms}ms
          {' · '}${rd.cost_usd?.toFixed(4)}
          {' · '}{rd.prompt_tokens}+{rd.completion_tokens} tokens
        </Text>
      </Space>
    </div>
  );
}

// ── Quality signals timeline ─────────────────────────────────────────────────
function QualitySignals({ signals }: { signals: any[] }) {
  if (!signals?.length) return null;

  const thumbsSignals = signals.filter((s) => s.type === 'thumbs');
  const evalSignals   = signals.filter((s) => s.type === 'auto_eval');

  return (
    <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center' }}>
      {evalSignals.map((s, i) => (
        <Tooltip key={i} title={`${s.evaluator ?? 'auto_eval'} · ${dayjs(s.created_at).format('HH:mm:ss')}`}>
          <Tag
            style={{
              fontSize: 9, padding: '0 4px',
              borderColor: qualityColor(s.value),
              color: qualityColor(s.value),
              background: `${qualityColor(s.value)}14`,
            }}
          >
            {s.evaluator === 'heuristic_v1' ? '⚡' : '◉'} {(s.value * 100).toFixed(0)}%
          </Tag>
        </Tooltip>
      ))}
      {thumbsSignals.map((s, i) => (
        <Tooltip key={i} title={`User feedback · ${dayjs(s.created_at).format('HH:mm:ss')}`}>
          <Tag key={i} color={s.value >= 0.5 ? 'success' : 'error'} style={{ fontSize: 9 }}>
            {s.value >= 0.5 ? '👍' : '👎'} {s.evaluator}
          </Tag>
        </Tooltip>
      ))}
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────────────
export default function ConversationDetail() {
  const { id } = useParams();
  const [conv, setConv]             = useState<any>(null);
  const [loading, setLoading]       = useState(true);
  const [regenIds, setRegenIds]     = useState<Set<string>>(new Set());
  const [feedbackIds, setFeedbackIds] = useState<Record<string, 'up' | 'down'>>({});
  const { message: messageApi }     = App.useApp();

  const reload = () => {
    if (id) getConversation(id).then(setConv).catch(() => {}).finally(() => setLoading(false));
  };

  useEffect(() => { reload(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) return <PageSkeleton type="detail" />;

  if (!conv) {
    return (
      <Result
        status="404"
        title="Conversation Not Found"
        subTitle="This conversation does not exist or has been removed."
        extra={<Link to="/conversations"><Button icon={<ArrowLeftOutlined />}>Back</Button></Link>}
      />
    );
  }

  const handleFeedback = async (msgId: string, value: number) => {
    setFeedbackIds((prev) => ({ ...prev, [msgId]: value >= 0.5 ? 'up' : 'down' }));
    await addFeedback(conv.id, msgId, value);
    if (value < 0.5) {
      messageApi.info('Feedback recorded — regenerating improved response…');
    } else {
      messageApi.success('Positive feedback recorded');
    }
  };

  const handleRegenerate = async (msgId: string) => {
    setRegenIds((prev) => new Set([...prev, msgId]));
    try {
      await regenerateMessage(conv.id, msgId);
      messageApi.info('Regeneration started — reload in a moment to see the improved response');
      setTimeout(reload, 4000); // auto-reload after 4s
    } catch {
      messageApi.error('Regeneration failed');
    } finally {
      setRegenIds((prev) => { const s = new Set(prev); s.delete(msgId); return s; });
    }
  };

  const copyMessage = (content: string) => {
    navigator.clipboard.writeText(content);
    messageApi.success('Copied to clipboard');
  };

  const hasTotals = conv.total_prompt_tokens || conv.total_completion_tokens || conv.total_cost_usd;
  const hasMetadata = conv.metadata && Object.keys(conv.metadata).length > 0;

  return (
    <div style={{ maxWidth: 940 }}>
      {/* ── Header ── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Space>
          <Link to="/conversations">
            <Button icon={<ArrowLeftOutlined />} size="small">Back</Button>
          </Link>
          <Typography.Title level={4} style={{ margin: 0 }}>Conversation</Typography.Title>
          <Tag color="purple">{conv.source}</Tag>
          {conv.healed_count > 0 && (
            <Tag color="purple" icon={<ExperimentOutlined />}>{conv.healed_count} healed</Tag>
          )}
        </Space>
        <Button icon={<ReloadOutlined />} size="small" onClick={reload}>Refresh</Button>
      </div>

      {/* ── Health banner ── */}
      <HealthBanner health={conv.health} />

      {/* ── Meta + KPIs ── */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Row gutter={[16, 8]} align="middle">
          <Col flex="auto">
            <Descriptions column={{ xs: 1, sm: 2, md: 3 }} size="small">
              <Descriptions.Item label="ID">
                <Text copyable style={{ fontFamily: 'monospace', fontSize: 11 }}>{conv.id}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="User">{conv.user_id || '—'}</Descriptions.Item>
              <Descriptions.Item label="Created">
                {conv.created_at ? dayjs(conv.created_at).format('YYYY-MM-DD HH:mm') : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="Messages">{conv.messages?.length ?? 0}</Descriptions.Item>
              {hasTotals && (
                <>
                  <Descriptions.Item label="Tokens">
                    {((conv.total_prompt_tokens ?? 0) + (conv.total_completion_tokens ?? 0)).toLocaleString()}
                  </Descriptions.Item>
                  <Descriptions.Item label="Cost">
                    ${conv.total_cost_usd?.toFixed(4)}
                  </Descriptions.Item>
                </>
              )}
            </Descriptions>
          </Col>
          {conv.health && (
            <Col>
              <Space size={16}>
                <Statistic
                  title="Health"
                  value={Math.round((conv.health.score ?? 1) * 100)}
                  suffix="%"
                  valueStyle={{ fontSize: 20, color: qualityColor(conv.health.score ?? 1) }}
                />
                {conv.health.avg_quality != null && (
                  <Statistic
                    title="Avg Quality"
                    value={Math.round((conv.health.avg_quality) * 100)}
                    suffix="%"
                    valueStyle={{ fontSize: 20, color: qualityColor(conv.health.avg_quality) }}
                  />
                )}
              </Space>
            </Col>
          )}
        </Row>

        {hasMetadata && (
          <Collapse ghost size="small" style={{ marginTop: 8 }} items={[{
            key: 'meta',
            label: <Space><CodeOutlined /><Text type="secondary" style={{ fontSize: 11 }}>Metadata</Text></Space>,
            children: (
              <pre style={{ fontSize: 11, margin: 0, padding: 8, background: 'rgba(0,0,0,0.15)', borderRadius: 4, overflowX: 'auto' }}>
                {JSON.stringify(conv.metadata, null, 2)}
              </pre>
            ),
          }]} />
        )}
      </Card>

      {/* ── Message Thread ── */}
      <Space direction="vertical" size={10} style={{ width: '100%' }}>
        {(conv.messages ?? []).map((msg: any) => {
          const cfg = ROLE_CONFIG[msg.role] ?? ROLE_CONFIG.user;
          const isAssistant = msg.role === 'assistant';
          const isHealed = msg.routing_decision?.self_healed;
          const isRegenerating = regenIds.has(msg.id);
          const feedbackState = feedbackIds[msg.id];

          return (
            <Card
              key={msg.id}
              size="small"
              style={{
                borderLeft: `3px solid ${isHealed ? '#8b5cf6' : cfg.color}`,
                opacity: isRegenerating ? 0.6 : 1,
                transition: 'opacity 0.3s',
              }}
              title={
                <Space size={6} wrap>
                  <span style={{ color: cfg.color }}>{cfg.icon}</span>
                  <Text strong style={{ textTransform: 'capitalize', fontSize: 13 }}>{cfg.label}</Text>
                  <Text type="secondary" style={{ fontSize: 10 }}>#{msg.sequence}</Text>
                  {msg.token_count != null && (
                    <Text type="secondary" style={{ fontSize: 10 }}>{msg.token_count} tok</Text>
                  )}
                  {/* Quality badge */}
                  {isAssistant && <QualityBadge score={msg.quality_score} />}
                  {/* Self-healed badge */}
                  {isHealed && (
                    <Tag color="purple" icon={<ExperimentOutlined />} style={{ fontSize: 10 }}>
                      Auto-Healed
                    </Tag>
                  )}
                  {/* Source tag for regen'd messages */}
                  {msg.routing_decision?.is_local && (
                    <Tag color="green" style={{ fontSize: 10 }}>Local</Tag>
                  )}
                </Space>
              }
              extra={
                <Space size={2}>
                  <Tooltip title="Copy">
                    <Button type="text" size="small" icon={<CopyOutlined />}
                      onClick={() => copyMessage(msg.content)} />
                  </Tooltip>
                  {isAssistant && (
                    <>
                      <Tooltip title="Good response">
                        <Button
                          type="text" size="small"
                          icon={<LikeOutlined />}
                          style={feedbackState === 'up' ? { color: '#10b981' } : undefined}
                          onClick={() => handleFeedback(msg.id, 1.0)}
                        />
                      </Tooltip>
                      <Tooltip title="Poor response — will trigger auto-improvement">
                        <Button
                          type="text" size="small"
                          icon={<DislikeOutlined />}
                          style={feedbackState === 'down' ? { color: '#ef4444' } : undefined}
                          onClick={() => handleFeedback(msg.id, 0.0)}
                        />
                      </Tooltip>
                      <Tooltip title="Regenerate this response now">
                        <Button
                          type="text" size="small"
                          icon={isRegenerating ? <Spin size="small" /> : <ReloadOutlined />}
                          onClick={() => handleRegenerate(msg.id)}
                          disabled={isRegenerating}
                        />
                      </Tooltip>
                    </>
                  )}
                </Space>
              }
            >
              {/* Message body */}
              <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 13, lineHeight: 1.6 }}>
                {msg.content}
              </div>

              {/* Routing decision footer */}
              <RoutingFooter rd={msg.routing_decision} />

              {/* Quality signals row */}
              <QualitySignals signals={msg.quality_signals} />
            </Card>
          );
        })}
      </Space>
    </div>
  );
}
