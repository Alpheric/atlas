import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Typography, Card, Row, Col, Table, Tag, Progress, Tooltip } from 'antd';
import {
  ThunderboltOutlined, FieldNumberOutlined, ClockCircleOutlined,
  CheckCircleOutlined, BarChartOutlined, DollarOutlined, BulbOutlined,
  FireOutlined,
} from '@ant-design/icons';
import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, Cell, XAxis, YAxis,
  CartesianGrid, Tooltip as RTooltip, ResponsiveContainer, AreaChart, Area, Legend,
} from 'recharts';
import {
  getMetrics, getRoutingDecisions, getModelLeaderboard, getTokenTimeseries,
  getCostTimeseries, getRequestHeatmap,
} from '../lib/api';
import DateRangeFilter from '../components/shared/DateRangeFilter';
import PageSkeleton from '../components/shared/PageSkeleton';
import StatsCard from '../components/shared/StatsCard';

const COLORS = ['#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444', '#ec4899', '#06b6d4', '#84cc16'];
const CHART_HEIGHT = 220;

function EmptyChart({ height = CHART_HEIGHT, text = 'No data yet — send requests to see analytics' }: { height?: number; text?: string }) {
  return (
    <div style={{ height, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10 }}>
      <BarChartOutlined style={{ fontSize: 30, color: '#374151' }} />
      <Typography.Text type="secondary" style={{ fontSize: 12, textAlign: 'center', maxWidth: 220 }}>{text}</Typography.Text>
    </div>
  );
}

const formatUptime = (s: number) => {
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${Math.round(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
};

// ── Request heatmap (7 days × 24 hours) ────────────────────────────────────
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function RequestHeatmap({ data }: { data: Array<{ day: string; hour: number; count: number }> }) {
  const maxCount = Math.max(...data.map((d) => d.count), 1);
  const hasData = data.some((d) => d.count > 0);

  if (!hasData) {
    return <EmptyChart height={160} text="Heatmap fills as requests come in (by hour of day)" />;
  }

  const byKey: Record<string, number> = {};
  data.forEach((d) => { byKey[`${d.day}:${d.hour}`] = d.count; });

  return (
    <div style={{ overflowX: 'auto' }}>
      {/* Hour labels */}
      <div style={{ display: 'flex', marginLeft: 32, marginBottom: 2 }}>
        {Array.from({ length: 24 }, (_, h) => (
          <div key={h} style={{ width: 18, flexShrink: 0, fontSize: 8, color: '#6b7280', textAlign: 'center' }}>
            {h % 6 === 0 ? h : ''}
          </div>
        ))}
      </div>
      {/* Grid rows */}
      {DAYS.map((day) => (
        <div key={day} style={{ display: 'flex', alignItems: 'center', marginBottom: 2 }}>
          <div style={{ width: 28, fontSize: 9, color: '#9ca3af', flexShrink: 0 }}>{day}</div>
          {Array.from({ length: 24 }, (_, h) => {
            const count = byKey[`${day}:${h}`] ?? 0;
            const intensity = count / maxCount;
            const bg = count === 0
              ? 'rgba(255,255,255,0.04)'
              : `rgba(59,130,246,${0.15 + intensity * 0.85})`;
            return (
              <Tooltip key={h} title={`${day} ${h}:00 — ${count} requests`}>
                <div
                  style={{
                    width: 18, height: 16, flexShrink: 0,
                    background: bg,
                    borderRadius: 2,
                    marginRight: 1,
                    cursor: count > 0 ? 'default' : undefined,
                    transition: 'background 0.2s',
                  }}
                />
              </Tooltip>
            );
          })}
        </div>
      ))}
      {/* Legend */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 6, marginLeft: 32 }}>
        <span style={{ fontSize: 9, color: '#6b7280' }}>Low</span>
        {[0.1, 0.3, 0.5, 0.7, 0.9].map((i) => (
          <div key={i} style={{
            width: 14, height: 10, borderRadius: 2,
            background: `rgba(59,130,246,${0.15 + i * 0.85})`,
          }} />
        ))}
        <span style={{ fontSize: 9, color: '#6b7280' }}>High</span>
      </div>
    </div>
  );
}

export default function Analytics() {
  const [dateRange, setDateRange] = useState<[string | null, string | null]>([null, null]);

  const { data, isLoading: loading } = useQuery({
    queryKey: ['analyticsBundle'],
    queryFn: async () => {
      const [m, d, lb, ts, cs, hm] = await Promise.all([
        getMetrics(),
        getRoutingDecisions({ limit: 200 }),
        getModelLeaderboard().catch(() => ({ data: [] })),
        getTokenTimeseries().catch(() => ({ data: [] })),
        getCostTimeseries().catch(() => ({ data: [] })),
        getRequestHeatmap().catch(() => ({ data: [] })),
      ]);
      return {
        metrics: m,
        decisions: d.data || [],
        leaderboard: lb.data || [],
        tokenSeries: ts.data || [],
        costSeries: cs.data || [],
        heatmapData: hm.data || [],
      };
    },
  });

  const metrics = data?.metrics ?? null;
  const decisions: any[] = data?.decisions ?? [];
  const leaderboard: any[] = data?.leaderboard ?? [];
  const tokenSeries: any[] = data?.tokenSeries ?? [];
  const costSeries: any[] = data?.costSeries ?? [];
  const heatmapData: any[] = data?.heatmapData ?? [];

  if (loading) return <PageSkeleton />;

  const totalTokens = (metrics?.total_prompt_tokens ?? 0) + (metrics?.total_completion_tokens ?? 0);
  const localPct    = metrics?.request_count
    ? Math.round(((metrics?.local?.request_count ?? 0) / metrics.request_count) * 100)
    : 0;

  // Derived chart data from routing decisions
  const costByProvider: Record<string, number> = {};
  const latencyByModel: Record<string, { total: number; count: number }> = {};
  const tokensByModel: Record<string, number> = {};
  for (const d of decisions) {
    costByProvider[d.provider] = (costByProvider[d.provider] || 0) + (d.cost_usd || 0);
    if (!latencyByModel[d.model]) latencyByModel[d.model] = { total: 0, count: 0 };
    latencyByModel[d.model].total += d.latency_ms || 0;
    latencyByModel[d.model].count += 1;
    tokensByModel[d.model] = (tokensByModel[d.model] || 0) + (d.prompt_tokens || 0) + (d.completion_tokens || 0);
  }

  const costData    = Object.entries(costByProvider).map(([name, cost]) => ({ name, cost: +cost.toFixed(4) }));
  const latencyData = Object.entries(latencyByModel).map(([name, { total, count }]) => ({
    name: name.length > 18 ? name.slice(0, 18) + '…' : name,
    avg_latency: Math.round(total / count),
  }));
  const tokenData = Object.entries(tokensByModel).map(([name, tokens]) => ({
    name: name.length > 18 ? name.slice(0, 18) + '…' : name,
    tokens,
  }));
  const taskData = metrics?.task_type_counts
    ? Object.entries(metrics.task_type_counts).map(([name, count]) => ({ name, count }))
    : [];

  // P50/P95 from leaderboard
  const latencyPercData = leaderboard
    .filter((r) => r.p50_latency || r.p95_latency)
    .slice(0, 8)
    .map((r) => ({
      name: r.model.length > 18 ? r.model.slice(0, 18) + '…' : r.model,
      p50: r.p50_latency ?? 0,
      p95: r.p95_latency ?? 0,
      avg: Math.round(r.avg_latency_ms ?? 0),
    }));

  // 8 KPI cards
  const kpiStats = [
    { title: 'Total Requests',  value: metrics?.request_count ?? 0,                          icon: <ThunderboltOutlined />, color: '#3b82f6' },
    { title: 'Total Cost',      value: `$${(metrics?.total_cost_usd ?? 0).toFixed(4)}`,      icon: <DollarOutlined />,      color: (metrics?.total_cost_usd ?? 0) > 0 ? '#f59e0b' : '#10b981' },
    { title: 'Total Tokens',    value: totalTokens.toLocaleString(),                          icon: <FieldNumberOutlined />, color: '#8b5cf6' },
    { title: 'Uptime',          value: formatUptime(metrics?.uptime_seconds ?? 0),            icon: <ClockCircleOutlined />, color: '#10b981' },
    { title: 'Local Requests',  value: metrics?.local?.request_count ?? 0,                   icon: <BulbOutlined />,        color: '#10b981' },
    { title: 'External Reqs',   value: metrics?.external?.request_count ?? 0,                icon: <ThunderboltOutlined />, color: '#3b82f6' },
    { title: 'Prompt Tokens',   value: (metrics?.total_prompt_tokens ?? 0).toLocaleString(), icon: <FieldNumberOutlined />, color: '#06b6d4' },
    { title: 'Output Tokens',   value: (metrics?.total_completion_tokens ?? 0).toLocaleString(), icon: <FieldNumberOutlined />, color: '#8b5cf6' },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>Analytics</Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Token usage, cost, latency, and model performance
          </Typography.Text>
        </div>
        <DateRangeFilter value={dateRange} onChange={setDateRange} />
      </div>

      {/* KPI Row */}
      <Row gutter={[10, 10]} style={{ marginBottom: 12 }}>
        {kpiStats.map((s) => (
          <Col key={s.title} xs={12} sm={6} md={6} lg={3}>
            <StatsCard {...s} />
          </Col>
        ))}
      </Row>

      {/* Row 1: Local vs External + Token Trend */}
      <Row gutter={[10, 10]} style={{ marginBottom: 10 }}>
        <Col xs={24} md={8}>
          <Card size="small" title="Local vs External" style={{ height: '100%' }}>
            <div style={{ textAlign: 'center', padding: '8px 0' }}>
              <Progress
                type="dashboard"
                percent={localPct}
                strokeColor="#10b981"
                format={(pct) => `${pct}%\nLocal`}
                size={110}
              />
              <div style={{ marginTop: 12, display: 'flex', justifyContent: 'center', gap: 8 }}>
                <Tag color="green" icon={<CheckCircleOutlined />}>
                  {metrics?.local?.request_count ?? 0} Local
                </Tag>
                <Tag color="blue">
                  {metrics?.external?.request_count ?? 0} External
                </Tag>
              </div>
            </div>
          </Card>
        </Col>
        <Col xs={24} md={16}>
          <Card size="small" title="Token Usage Trend" style={{ height: '100%' }}>
            {tokenSeries.length > 0 ? (
              <ResponsiveContainer width="100%" height={180}>
                <AreaChart data={tokenSeries}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis dataKey="time" tick={{ fontSize: 9 }} tickFormatter={(v) => v.split('T')[1] || v} />
                  <YAxis tick={{ fontSize: 10 }} />
                  <RTooltip />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  <Area type="monotone" dataKey="local"    stackId="1" stroke="#10b981" fill="#10b981" fillOpacity={0.3} name="Local" />
                  <Area type="monotone" dataKey="external" stackId="1" stroke="#3b82f6" fill="#3b82f6" fillOpacity={0.3} name="External" />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <EmptyChart height={180} />
            )}
          </Card>
        </Col>
      </Row>

      {/* Row 2: Cost Trend + Latency Percentiles */}
      <Row gutter={[10, 10]} style={{ marginBottom: 10 }}>
        <Col xs={24} md={12}>
          <Card size="small" title={<span><DollarOutlined style={{ marginRight: 6 }} />Cost Trend</span>} style={{ height: '100%' }}>
            {costSeries.length > 0 ? (
              <ResponsiveContainer width="100%" height={180}>
                <LineChart data={costSeries}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis dataKey="time" tick={{ fontSize: 9 }} tickFormatter={(v) => v.split('T')[1] || v} />
                  <YAxis tick={{ fontSize: 10 }} tickFormatter={(v) => `$${Number(v).toFixed(3)}`} />
                  <RTooltip formatter={(v: any) => [`$${Number(v).toFixed(5)}`, 'Cost']} />
                  <Line
                    type="monotone" dataKey="cost" stroke="#f59e0b"
                    strokeWidth={2} dot={false} name="Cost ($)"
                  />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <EmptyChart height={180} text="Cost trend appears after requests are processed" />
            )}
          </Card>
        </Col>
        <Col xs={24} md={12}>
          <Card size="small" title={<span><ClockCircleOutlined style={{ marginRight: 6 }} />Latency Percentiles (P50 / P95)</span>} style={{ height: '100%' }}>
            {latencyPercData.length > 0 ? (
              <ResponsiveContainer width="100%" height={180}>
                <BarChart data={latencyPercData} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={(v) => `${v}ms`} />
                  <YAxis type="category" dataKey="name" tick={{ fontSize: 9 }} width={120} />
                  <RTooltip formatter={(v: any, name: string) => [`${v}ms`, name]} />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  <Bar dataKey="p50" fill="#10b981" name="P50" radius={[0, 3, 3, 0]} barSize={7} />
                  <Bar dataKey="p95" fill="#f59e0b" name="P95" radius={[0, 3, 3, 0]} barSize={7} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <EmptyChart height={180} text="Latency percentiles appear after requests are processed" />
            )}
          </Card>
        </Col>
      </Row>

      {/* Row 3: Request Heatmap */}
      <Card
        size="small"
        title={<span><FireOutlined style={{ marginRight: 6, color: '#f59e0b' }} />Request Volume Heatmap</span>}
        style={{ marginBottom: 10 }}
      >
        <RequestHeatmap data={heatmapData} />
      </Card>

      {/* Model Leaderboard */}
      <Card size="small" title="Model Performance Leaderboard" style={{ marginBottom: 10 }}>
        {leaderboard.length > 0 ? (
          <Table
            dataSource={leaderboard} rowKey="model" size="small" pagination={false}
            columns={[
              {
                title: 'Model', dataIndex: 'model', width: 200,
                render: (m: string) => <span style={{ fontFamily: 'monospace', fontSize: 11 }}>{m}</span>,
              },
              { title: 'Requests', dataIndex: 'requests', width: 80, sorter: (a: any, b: any) => a.requests - b.requests },
              {
                title: 'Tokens (In/Out)', width: 150,
                render: (_: any, r: any) => (
                  <span style={{ fontSize: 10 }}>
                    <Tag color="blue"   style={{ fontSize: 9 }}>{r.prompt_tokens.toLocaleString()} in</Tag>
                    <Tag color="purple" style={{ fontSize: 9 }}>{r.completion_tokens.toLocaleString()} out</Tag>
                  </span>
                ),
              },
              {
                title: 'Avg Latency', dataIndex: 'avg_latency_ms', width: 100,
                render: (v: number) => (
                  <span style={{ color: v > 5000 ? '#ef4444' : v > 2000 ? '#f59e0b' : '#10b981', fontWeight: 600 }}>
                    {Math.round(v)}ms
                  </span>
                ),
                sorter: (a: any, b: any) => a.avg_latency_ms - b.avg_latency_ms,
              },
              {
                title: 'P50', dataIndex: 'p50_latency', width: 80,
                render: (v: number) => <span style={{ fontSize: 11, color: '#10b981' }}>{v ?? '—'}ms</span>,
                sorter: (a: any, b: any) => (a.p50_latency ?? 0) - (b.p50_latency ?? 0),
              },
              {
                title: 'P95', dataIndex: 'p95_latency', width: 80,
                render: (v: number) => <span style={{ fontSize: 11, color: '#f59e0b' }}>{v ?? '—'}ms</span>,
                sorter: (a: any, b: any) => (a.p95_latency ?? 0) - (b.p95_latency ?? 0),
              },
              {
                title: 'Cost', dataIndex: 'cost_usd', width: 80,
                render: (v: number) => (
                  <span style={{ color: v > 0 ? '#f59e0b' : '#10b981', fontSize: 11 }}>${v.toFixed(4)}</span>
                ),
              },
              {
                title: 'Err%', dataIndex: 'error_rate', width: 70,
                render: (v: number) => <Tag color={v > 0 ? 'red' : 'green'} style={{ fontSize: 9 }}>{v}%</Tag>,
              },
            ]}
          />
        ) : (
          <EmptyChart text="Model performance data will appear after requests are processed" />
        )}
      </Card>

      {/* Charts Grid */}
      <Row gutter={[10, 10]}>
        <Col xs={24} lg={12}>
          <Card title="Cost by Provider" size="small" style={{ height: '100%' }}>
            {costData.length > 0 ? (
              <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
                <BarChart data={costData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <RTooltip formatter={(v: any) => [`$${Number(v).toFixed(4)}`, 'Cost']} />
                  <Bar dataKey="cost" fill="#10b981" name="Cost ($)" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : <EmptyChart />}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="Avg Latency by Model" size="small" style={{ height: '100%' }}>
            {latencyData.length > 0 ? (
              <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
                <BarChart data={latencyData} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis type="number" tick={{ fontSize: 11 }} />
                  <YAxis type="category" dataKey="name" tick={{ fontSize: 10 }} width={130} />
                  <RTooltip />
                  <Bar dataKey="avg_latency" fill="#f59e0b" name="Latency (ms)" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : <EmptyChart />}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="Token Usage by Model" size="small" style={{ height: '100%' }}>
            {tokenData.length > 0 ? (
              <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
                <PieChart>
                  <Pie data={tokenData} dataKey="tokens" nameKey="name" cx="50%" cy="50%" outerRadius={75} innerRadius={32}>
                    {tokenData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                  </Pie>
                  <RTooltip formatter={(v: any) => [Number(v).toLocaleString(), 'Tokens']} />
                </PieChart>
              </ResponsiveContainer>
            ) : <EmptyChart />}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="Task Type Distribution" size="small" style={{ height: '100%' }}>
            {taskData.length > 0 ? (
              <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
                <BarChart data={taskData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis dataKey="name" tick={{ fontSize: 10 }} interval={0} angle={-30} textAnchor="end" height={45} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <RTooltip />
                  <Bar dataKey="count" fill="#8b5cf6" name="Requests" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : <EmptyChart />}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
