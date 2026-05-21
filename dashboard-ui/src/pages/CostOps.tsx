import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import {
  Typography, Card, Table, Tag, Progress, Row, Col, Statistic, Input, Button, Space, Alert, App,
} from 'antd';
import { DollarOutlined, WarningOutlined, BranchesOutlined } from '@ant-design/icons';
import {
  getCostByWorkspace, getCostByKey, getAnomalies, runRoutingReplay,
} from '../lib/api';
import PageSkeleton from '../components/shared/PageSkeleton';
import dayjs from 'dayjs';

const sevColor: Record<string, string> = { critical: 'error', warning: 'warning' };

export default function CostOps() {
  const { message } = App.useApp();
  const [candidate, setCandidate] = useState('{"*": "qwen2.5-coder:7b"}');
  const [replayResult, setReplayResult] = useState<any>(null);

  const { data: ws, isLoading: wsLoading } = useQuery<any>({
    queryKey: ['costByWorkspace'], queryFn: () => getCostByWorkspace(30),
  });
  const { data: keys } = useQuery<any>({
    queryKey: ['costByKey'], queryFn: () => getCostByKey(30, 50),
  });
  const { data: anomalies } = useQuery<any>({
    queryKey: ['anomalies'], queryFn: () => getAnomalies(50), refetchInterval: 30_000,
  });

  const replayMut = useMutation({
    mutationFn: (cand: Record<string, string>) => runRoutingReplay(cand, 30),
    onSuccess: (d) => setReplayResult(d),
    onError: () => message.error('Invalid candidate or replay failed'),
  });

  const runReplay = () => {
    try {
      const parsed = JSON.parse(candidate);
      replayMut.mutate(parsed);
    } catch {
      message.error('Candidate must be valid JSON, e.g. {"code": "qwen2.5-coder:7b"}');
    }
  };

  if (wsLoading) return <PageSkeleton />;

  const wsData = ws?.data ?? [];
  const keyData = keys?.data ?? [];
  const anomData = anomalies?.data ?? [];

  return (
    <div>
      <Typography.Title level={4} style={{ margin: '0 0 16px' }}>Cost &amp; Ops</Typography.Title>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={8}><Card size="small"><Statistic title="30-day spend (workspaces)" prefix={<DollarOutlined />} value={ws?.total_cost_usd ?? 0} precision={2} /></Card></Col>
        <Col xs={24} sm={8}><Card size="small"><Statistic title="Tracked API keys" value={keyData.length} /></Card></Col>
        <Col xs={24} sm={8}><Card size="small"><Statistic title="Open anomalies" valueStyle={{ color: anomData.length ? '#f59e0b' : undefined }} prefix={<WarningOutlined />} value={anomData.length} /></Card></Col>
      </Row>

      {/* Anomalies */}
      <Card size="small" title={<Space><WarningOutlined />Recent anomalies</Space>} style={{ marginBottom: 16 }}>
        {anomData.length === 0 ? (
          <Typography.Text type="secondary">No anomalies detected.</Typography.Text>
        ) : (
          <Table
            rowKey={(r: any) => r.detected_at + r.kind} dataSource={anomData} pagination={{ pageSize: 8 }} size="small"
            columns={[
              { title: 'Severity', dataIndex: 'severity', render: (s) => <Tag color={sevColor[s] || 'default'}>{s}</Tag> },
              { title: 'Kind', dataIndex: 'kind' },
              { title: 'Message', dataIndex: 'message' },
              { title: 'When', dataIndex: 'detected_at', render: (t) => t ? dayjs(t).format('MM-DD HH:mm:ss') : '—' },
            ]}
          />
        )}
      </Card>

      {/* Cost by workspace */}
      <Card size="small" title="Cost by workspace (30d)" style={{ marginBottom: 16 }}>
        <Table
          rowKey={(r: any) => r.workspace_id ?? 'unattributed'} dataSource={wsData} pagination={false} size="small"
          columns={[
            { title: 'Workspace', dataIndex: 'workspace_name' },
            { title: 'Requests', dataIndex: 'requests' },
            { title: 'Cost', dataIndex: 'cost_usd', render: (v) => `$${v.toFixed(4)}` },
            { title: 'Savings', dataIndex: 'savings_usd', render: (v) => v ? `$${v.toFixed(4)}` : '—' },
            {
              title: 'Budget', dataIndex: 'budget',
              render: (b: any) => b && b.pct_used != null
                ? <Progress percent={Math.round(b.pct_used * 100)} size="small" status={b.pct_used >= 1 ? 'exception' : 'active'} style={{ width: 120 }} />
                : '—',
            },
          ]}
        />
      </Card>

      {/* Cost by key */}
      <Card size="small" title="Cost by API key / tenant (30d)" style={{ marginBottom: 16 }}>
        <Table
          rowKey={(r: any) => r.api_key_hash ?? r.label} dataSource={keyData} pagination={{ pageSize: 10 }} size="small"
          columns={[
            { title: 'Label', dataIndex: 'label', render: (t) => <Typography.Text strong>{t}</Typography.Text> },
            { title: 'Requests', dataIndex: 'requests' },
            { title: 'Tokens', dataIndex: 'total_tokens', render: (v) => v?.toLocaleString() },
            { title: 'Cost', dataIndex: 'cost_usd', render: (v) => `$${v.toFixed(4)}` },
            { title: 'Errors', dataIndex: 'errors' },
          ]}
        />
      </Card>

      {/* Routing replay */}
      <Card size="small" title={<Space><BranchesOutlined />Routing cost projection (shadow eval)</Space>}>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          Project the cost of a candidate routing policy over the last 30 days of real traffic.
          Candidate is a JSON map of <code>task_type</code> (or <code>"*"</code>) → model.
        </Typography.Paragraph>
        <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
          <Input value={candidate} onChange={(e) => setCandidate(e.target.value)} style={{ fontFamily: 'monospace' }} />
          <Button type="primary" loading={replayMut.isPending} onClick={runReplay}>Project</Button>
        </Space.Compact>
        {replayResult && (
          <Alert
            type={replayResult.projected_savings_usd > 0 ? 'success' : 'info'}
            message={
              <Space size={24} wrap>
                <span>Changed <b>{replayResult.routes_changed}</b>/{replayResult.decisions_evaluated} routes</span>
                <span>Actual <b>${replayResult.actual_cost_usd?.toFixed(2)}</b></span>
                <span>Projected <b>${replayResult.projected_cost_usd?.toFixed(2)}</b></span>
                <span>Savings <b style={{ color: replayResult.projected_savings_usd > 0 ? '#10b981' : undefined }}>${replayResult.projected_savings_usd?.toFixed(2)}</b></span>
              </Space>
            }
            description={<Typography.Text type="secondary" style={{ fontSize: 12 }}>Cost projection only — validate quality with an eval run before shifting traffic.</Typography.Text>}
          />
        )}
      </Card>
    </div>
  );
}
