import { useQuery } from '@tanstack/react-query';
import {
  Typography, Card, Row, Col, Statistic, Table, Tag, Progress, Space,
  Badge, Button, Alert, Spin, Empty,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  MedicineBoxOutlined, HeartOutlined, CheckCircleOutlined, CloseCircleOutlined,
  WarningOutlined, ReloadOutlined, ThunderboltOutlined, RobotOutlined,
  FireOutlined, ExperimentOutlined, BulbOutlined, AlertOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import {
  PieChart, Pie, Cell, AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip as RTooltip, ResponsiveContainer, Legend,
} from 'recharts';
import { getConversationHealth, getOverview, getDailyStats } from '../lib/api';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
dayjs.extend(relativeTime);

const HEALTH_COLORS = {
  healthy: '#10b981',
  warning: '#f59e0b',
  critical: '#ef4444',
  healed: '#a855f7',
};

function HealthRing({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color = pct >= 70 ? HEALTH_COLORS.healthy : pct >= 40 ? HEALTH_COLORS.warning : HEALTH_COLORS.critical;
  return (
    <Progress
      type="circle"
      percent={pct}
      size={40}
      strokeColor={color}
      format={(p) => <span style={{ fontSize: 10, color: '#e5e7eb' }}>{p}%</span>}
    />
  );
}

function FlagTags({ flags }: { flags: any }) {
  if (!flags) return null;
  return (
    <Space size={2} wrap>
      {flags.stuck && <Tag color="orange" style={{ fontSize: 9, margin: 0 }}>stuck</Tag>}
      {flags.abandoned && <Tag color="red" style={{ fontSize: 9, margin: 0 }}>abandoned</Tag>}
      {flags.low_quality && <Tag color="volcano" style={{ fontSize: 9, margin: 0 }}>low quality</Tag>}
      {flags.self_healed && <Tag color="purple" style={{ fontSize: 9, margin: 0 }}>healed</Tag>}
    </Space>
  );
}

export default function Healing() {
  const navigate = useNavigate();

  const healthQuery = useQuery<any[]>({
    queryKey: ['conversationHealth', 200],
    queryFn: async () => (await getConversationHealth(200)).data ?? [],
  });
  const overviewQuery = useQuery<any>({
    queryKey: ['overview'],
    queryFn: () => getOverview().catch(() => null),
  });
  const dailyQuery = useQuery<any[]>({
    queryKey: ['dailyStats'],
    queryFn: () => getDailyStats().catch(() => []),
  });

  const healthRows = healthQuery.data ?? [];
  const overview = overviewQuery.data ?? null;
  const dailyStats = dailyQuery.data ?? [];
  const loading = healthQuery.isFetching;
  const load = () => {
    healthQuery.refetch();
    overviewQuery.refetch();
    dailyQuery.refetch();
  };

  // --- Computed stats ---
  const total = healthRows.length;
  const healthy = healthRows.filter((h) => h.health_score >= 0.7);
  const warning = healthRows.filter((h) => h.health_score >= 0.4 && h.health_score < 0.7);
  const critical = healthRows.filter((h) => h.health_score < 0.4);
  const healed = healthRows.filter((h) => h.flags?.self_healed);
  const stuck = healthRows.filter((h) => h.flags?.stuck);
  const abandoned = healthRows.filter((h) => h.flags?.abandoned);
  const lowQuality = healthRows.filter((h) => h.flags?.low_quality);

  const avgScore = total > 0
    ? healthRows.reduce((s, h) => s + h.health_score, 0) / total
    : 0;
  const avgQuality = healthRows.filter((h) => h.avg_quality != null).length > 0
    ? healthRows.filter((h) => h.avg_quality != null)
        .reduce((s, h) => s + h.avg_quality, 0) /
      healthRows.filter((h) => h.avg_quality != null).length
    : null;

  const pieDist = [
    { name: 'Healthy (≥70%)', value: healthy.length, color: HEALTH_COLORS.healthy },
    { name: 'Warning (40–69%)', value: warning.length, color: HEALTH_COLORS.warning },
    { name: 'Critical (<40%)', value: critical.length, color: HEALTH_COLORS.critical },
  ].filter((d) => d.value > 0);

  const flagPie = [
    { name: 'Stuck', value: stuck.length, color: '#f97316' },
    { name: 'Abandoned', value: abandoned.length, color: '#ef4444' },
    { name: 'Low Quality', value: lowQuality.length, color: '#eab308' },
    { name: 'Self-Healed', value: healed.length, color: HEALTH_COLORS.healed },
  ].filter((d) => d.value > 0);

  // From overview db_stats
  const dbStats = overview?.db_stats;
  const selfHealedTotal = dbStats?.self_healed_count ?? 0;
  const totalRequests = dbStats?.total_requests ?? 0;
  const healRate = totalRequests > 0 ? ((selfHealedTotal / totalRequests) * 100).toFixed(1) : '0.0';

  // Columns for the unhealthy conversations table
  const columns: ColumnsType<any> = [
    {
      title: 'Conv ID', dataIndex: 'conversation_id', width: 100,
      render: (id: string) => (
        <a
          onClick={() => navigate(`/conversations/${id}`)}
          style={{ fontFamily: 'monospace', fontSize: 11 }}
        >
          {id.slice(0, 8)}
        </a>
      ),
    },
    {
      title: 'Health', dataIndex: 'health_score', width: 80,
      sorter: (a, b) => a.health_score - b.health_score,
      defaultSortOrder: 'ascend',
      render: (s: number) => <HealthRing score={s} />,
    },
    {
      title: 'Avg Quality', dataIndex: 'avg_quality', width: 90,
      sorter: (a, b) => (a.avg_quality ?? 0) - (b.avg_quality ?? 0),
      render: (q: number | null) => {
        if (q == null) return <span style={{ color: '#4b5563', fontSize: 11 }}>—</span>;
        const pct = Math.round(q * 100);
        const color = pct >= 70 ? '#10b981' : pct >= 40 ? '#f59e0b' : '#ef4444';
        return (
          <Tag style={{ color, borderColor: color, background: `${color}18`, fontSize: 10, margin: 0 }}>
            {pct}%
          </Tag>
        );
      },
    },
    {
      title: 'Turns', dataIndex: 'turn_count', width: 65,
      sorter: (a, b) => a.turn_count - b.turn_count,
      render: (c: number) => (
        <Badge count={c} showZero color="#3b82f6" style={{ fontSize: 10 }} overflowCount={99} />
      ),
    },
    {
      title: 'Flags', dataIndex: 'flags', width: 180,
      render: (flags: any) => <FlagTags flags={flags} />,
    },
    {
      title: 'Checked', dataIndex: 'checked_at', width: 120,
      sorter: (a, b) => new Date(a.checked_at || 0).getTime() - new Date(b.checked_at || 0).getTime(),
      render: (d: string | null) => d
        ? <span style={{ fontSize: 10, color: '#6b7280' }}>{dayjs(d).fromNow()}</span>
        : '—',
    },
    {
      title: '', key: 'action', width: 70,
      render: (_: any, row: any) => (
        <Button
          size="small"
          type="link"
          onClick={() => navigate(`/conversations/${row.conversation_id}`)}
          style={{ fontSize: 11, padding: 0 }}
        >
          View →
        </Button>
      ),
    },
  ];

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>
            <MedicineBoxOutlined style={{ color: '#a855f7', marginRight: 8 }} />
            Self-Heal Monitor
          </Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Conversation health, quality scores, and auto-improvement activity
          </Typography.Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading} size="small">
          Refresh
        </Button>
      </div>

      <Spin spinning={loading && healthRows.length === 0}>
        {/* Row 1 — KPI cards */}
        <Row gutter={[10, 10]} style={{ marginBottom: 12 }}>
          {[
            {
              title: 'Avg Health Score',
              value: `${Math.round(avgScore * 100)}%`,
              icon: <HeartOutlined />,
              color: avgScore >= 0.7 ? '#10b981' : avgScore >= 0.4 ? '#f59e0b' : '#ef4444',
              suffix: '',
            },
            {
              title: 'Healthy Conv.',
              value: healthy.length,
              icon: <CheckCircleOutlined />,
              color: '#10b981',
              suffix: `/ ${total}`,
            },
            {
              title: 'Warning Conv.',
              value: warning.length,
              icon: <WarningOutlined />,
              color: '#f59e0b',
              suffix: `/ ${total}`,
            },
            {
              title: 'Critical Conv.',
              value: critical.length,
              icon: <CloseCircleOutlined />,
              color: '#ef4444',
              suffix: `/ ${total}`,
            },
            {
              title: 'Self-Healed (total)',
              value: selfHealedTotal,
              icon: <MedicineBoxOutlined />,
              color: '#a855f7',
              suffix: '',
            },
            {
              title: 'Heal Rate',
              value: `${healRate}%`,
              icon: <ThunderboltOutlined />,
              color: '#3b82f6',
              suffix: 'of requests',
            },
            {
              title: 'Stuck',
              value: stuck.length,
              icon: <AlertOutlined />,
              color: '#f97316',
              suffix: '',
            },
            {
              title: 'Avg Quality',
              value: avgQuality != null ? `${Math.round(avgQuality * 100)}%` : '—',
              icon: <ExperimentOutlined />,
              color: avgQuality != null && avgQuality >= 0.7 ? '#10b981' : '#f59e0b',
              suffix: '',
            },
          ].map((s) => (
            <Col key={s.title} xs={12} sm={6} md={3}>
              <Card size="small" hoverable>
                <Statistic
                  title={<span style={{ fontSize: 11 }}>{s.title}</span>}
                  value={s.value}
                  prefix={s.icon}
                  suffix={s.suffix ? <span style={{ fontSize: 10, color: '#6b7280' }}>{s.suffix}</span> : undefined}
                  valueStyle={{ color: s.color, fontSize: 17 }}
                />
              </Card>
            </Col>
          ))}
        </Row>

        {/* Row 2 — Charts */}
        <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
          {/* Health distribution donut */}
          <Col xs={24} md={7}>
            <Card title="Health Distribution" size="small">
              {pieDist.length > 0 ? (
                <>
                  <ResponsiveContainer width="100%" height={180}>
                    <PieChart>
                      <Pie
                        data={pieDist}
                        dataKey="value"
                        nameKey="name"
                        cx="50%"
                        cy="50%"
                        outerRadius={65}
                        innerRadius={30}
                        label={({ percent }) =>
                          percent > 0.05 ? `${Math.round(percent * 100)}%` : ''
                        }
                        labelLine={false}
                      >
                        {pieDist.map((d) => (
                          <Cell key={d.name} fill={d.color} />
                        ))}
                      </Pie>
                      <RTooltip
                        contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 6, fontSize: 11 }}
                        formatter={(val, name) => [`${val} conversations`, name]}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {pieDist.map((d) => (
                      <Tag key={d.name} style={{ color: d.color, borderColor: d.color, background: `${d.color}18`, fontSize: 9, margin: 0 }}>
                        {d.name}: {d.value}
                      </Tag>
                    ))}
                  </div>
                </>
              ) : (
                <Empty description="No health data yet" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ height: 180 }} />
              )}
            </Card>
          </Col>

          {/* Flag breakdown donut */}
          <Col xs={24} md={7}>
            <Card title="Issue Flags Breakdown" size="small">
              {flagPie.length > 0 ? (
                <>
                  <ResponsiveContainer width="100%" height={180}>
                    <PieChart>
                      <Pie
                        data={flagPie}
                        dataKey="value"
                        nameKey="name"
                        cx="50%"
                        cy="50%"
                        outerRadius={65}
                        innerRadius={30}
                        label={({ percent }) =>
                          percent > 0.07 ? `${Math.round(percent * 100)}%` : ''
                        }
                        labelLine={false}
                      >
                        {flagPie.map((d) => (
                          <Cell key={d.name} fill={d.color} />
                        ))}
                      </Pie>
                      <RTooltip
                        contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 6, fontSize: 11 }}
                        formatter={(val, name) => [`${val} conversations`, name]}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {flagPie.map((d) => (
                      <Tag key={d.name} style={{ color: d.color, borderColor: d.color, background: `${d.color}18`, fontSize: 9, margin: 0 }}>
                        {d.name}: {d.value}
                      </Tag>
                    ))}
                  </div>
                </>
              ) : (
                <Empty description="No flags recorded" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ height: 180 }} />
              )}
            </Card>
          </Col>

          {/* Daily requests + self-healed trend */}
          <Col xs={24} md={10}>
            <Card title="7-Day Quality Trend" size="small">
              {dailyStats.length > 0 ? (
                <ResponsiveContainer width="100%" height={200}>
                  <AreaChart data={dailyStats} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
                    <defs>
                      <linearGradient id="gReqs" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                    <XAxis
                      dataKey="day"
                      tickFormatter={(d) => dayjs(d).format('M/D')}
                      tick={{ fontSize: 10, fill: '#6b7280' }}
                    />
                    <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} />
                    <RTooltip
                      contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 6, fontSize: 11 }}
                      labelFormatter={(d) => dayjs(d).format('MMM D')}
                    />
                    <Legend wrapperStyle={{ fontSize: 10 }} />
                    <Area
                      type="monotone"
                      dataKey="requests"
                      name="Requests"
                      stroke="#3b82f6"
                      fill="url(#gReqs)"
                      strokeWidth={2}
                      dot={false}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <Empty description="No daily stats yet" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ height: 200 }} />
              )}
            </Card>
          </Col>
        </Row>

        {/* Self-heal pipeline status */}
        <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
          <Col xs={24} md={8}>
            <Card title={<Space><BulbOutlined style={{ color: '#a855f7' }} />Self-Heal Pipeline</Space>} size="small">
              <Space direction="vertical" style={{ width: '100%' }} size={10}>
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span style={{ fontSize: 11, color: '#9ca3af' }}>Quality Scorer</span>
                    <Tag color="green" style={{ fontSize: 9 }}>active</Tag>
                  </div>
                  <Typography.Text type="secondary" style={{ fontSize: 10 }}>
                    Scores every non-streaming response on 5 heuristics.
                    Trigger threshold: &lt;40% quality.
                  </Typography.Text>
                </div>
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span style={{ fontSize: 11, color: '#9ca3af' }}>Self-Critique Gate</span>
                    <Tag color="green" style={{ fontSize: 9 }}>active</Tag>
                  </div>
                  <Typography.Text type="secondary" style={{ fontSize: 10 }}>
                    Low-score responses are re-generated via Claude CLI.
                    Original replaced transparently.
                  </Typography.Text>
                </div>
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span style={{ fontSize: 11, color: '#9ca3af' }}>Feedback Regeneration</span>
                    <Tag color="green" style={{ fontSize: 9 }}>active</Tag>
                  </div>
                  <Typography.Text type="secondary" style={{ fontSize: 10 }}>
                    Thumbs-down on any message triggers background re-generation.
                    Manual "Regenerate" button available per message.
                  </Typography.Text>
                </div>
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span style={{ fontSize: 11, color: '#9ca3af' }}>Health Monitor</span>
                    <Tag color="green" style={{ fontSize: 9 }}>running</Tag>
                  </div>
                  <Typography.Text type="secondary" style={{ fontSize: 10 }}>
                    Scans last-24h conversations every 5 min.
                    Scores: avg_quality (40%) + stuck (30%) + abandoned (20%) + length (10%).
                  </Typography.Text>
                </div>
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span style={{ fontSize: 11, color: '#9ca3af' }}>Training Quality Gate</span>
                    <Tag color="green" style={{ fontSize: 9 }}>active</Tag>
                  </div>
                  <Typography.Text type="secondary" style={{ fontSize: 10 }}>
                    Distillation only uses samples above quality threshold.
                    Low-quality pairs are excluded from fine-tuning.
                  </Typography.Text>
                </div>
              </Space>
            </Card>
          </Col>

          {/* Top unhealthy conversations */}
          <Col xs={24} md={16}>
            <Card
              title={
                <Space>
                  <FireOutlined style={{ color: '#ef4444' }} />
                  Unhealthy Conversations
                  <Tag color="red" style={{ fontSize: 10 }}>{critical.length} critical</Tag>
                  <Tag color="orange" style={{ fontSize: 10 }}>{warning.length} warning</Tag>
                </Space>
              }
              size="small"
            >
              {healthRows.filter((h) => h.health_score < 0.7).length > 0 ? (
                <Table
                  columns={columns}
                  dataSource={healthRows
                    .filter((h) => h.health_score < 0.7)
                    .slice(0, 10)}
                  rowKey="conversation_id"
                  size="small"
                  pagination={false}
                  onRow={(record) => ({
                    onClick: () => navigate(`/conversations/${record.conversation_id}`),
                    style: { cursor: 'pointer' },
                  })}
                  rowClassName={(record) =>
                    record.health_score < 0.4 ? 'ant-table-row-critical' : ''
                  }
                />
              ) : (
                <Empty
                  description={
                    <span style={{ fontSize: 12, color: '#10b981' }}>
                      All conversations are healthy ✓
                    </span>
                  }
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                />
              )}
            </Card>
          </Col>
        </Row>

        {/* Full health table */}
        <Card
          title={
            <Space>
              <RobotOutlined />
              All Monitored Conversations
              <Tag color="default" style={{ fontSize: 10 }}>{total} total</Tag>
            </Space>
          }
          size="small"
        >
          {healthRows.length > 0 ? (
            <Table
              columns={columns}
              dataSource={healthRows}
              rowKey="conversation_id"
              size="small"
              pagination={{ pageSize: 20, showTotal: (t) => `${t} conversations`, showSizeChanger: false }}
              onRow={(record) => ({
                onClick: () => navigate(`/conversations/${record.conversation_id}`),
                style: { cursor: 'pointer' },
              })}
            />
          ) : (
            <Alert
              type="info"
              showIcon
              message="Health monitor is running"
              description="Conversation health scores are computed every 5 minutes. Check back after some conversations have been processed."
              style={{ fontSize: 12 }}
            />
          )}
        </Card>
      </Spin>
    </div>
  );
}
