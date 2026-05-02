import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Row, Col, Card, Tag, List, Typography, Badge, Progress, Space,
} from 'antd';
import {
  ThunderboltOutlined,
  DashboardOutlined,
  ClockCircleOutlined,
  DollarOutlined,
  WarningOutlined,
  MessageOutlined,
  CloudServerOutlined,
  RocketOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ApiOutlined,
  TeamOutlined,
  ExperimentOutlined,
  BulbOutlined,
} from '@ant-design/icons';
import {
  BarChart, Bar,
  PieChart, Pie, Cell,
  ResponsiveContainer, Tooltip,
  XAxis, YAxis, CartesianGrid,
} from 'recharts';
import { getOverview, getDailyStats } from '../lib/api';
import { useWebSocketStore } from '../stores/websocketStore';
import PageSkeleton from '../components/shared/PageSkeleton';
import StatsCard from '../components/shared/StatsCard';
import { COLORS, LATENCY_WARN_MS } from '../lib/tokens';
import type { OverviewData, DailyStatPoint, PoolAccountStatus, DistillationTaskType } from '../types';

const { Text } = Typography;

export default function Overview() {
  const wsSubscribe = useWebSocketStore((s) => s.subscribe);
  const wsConnect   = useWebSocketStore((s) => s.connect);

  // Primary data — 5s refresh. Embeds db_stats, pool_status, distillation_summary.
  const { data, isLoading } = useQuery<OverviewData>({
    queryKey: ['overview'],
    queryFn: getOverview,
    refetchInterval: 5_000,
  });

  // Daily trend — historical, 60s refresh.
  const { data: dailyStats = [] } = useQuery<DailyStatPoint[]>({
    queryKey: ['dailyStats'],
    queryFn: async () => {
      const r = await getDailyStats(7);
      return r.data ?? [];
    },
    refetchInterval: 60_000,
  });

  useEffect(() => {
    wsConnect();
    const unsub = wsSubscribe('*', () => {});
    return () => unsub();
  }, []);

  if (isLoading) return <PageSkeleton type="cards" />;

  const m   = data?.metrics;
  const db  = data?.db_stats;
  const pool = data?.pool_status ?? [];
  const dist = data?.distillation_summary;

  // "Live" = in-memory has data since last restart
  const hasLiveData = (m?.request_count ?? 0) > 0;

  const totalTokens = (db?.total_prompt_tokens ?? 0) + (db?.total_completion_tokens ?? 0);
  const healthyAccounts = pool.filter((a) => a.healthy).length;

  // ── KPI cards ─────────────────────────────────────────────────────────
  const kpiStats = [
    {
      title: 'Requests',
      value: db?.total_requests ?? 0,
      icon: <ThunderboltOutlined />,
      color: '#3b82f6',
      live: hasLiveData,
    },
    {
      title: 'Total Cost',
      value: db?.total_cost_usd ?? 0,
      icon: <DollarOutlined />,
      precision: 4,
      color: '#f59e0b',
      live: hasLiveData,
    },
    {
      title: 'Avg Latency',
      value: db?.avg_latency_ms ?? 0,
      icon: <ClockCircleOutlined />,
      suffix: 'ms',
      color: (db?.avg_latency_ms ?? 0) > LATENCY_WARN_MS ? '#ef4444' : '#10b981',
      live: hasLiveData,
    },
    {
      title: 'Conversations',
      value: data?.conversations_count ?? 0,
      icon: <MessageOutlined />,
    },
    {
      title: 'Local %',
      value: db?.local_pct ?? 0,
      icon: <BulbOutlined />,
      suffix: '%',
      color: '#10b981',
    },
    {
      title: 'Tokens',
      value: totalTokens,
      icon: <ApiOutlined />,
      live: hasLiveData,
    },
    {
      title: 'Errors',
      value: db?.error_count ?? 0,
      icon: <WarningOutlined />,
      color: (db?.error_count ?? 0) > 0 ? '#ef4444' : '#10b981',
    },
    {
      title: 'CLI Pool',
      value: `${healthyAccounts}/${pool.length}`,
      icon: <TeamOutlined />,
      color: healthyAccounts === pool.length && pool.length > 0 ? '#10b981' : '#ef4444',
    },
    {
      title: 'Self-Healed',
      value: db?.self_healed_count ?? 0,
      icon: <ExperimentOutlined />,
      color: (db?.self_healed_count ?? 0) > 0 ? '#8b5cf6' : '#6b7280',
    },
  ];

  return (
    <div>

      {/* ── Row 1: Hero ──────────────────────────────────────────────────── */}
      <Card
        size="small"
        style={{
          marginBottom: 16,
          background: 'linear-gradient(135deg, rgba(59,130,246,0.12) 0%, rgba(139,92,246,0.08) 100%)',
          border: '1px solid rgba(59,130,246,0.2)',
        }}
      >
        <Row align="middle" gutter={24}>
          <Col flex="auto">
            <Space align="center" size={12}>
              <RocketOutlined style={{ fontSize: 28, color: '#3b82f6' }} />
              <div>
                <Typography.Title level={4} style={{ margin: 0 }}>
                  Alpheric.AI — Atlas <Tag color="blue">Live</Tag>
                </Typography.Title>
                <Text type="secondary">
                  Smart-routing to {data?.providers?.filter((p) => p.healthy).length ?? 0} providers
                  ,{' '}
                  {data?.providers?.reduce((s, p) => s + (p.models?.length ?? 0), 0) ?? 0} models
                </Text>
              </div>
            </Space>
          </Col>
          <Col>
            <Space wrap>
              <Tag color="green">{db?.local_pct ?? 0}% Local</Tag>
              <Tag color="gold">${(db?.total_cost_usd ?? 0).toFixed(4)} total cost</Tag>
              <Tag color="blue">{totalTokens.toLocaleString()} tokens</Tag>
              <Tag color="purple" icon={<TeamOutlined />}>
                {healthyAccounts}/{pool.length} CLI accounts
              </Tag>
            </Space>
          </Col>
        </Row>
      </Card>

      {/* ── Row 2: KPI cards ─────────────────────────────────────────────── */}
      <Row gutter={[12, 12]}>
        {kpiStats.map((stat) => (
          <Col key={stat.title} xs={12} sm={8} md={6} lg={3}>
            <StatsCard {...stat} />
          </Col>
        ))}
      </Row>

      {/* ── Row 3: Daily trend + CLI Pool ────────────────────────────────── */}
      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} lg={14}>
          <Card title="7-Day Activity" size="small">
            {dailyStats.length > 0 ? (
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={dailyStats} margin={{ top: 4, right: 20, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis
                    dataKey="day"
                    tick={{ fontSize: 10 }}
                    tickFormatter={(v: string) => v.slice(5)}
                  />
                  <YAxis yAxisId="left"  tick={{ fontSize: 10 }} />
                  <YAxis
                    yAxisId="right"
                    orientation="right"
                    tick={{ fontSize: 10 }}
                    tickFormatter={(v) => `$${Number(v).toFixed(3)}`}
                  />
                  <Tooltip
                    formatter={(val: any, name: string) =>
                      name === 'cost_usd'
                        ? [`$${Number(val).toFixed(4)}`, 'Cost (USD)']
                        : [val, 'Requests']
                    }
                  />
                  <Bar yAxisId="left"  dataKey="requests" fill="#3b82f6" name="requests" radius={[2,2,0,0]} />
                  <Bar yAxisId="right" dataKey="cost_usd" fill="#f59e0b" name="cost_usd" radius={[2,2,0,0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div style={{ height: 220, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column' }}>
                <DashboardOutlined style={{ fontSize: 32, color: '#4b5563', marginBottom: 8 }} />
                <Text type="secondary">Send requests to see daily trends</Text>
              </div>
            )}
          </Card>
        </Col>

        <Col xs={24} lg={10}>
          <Card
            title={<span><TeamOutlined style={{ marginRight: 6 }} />Claude CLI Pool</span>}
            size="small"
            style={{ height: '100%' }}
          >
            {pool.length === 0 ? (
              <Text type="secondary">No pool accounts configured</Text>
            ) : (
              pool.map((acc: PoolAccountStatus) => (
                <Card
                  key={acc.user}
                  size="small"
                  style={{ marginBottom: 8, borderLeft: `3px solid ${acc.healthy ? '#10b981' : '#ef4444'}` }}
                >
                  <Row justify="space-between" align="middle">
                    <Space size={6}>
                      <Badge status={acc.healthy ? 'success' : 'error'} />
                      <Text strong style={{ fontFamily: 'monospace' }}>{acc.user}</Text>
                      <Tag color={acc.healthy ? 'success' : 'error'} style={{ fontSize: 10, margin: 0 }}>
                        {acc.healthy ? 'Healthy' : 'Down'}
                      </Tag>
                    </Space>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {acc.sessions} active session{acc.sessions !== 1 ? 's' : ''}
                    </Text>
                  </Row>
                  <Text type="secondary" style={{ fontSize: 11, marginTop: 4, display: 'block' }}>
                    {acc.requests} reqs
                    {' · '}{acc.input_tokens.toLocaleString()} in
                    {' · '}{acc.output_tokens.toLocaleString()} out
                    {acc.cost_usd > 0 ? ` · $${acc.cost_usd.toFixed(4)}` : ''}
                  </Text>
                </Card>
              ))
            )}
          </Card>
        </Col>
      </Row>

      {/* ── Row 4: Distillation + Recent Requests ────────────────────────── */}
      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} lg={10}>
          <Card
            title={<span><ExperimentOutlined style={{ marginRight: 6 }} />Distillation Pipeline</span>}
            size="small"
          >
            {!dist?.enabled ? (
              <Text type="secondary">Distillation disabled (set A1_DISTILLATION_ENABLED=true)</Text>
            ) : (dist?.task_types ?? []).length === 0 ? (
              <Text type="secondary">No samples collected yet — send requests to start building training data</Text>
            ) : (
              (dist?.task_types ?? []).map((tt: DistillationTaskType) => {
                const pct = Math.min(Math.round((tt.claude_samples / tt.training_threshold) * 100), 100);
                return (
                  <div key={tt.task_type} style={{ marginBottom: 16 }}>
                    <Row justify="space-between" style={{ marginBottom: 2 }}>
                      <Text style={{ fontSize: 12, textTransform: 'capitalize', fontWeight: 500 }}>
                        {tt.task_type}
                      </Text>
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        {tt.claude_samples} / {tt.training_threshold} samples
                      </Text>
                    </Row>
                    <Progress
                      percent={pct}
                      strokeColor={tt.ready_for_training ? '#10b981' : '#3b82f6'}
                      size="small"
                      format={() =>
                        tt.ready_for_training
                          ? '✓ Ready to train'
                          : `${tt.remaining} more needed`
                      }
                    />
                    {tt.local_handoff_pct > 0 && (
                      <Text type="secondary" style={{ fontSize: 10 }}>
                        {tt.local_handoff_pct}% requests currently routed locally
                      </Text>
                    )}
                  </div>
                );
              })
            )}
          </Card>
        </Col>

        <Col xs={24} lg={14}>
          <Card
            title={
              <span>
                Recent Requests
                {hasLiveData && (
                  <Badge dot status="processing" style={{ marginLeft: 8 }} />
                )}
              </span>
            }
            size="small"
          >
            <List
              dataSource={db?.recent_requests ?? []}
              locale={{ emptyText: 'No requests yet — try the Playground!' }}
              style={{ height: 260, overflowY: 'auto' }}
              renderItem={(req: any) => (
                <List.Item style={{ padding: '4px 0', borderBottom: 'none' }}>
                  <div style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
                    <Tag
                      color={req.is_local ? 'green' : req.cache_hit ? 'purple' : 'blue'}
                      style={{ fontSize: 10, margin: 0, minWidth: 40, textAlign: 'center' }}
                    >
                      {req.is_local ? 'LOCAL' : req.cache_hit ? 'CACHE' : 'EXT'}
                    </Tag>
                    <Text style={{ fontSize: 11, flex: 1, fontFamily: 'monospace' }} ellipsis>
                      {req.model}
                    </Text>
                    <Text type="secondary" style={{ fontSize: 10 }}>
                      {req.prompt_tokens}→{req.completion_tokens}t
                    </Text>
                    <Text
                      style={{
                        fontSize: 10,
                        color: req.latency_ms > LATENCY_WARN_MS ? '#ef4444' : '#10b981',
                        minWidth: 55,
                        textAlign: 'right',
                      }}
                    >
                      {req.latency_ms}ms
                    </Text>
                    <Text type="secondary" style={{ fontSize: 10, minWidth: 50, textAlign: 'right' }}>
                      ${req.cost_usd.toFixed(4)}
                    </Text>
                  </div>
                </List.Item>
              )}
            />
          </Card>
        </Col>
      </Row>

      {/* ── Row 5: Provider distribution + Infrastructure ────────────────── */}
      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} lg={8}>
          <Card title="Provider Distribution (all-time)" size="small">
            {Object.keys(db?.provider_counts ?? {}).length > 0 ? (
              <>
                <ResponsiveContainer width="100%" height={180}>
                  <PieChart>
                    <Pie
                      data={Object.entries(db?.provider_counts ?? {}).map(([name, value]) => ({ name, value }))}
                      dataKey="value"
                      nameKey="name"
                      cx="50%"
                      cy="50%"
                      outerRadius={60}
                      innerRadius={30}
                    >
                      {Object.keys(db?.provider_counts ?? {}).map((_, i) => (
                        <Cell key={i} fill={COLORS[i % COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip />
                  </PieChart>
                </ResponsiveContainer>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {Object.entries(db?.provider_counts ?? {}).map(([name, count], i) => (
                    <Tag key={name} color={COLORS[i % COLORS.length]}>
                      {name}: {count}
                    </Tag>
                  ))}
                </div>
              </>
            ) : (
              <div style={{ height: 180, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column' }}>
                <CloudServerOutlined style={{ fontSize: 32, color: '#4b5563', marginBottom: 8 }} />
                <Text type="secondary">No request history yet</Text>
              </div>
            )}
          </Card>
        </Col>

        <Col xs={24} lg={16}>
          <Card title="Infrastructure Health" size="small">
            <Row gutter={[12, 12]}>
              {(data?.providers ?? []).map((p) => (
                <Col key={p.name} xs={12} sm={8}>
                  <Card
                    size="small"
                    style={{ borderLeft: `3px solid ${p.healthy ? '#10b981' : '#ef4444'}` }}
                    hoverable
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <Text strong style={{ textTransform: 'capitalize' }}>{p.name}</Text>
                      <Tag
                        color={p.healthy ? 'success' : 'error'}
                        icon={p.healthy ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
                      >
                        {p.healthy ? 'Healthy' : 'Down'}
                      </Tag>
                    </div>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {p.models?.length ?? 0} models
                    </Text>

                    {/* Inline pool accounts for claude-cli */}
                    {p.name === 'claude-cli' && pool.length > 0 && (
                      <div style={{ marginTop: 4 }}>
                        {pool.map((acc) => (
                          <div key={acc.user} style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 2 }}>
                            <Badge status={acc.healthy ? 'success' : 'error'} />
                            <Text style={{ fontSize: 10, fontFamily: 'monospace' }}>{acc.user}</Text>
                            <Text type="secondary" style={{ fontSize: 10 }}>
                              {acc.requests}r
                            </Text>
                          </div>
                        ))}
                      </div>
                    )}

                    <div style={{ marginTop: 4, display: 'flex', flexWrap: 'wrap', gap: 2 }}>
                      {(p.models || []).slice(0, 3).map((model: string) => (
                        <Tag key={model} style={{ fontSize: 9, margin: 0 }}>{model}</Tag>
                      ))}
                      {(p.models?.length ?? 0) > 3 && (
                        <Tag style={{ fontSize: 9 }}>+{(p.models?.length ?? 0) - 3}</Tag>
                      )}
                    </div>
                  </Card>
                </Col>
              ))}
            </Row>
          </Card>
        </Col>
      </Row>

    </div>
  );
}
