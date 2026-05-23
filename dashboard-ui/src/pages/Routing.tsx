import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Typography, Card, Table, Tag, Space, Row, Col, Statistic, Button,
  Progress, Badge, Select,
} from 'antd';
import {
  BranchesOutlined, ClockCircleOutlined, ThunderboltOutlined, DollarOutlined,
  CheckCircleOutlined, ReloadOutlined, RocketOutlined, SafetyCertificateOutlined,
  ExperimentOutlined, CloudServerOutlined, DatabaseOutlined, FireOutlined,
  ApiOutlined,
} from '@ant-design/icons';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RTooltip,
  ResponsiveContainer, PieChart, Pie, Cell,
} from 'recharts';
import {
  getRoutingDecisions, getModelLeaderboard,
  getDistillationOverview, getOverview,
} from '../lib/api';
import ExportDropdown from '../components/shared/ExportDropdown';
import DateRangeFilter from '../components/shared/DateRangeFilter';
import PageSkeleton from '../components/shared/PageSkeleton';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
dayjs.extend(relativeTime);

const COLORS = ['#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444', '#ec4899', '#06b6d4', '#84cc16'];

const ATLAS_INFO: Record<string, { icon: any; color: string; description: string }> = {
  'atlas-plan': { icon: <BranchesOutlined />, color: '#3b82f6', description: 'Planning & Discussion' },
  'atlas-code': { icon: <ApiOutlined />, color: '#8b5cf6', description: 'Code Generation' },
  'atlas-secure': { icon: <SafetyCertificateOutlined />, color: '#ef4444', description: 'Security Analysis' },
  'atlas-infra': { icon: <CloudServerOutlined />, color: '#06b6d4', description: 'Infrastructure' },
  'atlas-data': { icon: <DatabaseOutlined />, color: '#f59e0b', description: 'Data Analysis' },
  'atlas-books': { icon: <FireOutlined />, color: '#10b981', description: 'Documentation' },
  'atlas-audit': { icon: <ExperimentOutlined />, color: '#ec4899', description: 'Compliance Audit' },
};

export default function Routing() {
  const [taskFilter, setTaskFilter] = useState<string | undefined>();
  const [dateRange, setDateRange] = useState<[string | null, string | null]>([null, null]);

  // Filters live in the queryKey, so changing them refetches automatically.
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['routingBundle', taskFilter, dateRange[0], dateRange[1]],
    queryFn: async () => {
      const routingParams: any = { limit: 200 };
      if (dateRange[0]) routingParams.date_from = dateRange[0];
      if (dateRange[1]) routingParams.date_to = dateRange[1];
      if (taskFilter) routingParams.task_type = taskFilter;
      const [d, lb, dist, ov] = await Promise.all([
        getRoutingDecisions(routingParams),
        getModelLeaderboard().catch(() => ({ data: [] })),
        getDistillationOverview().catch(() => null),
        getOverview().catch(() => null),
      ]);
      return { decisions: d.data || [], leaderboard: lb.data || [], distillation: dist, overview: ov };
    },
    refetchInterval: 10_000,
  });

  const decisions: any[] = data?.decisions ?? [];
  const leaderboard: any[] = data?.leaderboard ?? [];
  const distillation = data?.distillation ?? null;
  const overview = data?.overview ?? null;
  const load = () => refetch();

  if (isLoading) return <PageSkeleton type="table" />;

  // KPIs derived from DB-backed decisions (survive server restarts)
  const totalReqs = decisions.length || overview?.metrics?.request_count || 0;
  const localReqs = decisions.filter((r: any) => r.is_local).length;
  const externalReqs = totalReqs - localReqs;
  const localPct = totalReqs > 0 ? Math.round((localReqs / totalReqs) * 100) : 0;
  const avgLatency = totalReqs > 0 ? Math.round(decisions.reduce((s: number, r: any) => s + (r.latency_ms || 0), 0) / totalReqs) : 0;
  const allTaskTypes = [...new Set(decisions.map((r: any) => r.task_type).filter(Boolean))];
  const models = [...new Set(decisions.map((r: any) => r.model).filter(Boolean))];
  const totalCost = decisions.reduce((s: number, r: any) => s + (r.cost_usd || 0), 0);
  const totalTokens = decisions.reduce((s: number, r: any) => s + (r.prompt_tokens || 0) + (r.completion_tokens || 0), 0);

  // Task type distribution for pie chart (from DB decisions)
  const taskDist = decisions.reduce((acc: Record<string, number>, r: any) => {
    const t = r.task_type || 'unknown';
    acc[t] = (acc[t] || 0) + 1;
    return acc;
  }, {});
  const taskPieData = Object.entries(taskDist).map(([name, value]) => ({ name, value }));

  // Provider distribution (from DB decisions)
  const provDist = decisions.reduce((acc: Record<string, number>, r: any) => {
    acc[r.provider] = (acc[r.provider] || 0) + 1;
    return acc;
  }, {});
  const provPieData = Object.entries(provDist).map(([name, value]) => ({ name, value }));

  const formatLatency = (ms: number) => ms > 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
  const latencyColor = (ms: number) => ms > 10000 ? '#ef4444' : ms > 3000 ? '#f59e0b' : '#10b981';

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>Routing Intelligence</Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Smart routing decisions, Atlas model performance, distillation progress
          </Typography.Text>
        </div>
        <Space>
          <Select
            allowClear
            placeholder="All task types"
            style={{ width: 140 }}
            size="small"
            value={taskFilter}
            onChange={(v) => setTaskFilter(v)}
            options={allTaskTypes.map((t) => ({ label: t, value: t }))}
          />
          <DateRangeFilter value={dateRange} onChange={setDateRange} />
          <Button icon={<ReloadOutlined />} onClick={load} size="small">Refresh</Button>
          <ExportDropdown data={decisions} filename="routing-decisions" />
        </Space>
      </div>

      {/* KPI Row */}
      <Row gutter={[10, 10]} style={{ marginBottom: 12 }}>
        {[
          { title: 'Total Requests', value: totalReqs, icon: <ThunderboltOutlined />, color: '#3b82f6' },
          { title: 'Local %', value: `${localPct}%`, icon: <CheckCircleOutlined />, color: '#10b981' },
          { title: 'External', value: externalReqs, icon: <CloudServerOutlined />, color: '#8b5cf6' },
          { title: 'Avg Latency', value: formatLatency(avgLatency), icon: <ClockCircleOutlined />, color: latencyColor(avgLatency) },
          { title: 'Total Cost', value: `$${totalCost.toFixed(4)}`, icon: <DollarOutlined />, color: totalCost > 0 ? '#f59e0b' : '#10b981' },
          { title: 'Total Tokens', value: totalTokens.toLocaleString(), icon: <DatabaseOutlined />, color: '#06b6d4' },
          { title: 'Models Active', value: models.length, icon: <RocketOutlined />, color: '#f59e0b' },
          { title: 'Task Types', value: allTaskTypes.length, icon: <BranchesOutlined />, color: '#ec4899' },
        ].map((s) => (
          <Col key={s.title} xs={12} sm={6} md={3}>
            <Card size="small" hoverable>
              <Statistic title={s.title} value={s.value} prefix={s.icon} valueStyle={{ color: s.color, fontSize: 18 }} />
            </Card>
          </Col>
        ))}
      </Row>

      {/* Distillation Progress */}
      {distillation?.enabled && (
        <Card size="small" title={
          <Space><ExperimentOutlined style={{ color: '#8b5cf6' }} /> <span>Distillation Pipeline — Claude → Local Training</span>
            <Tag color="purple">Teacher: {distillation.teacher_model}</Tag></Space>
        } style={{ marginBottom: 12 }}>
          <Row gutter={[12, 12]}>
            {(distillation.task_types || []).map((tt: any) => {
              const info = ATLAS_INFO[`atlas-${tt.task_type === 'chat' ? 'plan' : tt.task_type}`];
              const progress = Math.round((tt.claude_samples / tt.training_threshold) * 100);
              return (
                <Col key={tt.task_type} xs={24} sm={12} md={8} lg={6}>
                  <Card size="small" hoverable style={{ borderLeft: `3px solid ${info?.color || '#6b7280'}` }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                      <Space size={4}>
                        {info?.icon}
                        <Typography.Text strong style={{ fontSize: 12 }}>{tt.task_type}</Typography.Text>
                      </Space>
                      <Tag color={tt.ready_for_training ? 'green' : 'default'} style={{ fontSize: 10 }}>
                        {tt.ready_for_training ? 'Ready' : `${tt.claude_samples}/${tt.training_threshold}`}
                      </Tag>
                    </div>
                    <Progress
                      percent={Math.min(progress, 100)}
                      size="small"
                      strokeColor={tt.ready_for_training ? '#10b981' : '#3b82f6'}
                      format={() => `${tt.claude_samples} samples`}
                    />
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#6b7280', marginTop: 4 }}>
                      <span>Handoff: {tt.local_handoff_pct}%</span>
                      <span>{tt.total_comparisons} comparisons</span>
                    </div>
                  </Card>
                </Col>
              );
            })}
          </Row>
        </Card>
      )}

      {/* Charts Row */}
      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col xs={24} lg={8}>
          <Card title="Task Distribution" size="small">
            {taskPieData.length > 0 ? (
              <>
                <ResponsiveContainer width="100%" height={180}>
                  <PieChart>
                    <Pie data={taskPieData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={60} innerRadius={30}>
                      {taskPieData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                    </Pie>
                    <RTooltip />
                  </PieChart>
                </ResponsiveContainer>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {taskPieData.map((d, i) => (
                    <Tag key={d.name} color={COLORS[i % COLORS.length]} style={{ fontSize: 10 }}>{d.name}: {d.value as number}</Tag>
                  ))}
                </div>
              </>
            ) : (
              <div style={{ height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column' }}>
                <BranchesOutlined style={{ fontSize: 32, color: '#4b5563', marginBottom: 8 }} />
                <Typography.Text type="secondary">Send requests to see distribution</Typography.Text>
              </div>
            )}
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="Provider Split" size="small">
            {provPieData.length > 0 ? (
              <>
                <ResponsiveContainer width="100%" height={180}>
                  <PieChart>
                    <Pie data={provPieData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={60} innerRadius={30}>
                      {provPieData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                    </Pie>
                    <RTooltip />
                  </PieChart>
                </ResponsiveContainer>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {provPieData.map((d, i) => (
                    <Tag key={d.name} color={COLORS[i % COLORS.length]} style={{ fontSize: 10 }}>{d.name}: {d.value as number}</Tag>
                  ))}
                </div>
              </>
            ) : (
              <div style={{ height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column' }}>
                <CloudServerOutlined style={{ fontSize: 32, color: '#4b5563', marginBottom: 8 }} />
                <Typography.Text type="secondary">Provider data will appear here</Typography.Text>
              </div>
            )}
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="Latency by Model" size="small">
            {leaderboard.length > 0 ? (
              <ResponsiveContainer width="100%" height={210}>
                <BarChart data={leaderboard} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis type="number" tick={{ fontSize: 10 }} />
                  <YAxis type="category" dataKey="model" tick={{ fontSize: 9 }} width={130} />
                  <RTooltip />
                  <Bar dataKey="avg_latency_ms" fill="#f59e0b" name="Avg Latency (ms)" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div style={{ height: 210, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column' }}>
                <ClockCircleOutlined style={{ fontSize: 32, color: '#4b5563', marginBottom: 8 }} />
                <Typography.Text type="secondary">Latency data will appear here</Typography.Text>
              </div>
            )}
          </Card>
        </Col>
      </Row>

      {/* Model Leaderboard */}
      <Card title="Model Performance Leaderboard" size="small" style={{ marginBottom: 12 }}>
        {leaderboard.length > 0 ? (
          <Table columns={[
            {
              title: 'Model', dataIndex: 'model', width: 200,
              render: (m: string) => {
                const atlas = ATLAS_INFO[m];
                return (
                  <Space size={4}>
                    {atlas && <span style={{ color: atlas.color }}>{atlas.icon}</span>}
                    <span style={{ fontFamily: 'monospace', fontSize: 11 }}>{m}</span>
                  </Space>
                );
              },
            },
            { title: 'Reqs', dataIndex: 'requests', width: 60, sorter: (a: any, b: any) => a.requests - b.requests },
            {
              title: 'Tokens', width: 140,
              render: (_: any, r: any) => (
                <span style={{ fontSize: 10 }}>
                  <Tag color="blue" style={{ fontSize: 9, margin: 0 }}>{r.prompt_tokens?.toLocaleString()} in</Tag>{' '}
                  <Tag color="purple" style={{ fontSize: 9, margin: 0 }}>{r.completion_tokens?.toLocaleString()} out</Tag>
                </span>
              ),
            },
            {
              title: 'Avg', dataIndex: 'avg_latency_ms', width: 80,
              sorter: (a: any, b: any) => a.avg_latency_ms - b.avg_latency_ms,
              render: (v: number) => <span style={{ color: latencyColor(v), fontWeight: 600, fontSize: 11 }}>{formatLatency(v)}</span>,
            },
            {
              title: 'P95', dataIndex: 'p95_latency', width: 70,
              render: (v: number) => <span style={{ fontSize: 11 }}>{formatLatency(v)}</span>,
            },
            {
              title: 'Cost', dataIndex: 'cost_usd', width: 80,
              render: (v: number) => <span style={{ color: v > 0 ? '#f59e0b' : '#10b981', fontSize: 11 }}>{v > 0 ? `$${v.toFixed(4)}` : 'FREE'}</span>,
            },
            {
              title: 'Err%', dataIndex: 'error_rate', width: 55,
              render: (v: number) => <Tag color={v > 0 ? 'red' : 'green'} style={{ fontSize: 9 }}>{v}%</Tag>,
            },
            {
              title: 'Tok/Req', dataIndex: 'avg_tokens_per_request', width: 70,
              render: (v: number) => <span style={{ fontSize: 11 }}>{Math.round(v)}</span>,
            },
          ]} dataSource={leaderboard} rowKey="model" size="small" pagination={false} />
        ) : (
          <div style={{ textAlign: 'center', padding: 32 }}>
            <RocketOutlined style={{ fontSize: 36, color: '#4b5563', marginBottom: 8 }} />
            <div><Typography.Text type="secondary">Send requests to see model performance data</Typography.Text></div>
          </div>
        )}
      </Card>

      {/* Recent Routing Decisions (from DB) */}
      <Card title={<Space>Recent Routing Decisions <Badge count={decisions.length} showZero style={{ backgroundColor: '#3b82f6' }} /></Space>}
        size="small"
      >
        {decisions.length > 0 ? (
          <Table
            columns={[
              {
                title: 'Time', dataIndex: 'created_at', width: 120,
                render: (d: string) => d ? (
                  <Space direction="vertical" size={0}>
                    <span style={{ fontSize: 11 }}>{dayjs(d).format('HH:mm:ss')}</span>
                    <span style={{ fontSize: 9, color: '#6b7280' }}>{dayjs(d).fromNow()}</span>
                  </Space>
                ) : '—',
              },
              {
                title: 'Provider', dataIndex: 'provider', width: 100,
                render: (p: string) => <Tag color={p === 'ollama' ? 'green' : p === 'claude-cli' ? 'blue' : 'purple'} style={{ fontSize: 10 }}>{p}</Tag>,
              },
              {
                title: 'Model', dataIndex: 'model', width: 180, ellipsis: true,
                render: (m: string) => {
                  const atlas = ATLAS_INFO[m];
                  return (
                    <Space size={4}>
                      {atlas && <span style={{ color: atlas.color }}>{atlas.icon}</span>}
                      <span style={{ fontFamily: 'monospace', fontSize: 11 }}>{m}</span>
                    </Space>
                  );
                },
              },
              {
                title: 'Task', dataIndex: 'task_type', width: 100,
                render: (t: string) => <Tag color="gold" style={{ fontSize: 10 }}>{t || 'general'}</Tag>,
              },
              {
                title: 'Strategy', dataIndex: 'strategy', width: 100,
                render: (s: string) => (
                  <Tag color={s === 'distillation' ? 'magenta' : s === 'best_quality' ? 'blue' : 'green'} style={{ fontSize: 10 }}>
                    {s || '—'}
                  </Tag>
                ),
              },
              {
                title: 'Latency', dataIndex: 'latency_ms', width: 80,
                sorter: (a: any, b: any) => a.latency_ms - b.latency_ms,
                render: (v: number) => <span style={{ color: latencyColor(v), fontSize: 11, fontWeight: 600 }}>{formatLatency(v)}</span>,
              },
              {
                title: 'Cost', dataIndex: 'cost_usd', width: 75,
                render: (v: number) => <span style={{ color: v > 0 ? '#f59e0b' : '#10b981', fontSize: 11 }}>{v > 0 ? `$${v?.toFixed(4)}` : 'FREE'}</span>,
              },
              {
                title: 'Tokens', width: 100,
                render: (_: any, r: any) => (
                  <span style={{ fontSize: 10 }}>
                    <Tag color="blue" style={{ fontSize: 9, margin: 0 }}>{r.prompt_tokens} in</Tag>{' '}
                    <Tag color="purple" style={{ fontSize: 9, margin: 0 }}>{r.completion_tokens} out</Tag>
                  </span>
                ),
              },
            ]}
            dataSource={decisions}
            rowKey="id"
            size="small"
            pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `${t} decisions` }}
            scroll={{ x: 950 }}
          />
        ) : (
          <div style={{ textAlign: 'center', padding: 32 }}>
            <BranchesOutlined style={{ fontSize: 36, color: '#4b5563', marginBottom: 8 }} />
            <div><Typography.Text type="secondary">Routing decisions will appear as requests are processed</Typography.Text></div>
          </div>
        )}
      </Card>
    </div>
  );
}
