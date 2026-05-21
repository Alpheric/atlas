import { useEffect, useState } from 'react';
import { Typography, Card, Button, Tag, Table, Row, Col, Badge, App, Tooltip } from 'antd';
import { ReloadOutlined, CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons';
import { getProviders, refreshProviders, getModels, getProviderCircuit } from '../lib/api';
import PageSkeleton from '../components/shared/PageSkeleton';

const circuitColor: Record<string, string> = { closed: 'green', half_open: 'gold', open: 'red' };

export default function Providers() {
  const [providers, setProviders] = useState<any[]>([]);
  const [models, setModels] = useState<any[]>([]);
  const [circuit, setCircuit] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const { message } = App.useApp();

  const load = () => {
    setLoading(true);
    Promise.all([getProviders(), getModels(), getProviderCircuit().catch(() => ({ data: [] }))])
      .then(([p, m, c]) => {
        setProviders(p.data || []);
        setModels(m.data || []);
        const cmap: Record<string, any> = {};
        for (const b of (c.data || [])) cmap[b.provider] = b;
        setCircuit(cmap);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      const result = await refreshProviders();
      setProviders(result.providers || []);
      message.success('Provider health refreshed');
    } catch {}
    setRefreshing(false);
  };

  if (loading) return <PageSkeleton />;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Providers</Typography.Title>
        <Button icon={<ReloadOutlined />} loading={refreshing} onClick={handleRefresh}>Refresh Health</Button>
      </div>

      <Row gutter={[16, 16]}>
        {providers.map((p) => (
          <Col key={p.name} xs={24} sm={12} md={12} lg={6}>
            <Badge.Ribbon text={p.healthy ? 'Healthy' : 'Down'} color={p.healthy ? 'green' : 'red'}>
              <Card size="small">
                <Typography.Title level={5} style={{ textTransform: 'capitalize', marginTop: 0 }}>
                  {p.healthy ? <CheckCircleOutlined style={{ color: '#10b981', marginRight: 8 }} /> : <CloseCircleOutlined style={{ color: '#ef4444', marginRight: 8 }} />}
                  {p.name}
                </Typography.Title>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>{p.models?.length ?? 0} models</Typography.Text>
                {circuit[p.name] && (
                  <div style={{ marginTop: 6 }}>
                    <Tooltip title={`Circuit breaker: ${circuit[p.name].state}${circuit[p.name].consecutive_failures ? ` · ${circuit[p.name].consecutive_failures} consecutive failures` : ''}`}>
                      <Tag color={circuitColor[circuit[p.name].state] || 'default'} style={{ fontSize: 11 }}>
                        breaker: {circuit[p.name].state}
                      </Tag>
                    </Tooltip>
                  </div>
                )}
                <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {(p.models || []).map((m: string) => <Tag key={m} color="blue" style={{ fontSize: 11 }}>{m}</Tag>)}
                </div>
              </Card>
            </Badge.Ribbon>
          </Col>
        ))}
      </Row>

      <Card title="All Available Models" size="small" style={{ marginTop: 16 }}>
        <Table
          columns={[
            { title: 'Model ID', dataIndex: 'id', render: (m: string) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{m}</span> },
            { title: 'Provider', dataIndex: 'owned_by', render: (p: string) => <Tag color="purple">{p}</Tag> },
            { title: 'Context Window', dataIndex: 'context_window', render: (c: number) => c?.toLocaleString(), sorter: (a: any, b: any) => a.context_window - b.context_window },
          ]}
          dataSource={models}
          rowKey="id"
          size="small"
          pagination={false}
        />
      </Card>
    </div>
  );
}
