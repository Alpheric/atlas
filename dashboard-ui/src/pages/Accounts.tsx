import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Typography, Card, Table, Tag, Button, Modal, Form, Input, InputNumber, Select, Space, Progress, App } from 'antd';
import { PlusOutlined, DeleteOutlined, CheckCircleOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';

// API functions inline since they're not yet in api.ts
import api from '../lib/api';
const getAccounts = () => api.get('/admin/accounts').then(r => r.data);
const createAccount = (data: any) => api.post('/admin/accounts', null, { params: data }).then(r => r.data);
const deleteAccount = (id: string) => api.delete(`/admin/accounts/${id}`).then(r => r.data);
const testAccount = (id: string) => api.post(`/admin/accounts/${id}/test`).then(r => r.data);

export default function Accounts() {
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const { message: msgApi } = App.useApp();
  const qc = useQueryClient();

  const { data: accounts = [], isLoading: loading } = useQuery<any[]>({
    queryKey: ['accounts'],
    queryFn: async () => (await getAccounts()).data ?? [],
  });

  const createMut = useMutation({
    mutationFn: createAccount,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['accounts'] });
      msgApi.success('Account created');
      setModalOpen(false);
      form.resetFields();
    },
  });

  const deleteMut = useMutation({
    mutationFn: deleteAccount,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['accounts'] });
      msgApi.success('Account deleted');
    },
  });

  const handleCreate = async () => {
    const values = await form.validateFields();
    await createMut.mutateAsync(values);
  };

  const handleDelete = (id: string) => deleteMut.mutate(id);

  const handleTest = async (id: string) => {
    try {
      const result = await testAccount(id);
      if (result.status === 'ok') msgApi.success('Key is valid');
      else msgApi.error(result.message || 'Key test failed');
    } catch { msgApi.error('Test failed'); }
  };

  const columns = [
    { title: 'Provider', dataIndex: 'provider', width: 110, render: (p: string) => <Tag color="blue" style={{ textTransform: 'capitalize' }}>{p}</Tag> },
    { title: 'Name', dataIndex: 'name', ellipsis: true },
    { title: 'Status', dataIndex: 'is_active', width: 80, render: (v: boolean) => <Tag color={v ? 'success' : 'default'}>{v ? 'Active' : 'Off'}</Tag> },
    { title: 'Priority', dataIndex: 'priority', width: 80 },
    { title: 'Requests', dataIndex: 'total_requests', width: 100, sorter: (a: any, b: any) => a.total_requests - b.total_requests },
    { title: 'Tokens', dataIndex: 'total_tokens', width: 110, render: (v: number) => v?.toLocaleString(), sorter: (a: any, b: any) => a.total_tokens - b.total_tokens },
    {
      title: 'Budget', width: 160, render: (_: any, r: any) => {
        if (!r.monthly_budget_usd) return '—';
        const pct = Math.min(100, (r.current_month_cost_usd / r.monthly_budget_usd) * 100);
        return (
          <div>
            <Progress percent={Math.round(pct)} size="small" status={pct > 90 ? 'exception' : 'normal'} />
            <span style={{ fontSize: 11, color: '#9ca3af' }}>${r.current_month_cost_usd?.toFixed(2)} / ${r.monthly_budget_usd}</span>
          </div>
        );
      }
    },
    { title: 'Last Used', dataIndex: 'last_used_at', width: 140, render: (d: string) => d ? dayjs(d).format('MM-DD HH:mm') : '—' },
    { title: 'Last Error', dataIndex: 'last_error', width: 160, ellipsis: true, render: (e: string) => e ? <Tag color="error">{e.slice(0, 40)}</Tag> : '—' },
    {
      title: 'Actions', width: 140, render: (_: any, r: any) => (
        <Space size="small">
          <Button size="small" icon={<CheckCircleOutlined />} onClick={() => handleTest(r.id)}>Test</Button>
          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => handleDelete(r.id)} />
        </Space>
      )
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Provider Accounts</Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>Add Account</Button>
      </div>

      <Card size="small" styles={{ body: { padding: 0 } }}>
        <Table columns={columns} dataSource={accounts} rowKey="id" loading={loading} size="small"
          pagination={{ pageSize: 25, showTotal: (t) => `${t} accounts` }} scroll={{ x: 1100 }} />
      </Card>

      <Modal title="Add Provider Account" open={modalOpen} onOk={handleCreate} onCancel={() => setModalOpen(false)} confirmLoading={createMut.isPending} okText="Add Account">
        <Form form={form} layout="vertical" initialValues={{ provider: 'anthropic', priority: 0 }}>
          <Form.Item name="provider" label="Provider" rules={[{ required: true }]}>
            <Select options={[
              { value: 'anthropic', label: 'Anthropic (Claude)' },
              { value: 'openai', label: 'OpenAI' },
              { value: 'vertex', label: 'Google Vertex' },
              { value: 'groq', label: 'Groq' },
              { value: 'together', label: 'Together AI' },
              { value: 'mistral', label: 'Mistral' },
              { value: 'deepseek', label: 'DeepSeek' },
            ]} />
          </Form.Item>
          <Form.Item name="name" label="Account Name" rules={[{ required: true }]}>
            <Input placeholder="Claude Team Subscription #1" />
          </Form.Item>
          <Form.Item name="api_key" label="API Key" rules={[{ required: true }]}>
            <Input.Password placeholder="sk-ant-..." />
          </Form.Item>
          <Space>
            <Form.Item name="priority" label="Priority"><InputNumber min={0} max={100} /></Form.Item>
            <Form.Item name="rate_limit_rpm" label="Rate Limit (RPM)"><InputNumber min={1} placeholder="60" /></Form.Item>
            <Form.Item name="monthly_budget_usd" label="Monthly Budget ($)"><InputNumber min={0} step={10} placeholder="100" /></Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  );
}
