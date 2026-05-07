import { useEffect, useRef, useState } from 'react';
import {
  Typography, Table, Tag, Input, Space, Card, Row, Col, Statistic, Badge, Button,
  Tooltip, Segmented, Select, Progress,
} from 'antd';
import type { ColumnsType, TablePaginationConfig } from 'antd/es/table';
import {
  MessageOutlined, UserOutlined, ClockCircleOutlined, ThunderboltOutlined,
  CloudServerOutlined, SearchOutlined, ReloadOutlined, RocketOutlined,
  BranchesOutlined, DatabaseOutlined, ExperimentOutlined, SafetyCertificateOutlined,
  RobotOutlined, WarningOutlined, CheckCircleOutlined,
  CloseCircleOutlined, MedicineBoxOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip as RTooltip } from 'recharts';
import { getConversations, getConversationStats, getDistillationOverview, getSessions, getConversationHealth } from '../lib/api';
import ExportDropdown from '../components/shared/ExportDropdown';
import DateRangeFilter from '../components/shared/DateRangeFilter';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
dayjs.extend(relativeTime);

const { Search } = Input;
const COLORS = ['#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444', '#ec4899', '#06b6d4'];

/** Coloured health score pill — matches ConversationDetail style */
function HealthBadge({ score, flags }: { score: number | null | undefined; flags?: any }) {
  if (score == null) return <span style={{ color: '#4b5563', fontSize: 11 }}>—</span>;
  const pct = Math.round(score * 100);
  const color = pct >= 70 ? '#10b981' : pct >= 40 ? '#f59e0b' : '#ef4444';
  const icon = pct >= 70 ? <CheckCircleOutlined /> : pct >= 40 ? <WarningOutlined /> : <CloseCircleOutlined />;
  return (
    <Tooltip
      title={
        flags ? (
          <div style={{ fontSize: 11 }}>
            {flags.stuck && <div>⚠ Stuck (repeated user messages)</div>}
            {flags.abandoned && <div>⚠ Abandoned (no assistant reply)</div>}
            {flags.low_quality && <div>⚠ Low quality responses</div>}
            {flags.self_healed && <div>✓ Self-healed</div>}
          </div>
        ) : undefined
      }
    >
      <Tag
        icon={icon}
        style={{
          color, borderColor: color, background: `${color}18`,
          fontSize: 10, cursor: flags ? 'help' : 'default', margin: 0,
        }}
      >
        {pct}%
      </Tag>
    </Tooltip>
  );
}

const SOURCE_CONFIG: Record<string, { label: string; color: string; icon: any }> = {
  proxy: { label: 'Proxy', color: 'blue', icon: <ThunderboltOutlined /> },
  openai: { label: 'OpenAI', color: 'blue', icon: <ThunderboltOutlined /> },
  atlas: { label: 'Atlas', color: 'green', icon: <RocketOutlined /> },
  api: { label: 'API', color: 'blue', icon: <ThunderboltOutlined /> },
  onedesk: { label: 'OneDesk', color: 'geekblue', icon: <CloudServerOutlined /> },
  responses: { label: 'Responses', color: 'cyan', icon: <CloudServerOutlined /> },
  'import:paperclip': { label: 'Paperclip', color: 'purple', icon: <DatabaseOutlined /> },
  'import:openai_jsonl': { label: 'JSONL', color: 'orange', icon: <DatabaseOutlined /> },
  openclaw: { label: 'OpenClaw', color: 'cyan', icon: <CloudServerOutlined /> },
  distillation: { label: 'Distillation', color: 'gold', icon: <ExperimentOutlined /> },
  test: { label: 'Test', color: 'default', icon: <BranchesOutlined /> },
};

export default function Conversations() {
  const [data, setData] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<any>(null);
  const [distillation, setDistillation] = useState<any>(null);
  const [sessions, setSessions] = useState<any[]>([]);
  const [healthData, setHealthData] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [search, setSearch] = useState('');
  const [dateRange, setDateRange] = useState<[string | null, string | null]>([null, null]);
  const [sourceFilter, setSourceFilter] = useState<string | undefined>(undefined);
  const [dynamicSources, setDynamicSources] = useState<string[]>([]);
  const [view, setView] = useState<'conversations' | 'sessions'>('conversations');
  const navigate = useNavigate();

  // Track previous filter values to detect changes and reset page
  const prevFilters = useRef({ search, dateRange, sourceFilter });

  const buildQueryParams = (p: number, ps: number) => ({
    limit: ps,
    offset: (p - 1) * ps,
    search: search || undefined,
    date_from: dateRange[0] || undefined,
    date_to: dateRange[1] || undefined,
    source: sourceFilter,
  });

  const load = (p: number, ps: number) => {
    setLoading(true);
    Promise.all([
      getConversations(buildQueryParams(p, ps)),
      getConversationStats().catch(() => null),
      getDistillationOverview().catch(() => null),
      getSessions().catch(() => ({ data: [] })),
      getConversationHealth(200).catch(() => ({ data: [] })),
    ])
      .then(([res, st, dist, sess, health]) => {
        const rows = res.data || [];
        setData(rows);
        setTotal(res.total || 0);
        setStats(st);
        setDistillation(dist);
        setSessions(sess.data || []);
        setHealthData(health.data || []);
        // Collect any source values not already in SOURCE_CONFIG
        const seen = new Set<string>(rows.map((r: any) => r.source).filter(Boolean));
        setDynamicSources(Array.from(seen));
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    const prev = prevFilters.current;
    const filtersChanged =
      prev.search !== search ||
      prev.dateRange[0] !== dateRange[0] ||
      prev.dateRange[1] !== dateRange[1] ||
      prev.sourceFilter !== sourceFilter;
    prevFilters.current = { search, dateRange, sourceFilter };
    // If a filter changed and page is not 1, reset to 1; the resulting page change re-triggers this effect
    if (filtersChanged && page !== 1) {
      setPage(1);
    } else {
      load(page, pageSize);
    }
  }, [page, pageSize, search, dateRange, sourceFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-refresh every 30 seconds when on conversations tab
  useEffect(() => {
    if (view !== 'conversations') return;
    const timer = setInterval(() => load(page, pageSize), 30_000);
    return () => clearInterval(timer);
  }, [page, pageSize, search, dateRange, sourceFilter, view]); // eslint-disable-line react-hooks/exhaustive-deps

  const fetchAllForExport = async () => {
    const res = await getConversations({ ...buildQueryParams(1, 10000) });
    return res.data || [];
  };

  // Source pie data
  const sourcePie = stats?.sources
    ? Object.entries(stats.sources).map(([name, count]) => ({ name, value: count as number }))
    : [];

  // Distillation samples: count both claude and local comparison records
  const distSamples = (distillation?.task_types || []).reduce(
    (s: number, t: any) => s + (t.claude_samples || 0),
    0,
  );

  // Health distribution from health data
  const healthyCount = healthData.filter((h) => h.health_score >= 0.7).length;
  const warningCount = healthData.filter((h) => h.health_score >= 0.4 && h.health_score < 0.7).length;
  const criticalCount = healthData.filter((h) => h.health_score < 0.4).length;
  const selfHealedCount = healthData.filter((h) => h.flags?.self_healed).length;
  const avgHealthScore = healthData.length > 0
    ? healthData.reduce((s, h) => s + h.health_score, 0) / healthData.length
    : null;

  // Build a quick lookup map: conv_id → health record (from the health endpoint)
  // The list endpoint already has health_score on each row, so we use that directly.
  // healthMap is only needed for flags on rows where the list endpoint may not return them.
  const healthMap: Record<string, any> = {};
  healthData.forEach((h) => { healthMap[h.conversation_id] = h; });

  const columns: ColumnsType<any> = [
    {
      title: 'ID', dataIndex: 'id', width: 80,
      render: (id: string) => (
        <a onClick={() => navigate(`/conversations/${id}`)} style={{ fontFamily: 'monospace', fontSize: 11 }}>
          {id.slice(0, 8)}
        </a>
      ),
    },
    {
      title: 'Source', dataIndex: 'source', width: 105,
      render: (s: string) => {
        const cfg = SOURCE_CONFIG[s] || { label: s, color: 'default', icon: <BranchesOutlined /> };
        return <Tag icon={cfg.icon} color={cfg.color} style={{ fontSize: 10 }}>{cfg.label}</Tag>;
      },
    },
    {
      title: 'Preview', dataIndex: 'preview', ellipsis: true,
      render: (p: string | null, row: any) => p ? (
        <Tooltip title={p}>
          <span style={{ fontSize: 11, color: '#d1d5db' }}>{p}</span>
        </Tooltip>
      ) : (
        <span style={{ color: '#4b5563', fontSize: 11 }}>
          {row.user_id ? <><UserOutlined style={{ fontSize: 10 }} /> {row.user_id}</> : '— no preview —'}
        </span>
      ),
    },
    {
      title: 'Turns', dataIndex: 'message_count', width: 70,
      sorter: (a, b) => a.message_count - b.message_count,
      render: (c: number) => (
        <Badge count={c} showZero color={c > 5 ? '#3b82f6' : c > 0 ? '#10b981' : '#6b7280'}
          style={{ fontSize: 10 }} overflowCount={999} />
      ),
    },
    {
      title: 'Model / Task', key: 'model', width: 140,
      render: (_: any, row: any) => row.model ? (
        <Space size={2} direction="vertical" style={{ gap: 2 }}>
          <Tag color="purple" style={{ fontSize: 10, margin: 0 }}><RobotOutlined /> {row.model}</Tag>
          {row.task_type && <Tag color="gold" style={{ fontSize: 9, margin: 0 }}>{row.task_type}</Tag>}
        </Space>
      ) : <span style={{ color: '#4b5563', fontSize: 11 }}>—</span>,
    },
    {
      title: 'Health', key: 'health', width: 100,
      sorter: (a: any, b: any) => {
        const sa = a.health_score ?? -1;
        const sb = b.health_score ?? -1;
        return sa - sb;
      },
      render: (_: any, row: any) => {
        const h = healthMap[row.id];
        return (
          <Space size={3} direction="vertical" style={{ gap: 2 }}>
            <HealthBadge score={row.health_score ?? h?.health_score} flags={row.health_flags ?? h?.flags} />
            {(row.health_flags ?? h?.flags)?.stuck && (
              <Tag color="orange" style={{ fontSize: 9, margin: 0 }}>stuck</Tag>
            )}
            {(row.health_flags ?? h?.flags)?.self_healed && (
              <Tag color="purple" style={{ fontSize: 9, margin: 0 }}>healed</Tag>
            )}
          </Space>
        );
      },
    },
    {
      title: 'Created', dataIndex: 'created_at', width: 150,
      sorter: (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      defaultSortOrder: 'descend',
      render: (d: string) => d ? (
        <Space direction="vertical" size={0}>
          <span style={{ fontSize: 11 }}>{dayjs(d).format('MMM D, HH:mm')}</span>
          <span style={{ fontSize: 10, color: '#6b7280' }}>{dayjs(d).fromNow()}</span>
        </Space>
      ) : '—',
    },
  ];

  const sessionColumns: ColumnsType<any> = [
    {
      title: 'Session ID', dataIndex: 'id', width: 100,
      render: (id: string) => (
        <Tooltip title={id}>
          <span style={{ fontFamily: 'monospace', fontSize: 11 }}>{id.slice(0, 8)}</span>
        </Tooltip>
      ),
    },
    { title: 'User', dataIndex: 'user_id', width: 120, render: (u: string | null) => u || <span style={{ color: '#4b5563' }}>—</span> },
    {
      title: 'Messages', dataIndex: 'message_count', width: 80,
      render: (c: number) => <Badge count={c} showZero color="#3b82f6" style={{ fontSize: 10 }} />,
    },
    {
      title: 'Age', dataIndex: 'age_seconds', width: 100,
      sorter: (a, b) => a.age_seconds - b.age_seconds,
      render: (s: number) => s < 60 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`,
    },
    {
      title: 'Last Active', dataIndex: 'last_activity', width: 140,
      sorter: (a, b) => (a.last_activity || 0) - (b.last_activity || 0),
      render: (t: number) => t ? dayjs.unix(t).fromNow() : '—',
    },
  ];

  const pagination: TablePaginationConfig = {
    current: page, pageSize, total,
    showSizeChanger: true, showTotal: (t) => `${t} total`,
    pageSizeOptions: [10, 25, 50, 100],
    onChange: (p, ps) => { setPage(p); setPageSize(ps); },
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>Conversations & Sessions</Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            All chat sessions, distillation data, and active session memory
          </Typography.Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => load(page, pageSize)} size="small">Refresh</Button>
          <ExportDropdown data={data} filename="conversations" fetchAll={fetchAllForExport} />
        </Space>
      </div>

      {/* KPI Cards — row 1: conversation stats */}
      <Row gutter={[10, 10]} style={{ marginBottom: 8 }}>
        {[
          { title: 'Conversations', value: stats?.total_conversations ?? total, icon: <MessageOutlined />, color: '#3b82f6' },
          { title: 'Messages', value: stats?.total_messages ?? 0, icon: <ThunderboltOutlined />, color: '#8b5cf6' },
          { title: 'Identified Users', value: stats?.identified_users ?? 0, icon: <UserOutlined />, color: '#10b981' },
          { title: 'Avg Turns/Conv', value: stats ? Math.ceil((stats.avg_messages_per_conversation ?? 0) / 2) : 0, icon: <BranchesOutlined />, color: '#f59e0b' },
          { title: 'Routing Decisions', value: stats?.total_routing_decisions ?? 0, icon: <RocketOutlined />, color: '#ec4899' },
          { title: 'Last 24h', value: stats?.recent_24h ?? 0, icon: <ClockCircleOutlined />, color: '#06b6d4' },
          { title: 'Claude Samples', value: distSamples, icon: <ExperimentOutlined />, color: '#f59e0b' },
          { title: 'Active Sessions', value: sessions.length, icon: <SafetyCertificateOutlined />, color: '#10b981' },
        ].map((s) => (
          <Col key={s.title} xs={12} sm={6} md={3}>
            <Card size="small" hoverable>
              <Statistic title={s.title} value={s.value} prefix={s.icon} valueStyle={{ color: s.color, fontSize: 18 }} />
            </Card>
          </Col>
        ))}
      </Row>

      {/* KPI Cards — row 2: health distribution */}
      {healthData.length > 0 && (
        <Row gutter={[10, 10]} style={{ marginBottom: 12 }}>
          <Col xs={12} sm={6} md={3}>
            <Card size="small" hoverable style={{ borderColor: '#10b98133' }}>
              <Statistic
                title={<span style={{ fontSize: 11 }}>Healthy</span>}
                value={healthyCount}
                prefix={<CheckCircleOutlined />}
                valueStyle={{ color: '#10b981', fontSize: 18 }}
                suffix={<span style={{ fontSize: 10, color: '#4b5563' }}>/ {healthData.length}</span>}
              />
            </Card>
          </Col>
          <Col xs={12} sm={6} md={3}>
            <Card size="small" hoverable style={{ borderColor: '#f59e0b33' }}>
              <Statistic
                title={<span style={{ fontSize: 11 }}>Warning</span>}
                value={warningCount}
                prefix={<WarningOutlined />}
                valueStyle={{ color: '#f59e0b', fontSize: 18 }}
                suffix={<span style={{ fontSize: 10, color: '#4b5563' }}>/ {healthData.length}</span>}
              />
            </Card>
          </Col>
          <Col xs={12} sm={6} md={3}>
            <Card size="small" hoverable style={{ borderColor: '#ef444433' }}>
              <Statistic
                title={<span style={{ fontSize: 11 }}>Critical</span>}
                value={criticalCount}
                prefix={<CloseCircleOutlined />}
                valueStyle={{ color: '#ef4444', fontSize: 18 }}
                suffix={<span style={{ fontSize: 10, color: '#4b5563' }}>/ {healthData.length}</span>}
              />
            </Card>
          </Col>
          <Col xs={12} sm={6} md={3}>
            <Card size="small" hoverable style={{ borderColor: '#a855f733' }}>
              <Statistic
                title={<span style={{ fontSize: 11 }}>Self-Healed</span>}
                value={selfHealedCount}
                prefix={<MedicineBoxOutlined />}
                valueStyle={{ color: '#a855f7', fontSize: 18 }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} md={8}>
            <Card size="small" title={<span style={{ fontSize: 11 }}>Avg Health Score</span>}>
              {avgHealthScore != null ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <Progress
                    type="circle"
                    percent={Math.round(avgHealthScore * 100)}
                    size={48}
                    strokeColor={avgHealthScore >= 0.7 ? '#10b981' : avgHealthScore >= 0.4 ? '#f59e0b' : '#ef4444'}
                    format={(p) => <span style={{ fontSize: 11, color: '#e5e7eb' }}>{p}%</span>}
                  />
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                      <span style={{ fontSize: 10, color: '#10b981' }}>Healthy</span>
                      <span style={{ fontSize: 10, color: '#10b981' }}>{healthyCount}</span>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                      <span style={{ fontSize: 10, color: '#f59e0b' }}>Warning</span>
                      <span style={{ fontSize: 10, color: '#f59e0b' }}>{warningCount}</span>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ fontSize: 10, color: '#ef4444' }}>Critical</span>
                      <span style={{ fontSize: 10, color: '#ef4444' }}>{criticalCount}</span>
                    </div>
                  </div>
                </div>
              ) : (
                <span style={{ fontSize: 11, color: '#4b5563' }}>No health data yet</span>
              )}
            </Card>
          </Col>
        </Row>
      )}

      {/* Source Distribution + View Toggle */}
      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col xs={24} md={8}>
          <Card title="Source Distribution" size="small">
            {sourcePie.length > 0 ? (
              <>
                <ResponsiveContainer width="100%" height={160}>
                  <PieChart>
                    <Pie data={sourcePie} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={55} innerRadius={25}>
                      {sourcePie.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                    </Pie>
                    <RTooltip />
                  </PieChart>
                </ResponsiveContainer>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {sourcePie.map((d, i) => {
                    const cfg = SOURCE_CONFIG[d.name];
                    return <Tag key={d.name} color={cfg?.color || COLORS[i % COLORS.length]} style={{ fontSize: 10 }}>{cfg?.label || d.name}: {d.value}</Tag>;
                  })}
                </div>
              </>
            ) : (
              <div style={{ height: 180, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column' }}>
                <MessageOutlined style={{ fontSize: 28, color: '#4b5563', marginBottom: 8 }} />
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>No source data yet</Typography.Text>
              </div>
            )}
          </Card>
        </Col>
        <Col xs={24} md={16}>
          <Card size="small" title={
            <Space>
              <span>Data View</span>
              <Segmented options={[
                { label: 'Conversations', value: 'conversations', icon: <MessageOutlined /> },
                { label: 'Active Sessions', value: 'sessions', icon: <SafetyCertificateOutlined /> },
              ]} value={view} onChange={(v) => setView(v as any)} size="small" />
            </Space>
          }>
            {view === 'sessions' ? (
              sessions.length > 0 ? (
                <Table
                  columns={sessionColumns}
                  dataSource={sessions}
                  rowKey="id"
                  size="small"
                  pagination={{ pageSize: 10, showSizeChanger: false, showTotal: (t) => `${t} sessions` }}
                />
              ) : (
                <div style={{ textAlign: 'center', padding: 24 }}>
                  <SafetyCertificateOutlined style={{ fontSize: 28, color: '#4b5563', marginBottom: 8 }} />
                  <div><Typography.Text type="secondary" style={{ fontSize: 11 }}>No active sessions — sessions are created when using /atlas endpoint</Typography.Text></div>
                </div>
              )
            ) : (
              /* Recent conversations preview — replaces the redundant source breakdown cards */
              <div>
                <Typography.Text type="secondary" style={{ fontSize: 11, display: 'block', marginBottom: 8 }}>Recent activity</Typography.Text>
                {data.slice(0, 5).length > 0 ? (
                  data.slice(0, 5).map((c) => {
                    const cfg = SOURCE_CONFIG[c.source] || { label: c.source, color: 'default', icon: <BranchesOutlined /> };
                    return (
                      <div
                        key={c.id}
                        onClick={() => navigate(`/conversations/${c.id}`)}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 8, padding: '5px 4px',
                          cursor: 'pointer', borderRadius: 4, marginBottom: 4,
                          borderBottom: '1px solid rgba(255,255,255,0.05)',
                        }}
                      >
                        <Tag icon={cfg.icon} color={cfg.color} style={{ fontSize: 10, margin: 0, flexShrink: 0 }}>{cfg.label}</Tag>
                        <span style={{ fontSize: 11, color: '#9ca3af', fontFamily: 'monospace', flexShrink: 0 }}>{c.id.slice(0, 8)}</span>
                        <span style={{ fontSize: 11, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: c.preview ? '#d1d5db' : '#4b5563' }}>
                          {c.preview || c.user_id || 'anonymous'}
                        </span>
                        {c.model && (
                          <Tag color="purple" style={{ fontSize: 9, margin: 0, flexShrink: 0 }}>
                            <RobotOutlined /> {c.model}
                          </Tag>
                        )}
                        <span style={{ fontSize: 10, color: '#6b7280', flexShrink: 0 }}>{dayjs(c.created_at).fromNow()}</span>
                      </div>
                    );
                  })
                ) : (
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>No conversations yet</Typography.Text>
                )}
              </div>
            )}
          </Card>
        </Col>
      </Row>

      {/* Filters */}
      <Space style={{ marginBottom: 10 }} wrap>
        <Search
          placeholder="Search by user ID..."
          allowClear
          prefix={<SearchOutlined />}
          onSearch={(v) => setSearch(v)}
          onChange={(e) => { if (!e.target.value) setSearch(''); }}
          style={{ width: 260 }}
          size="small"
        />
        <Select
          placeholder="All sources"
          allowClear
          size="small"
          style={{ width: 150 }}
          value={sourceFilter}
          onChange={setSourceFilter}
          options={Array.from(new Set([
            ...Object.keys(SOURCE_CONFIG),
            ...dynamicSources,
          ])).map((k) => {
            const cfg = SOURCE_CONFIG[k];
            return { value: k, label: cfg ? cfg.label : k };
          })}
        />
        <DateRangeFilter value={dateRange} onChange={setDateRange} />
      </Space>

      {/* Main Table */}
      <Card size="small" styles={{ body: { padding: 0 } }}>
        <Table
          columns={columns}
          dataSource={data}
          rowKey="id"
          loading={loading}
          pagination={pagination}
          size="small"
          locale={{
            emptyText: (
              <div style={{ padding: '32px 0', textAlign: 'center' }}>
                <MessageOutlined style={{ fontSize: 36, color: '#4b5563', marginBottom: 10 }} />
                <div><Typography.Text type="secondary" style={{ fontSize: 13 }}>No conversations yet</Typography.Text></div>
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  Send requests to any <code>atlas-*</code> model via the API or Playground to start recording
                </Typography.Text>
              </div>
            ),
          }}
          onRow={(record) => ({
            onClick: () => navigate(`/conversations/${record.id}`),
            style: { cursor: 'pointer' },
          })}
        />
      </Card>
    </div>
  );
}
