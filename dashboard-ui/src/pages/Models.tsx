import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Typography, Card, Table, Tag, Button, Modal, Form, Input, Select, Space, Row, Col, Spin, App } from 'antd';
import { CloudServerOutlined, DeleteOutlined, SwapOutlined } from '@ant-design/icons';

import api from '../lib/api';
const getOllamaModels = () => api.get('/admin/ollama/models').then(r => r.data);
const pullModel = (name: string, serverUrl?: string) => api.post('/admin/ollama/pull', null, { params: { name, server_url: serverUrl } }).then(r => r.data);
const deleteModel = (name: string, serverUrl?: string) => api.delete(`/admin/ollama/models/${name}`, { params: { server_url: serverUrl } }).then(r => r.data);
const compareModels = (body: any) => api.post('/admin/models/compare', body).then(r => r.data);

export default function Models() {
  const [pullModalOpen, setPullModalOpen] = useState(false);
  const [pulling, setPulling] = useState(false);
  const [compareModalOpen, setCompareModalOpen] = useState(false);
  const [comparing, setComparing] = useState(false);
  const [compareResults, setCompareResults] = useState<any[]>([]);
  const [pullForm] = Form.useForm();
  const [compareForm] = Form.useForm();
  const { message: msgApi } = App.useApp();

  const { data = { data: [], servers: [] }, isLoading: loading, refetch } = useQuery<any>({
    queryKey: ['ollamaModels'],
    queryFn: getOllamaModels,
  });

  const handlePull = async () => {
    setPulling(true);
    try {
      const values = await pullForm.validateFields();
      await pullModel(values.name, values.server_url);
      msgApi.success(`Pulling ${values.name}...`);
      setPullModalOpen(false);
      pullForm.resetFields();
      setTimeout(() => refetch(), 5000); // reload after pull starts
    } catch {}
    setPulling(false);
  };

  const handleDelete = async (name: string) => {
    await deleteModel(name);
    msgApi.success(`Deleted ${name}`);
    refetch();
  };

  const handleCompare = async () => {
    setComparing(true);
    setCompareResults([]);
    try {
      const values = await compareForm.validateFields();
      const result = await compareModels(values);
      setCompareResults(result.results || []);
    } catch {}
    setComparing(false);
  };

  const modelColumns = [
    { title: 'Model', dataIndex: 'name', render: (n: string) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{n}</span> },
    { title: 'Provider', dataIndex: 'provider', render: (p: string) => <Tag color="green">{p}</Tag> },
    { title: 'Context', dataIndex: 'context_window', render: (v: number) => v?.toLocaleString() },
    {
      title: 'Actions', width: 100, render: (_: any, r: any) => (
        <Button size="small" danger icon={<DeleteOutlined />} onClick={() => handleDelete(r.name)} />
      )
    },
  ];

  const allModelNames = data.data?.map((m: any) => m.name) || [];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Local Models</Typography.Title>
        <Space>
          <Button icon={<SwapOutlined />} onClick={() => setCompareModalOpen(true)}>Compare Models</Button>
          <Button type="primary" icon={<CloudServerOutlined />} onClick={() => setPullModalOpen(true)}>Pull Model</Button>
        </Space>
      </div>

      {/* Servers */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        {(data.servers || []).map((s: any) => (
          <Col key={s.url} xs={24} md={12}>
            <Card size="small" style={{ borderLeft: `3px solid ${s.healthy ? '#10b981' : '#ef4444'}` }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <Space>
                  <CloudServerOutlined />
                  <Typography.Text strong>{s.url}</Typography.Text>
                </Space>
                <Tag color={s.healthy ? 'success' : 'error'}>{s.healthy ? 'Online' : 'Offline'}</Tag>
              </div>
              <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {(s.models || []).map((m: string) => <Tag key={m} color="blue" style={{ fontSize: 11 }}>{m}</Tag>)}
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* Models table */}
      <Card title={`All Local Models (${data.data?.length || 0})`} size="small">
        <Table columns={modelColumns} dataSource={data.data || []} rowKey="name" loading={loading} size="small" pagination={false} />
      </Card>

      {/* Pull Modal */}
      <Modal title="Pull Ollama Model" open={pullModalOpen} onOk={handlePull} onCancel={() => setPullModalOpen(false)} confirmLoading={pulling} okText="Pull">
        <Form form={pullForm} layout="vertical">
          <Form.Item name="name" label="Model Name" rules={[{ required: true }]}>
            <Input placeholder="llama3.2:latest" />
          </Form.Item>
          <Form.Item name="server_url" label="Server (optional)">
            <Select allowClear placeholder="Auto-select" options={(data.servers || []).map((s: any) => ({ value: s.url, label: `${s.url} (${s.model_count} models)` }))} />
          </Form.Item>
        </Form>
      </Modal>

      {/* Compare Modal */}
      <Modal title="Compare Models" open={compareModalOpen} onOk={handleCompare} onCancel={() => { setCompareModalOpen(false); setCompareResults([]); }} confirmLoading={comparing} okText="Compare" width={800}>
        <Form form={compareForm} layout="vertical" initialValues={{ max_tokens: 500 }}>
          <Form.Item name="prompt" label="Prompt" rules={[{ required: true }]}>
            <Input.TextArea rows={3} placeholder="Write a function to..." />
          </Form.Item>
          <Form.Item name="models" label="Models to Compare" rules={[{ required: true }]}>
            <Select mode="multiple" placeholder="Select 2+ models" options={allModelNames.map((m: string) => ({ value: m, label: m }))} />
          </Form.Item>
          <Form.Item name="max_tokens" label="Max Tokens"><Input type="number" /></Form.Item>
        </Form>

        {comparing && <div style={{ textAlign: 'center', padding: 24 }}><Spin size="large" /><br />Running comparison...</div>}

        {compareResults.length > 0 && (
          <Row gutter={[12, 12]} style={{ marginTop: 16 }}>
            {compareResults.map((r, i) => (
              <Col key={i} xs={24} md={12}>
                <Card size="small" title={<Space><Tag color="blue">{r.provider || '?'}</Tag><span style={{ fontFamily: 'monospace', fontSize: 12 }}>{r.model}</span></Space>}
                  extra={r.error ? <Tag color="error">Error</Tag> : <Tag color="success">{r.latency_ms}ms</Tag>}
                >
                  {r.error ? (
                    <Typography.Text type="danger">{r.error}</Typography.Text>
                  ) : (
                    <>
                      <div style={{ whiteSpace: 'pre-wrap', fontSize: 12, maxHeight: 200, overflow: 'auto' }}>{r.content}</div>
                      <div style={{ marginTop: 8, fontSize: 11, color: '#9ca3af' }}>
                        {r.prompt_tokens} in / {r.completion_tokens} out
                      </div>
                    </>
                  )}
                </Card>
              </Col>
            ))}
          </Row>
        )}
      </Modal>
    </div>
  );
}
