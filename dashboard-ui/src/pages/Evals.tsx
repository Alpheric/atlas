import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Typography, Card, Table, Tag, Button, Space, App, Form, Input, InputNumber, Tabs, Select,
} from 'antd';
import {
  PlusOutlined, PlayCircleOutlined, ImportOutlined,
} from '@ant-design/icons';
import {
  getEvalDatasets, createEvalDataset, promoteEvalFromDistillation,
  getEvalRuns, createEvalRun,
} from '../lib/api';
import PageSkeleton from '../components/shared/PageSkeleton';
import FormModal from '../components/shared/FormModal';
import dayjs from 'dayjs';

const runStatus: Record<string, string> = {
  pending: 'default', running: 'processing', completed: 'success', failed: 'error',
};

function score(v: number | null) {
  if (v === null || v === undefined) return '—';
  return (v * 100).toFixed(0);
}

export default function Evals() {
  const [dsOpen, setDsOpen] = useState(false);
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [dsForm] = Form.useForm();
  const [promoteForm] = Form.useForm();
  const [runForm] = Form.useForm();
  const { message } = App.useApp();
  const qc = useQueryClient();

  const { data: datasets = [], isLoading: dsLoading } = useQuery<any[]>({
    queryKey: ['evalDatasets'],
    queryFn: async () => (await getEvalDatasets()).data ?? [],
  });

  const { data: runs = [] } = useQuery<any[]>({
    queryKey: ['evalRuns'],
    queryFn: async () => (await getEvalRuns()).data ?? [],
    refetchInterval: 8_000,  // runs complete in the background
  });

  const dsMut = useMutation({
    mutationFn: createEvalDataset,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['evalDatasets'] }); message.success('Dataset created'); setDsOpen(false); dsForm.resetFields(); },
  });
  const promoteMut = useMutation({
    mutationFn: promoteEvalFromDistillation,
    onSuccess: (d: any) => { qc.invalidateQueries({ queryKey: ['evalDatasets'] }); message.success(`Promoted ${d.items_added} items`); setPromoteOpen(false); promoteForm.resetFields(); },
  });
  const runMut = useMutation({
    mutationFn: createEvalRun,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['evalRuns'] }); message.success('Eval run started'); setRunOpen(false); runForm.resetFields(); },
  });

  if (dsLoading) return <PageSkeleton type="table" />;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Evaluations</Typography.Title>
        <Space>
          <Button icon={<ImportOutlined />} onClick={() => setPromoteOpen(true)}>Promote from Distillation</Button>
          <Button icon={<PlusOutlined />} onClick={() => setDsOpen(true)}>New Dataset</Button>
          <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => setRunOpen(true)} disabled={datasets.length === 0}>Run Eval</Button>
        </Space>
      </div>

      <Tabs
        items={[
          {
            key: 'datasets', label: `Datasets (${datasets.length})`,
            children: (
              <Card size="small">
                <Table
                  rowKey="id" dataSource={datasets} pagination={false}
                  locale={{ emptyText: 'No eval datasets. Create one or promote from distillation records.' }}
                  columns={[
                    { title: 'Name', dataIndex: 'name', render: (t) => <Typography.Text strong>{t}</Typography.Text> },
                    { title: 'Task type', dataIndex: 'task_type', render: (t) => t ? <Tag>{t}</Tag> : '—' },
                    { title: 'Items', dataIndex: 'item_count' },
                    { title: 'Created', dataIndex: 'created_at', render: (t) => t ? dayjs(t).format('MM-DD HH:mm') : '—' },
                  ]}
                />
              </Card>
            ),
          },
          {
            key: 'runs', label: `Runs (${runs.length})`,
            children: (
              <Card size="small">
                <Table
                  rowKey="id" dataSource={runs} pagination={{ pageSize: 20 }}
                  locale={{ emptyText: 'No eval runs yet.' }}
                  columns={[
                    { title: 'Model', dataIndex: 'model', render: (t) => <Typography.Text style={{ fontFamily: 'monospace' }}>{t}</Typography.Text> },
                    { title: 'Status', dataIndex: 'status', render: (s) => <Tag color={runStatus[s] || 'default'}>{s}</Tag> },
                    { title: 'Items', dataIndex: 'item_count' },
                    { title: 'Heuristic', dataIndex: 'avg_heuristic', render: score },
                    { title: 'Judge', dataIndex: 'avg_judge', render: (v: number | null) => <Typography.Text strong>{score(v)}</Typography.Text> },
                    { title: 'Latency', dataIndex: 'avg_latency_ms', render: (v: number | null) => v ? `${Math.round(v)}ms` : '—' },
                    { title: 'Started', dataIndex: 'created_at', render: (t) => t ? dayjs(t).format('MM-DD HH:mm') : '—' },
                  ]}
                />
              </Card>
            ),
          },
        ]}
      />

      <FormModal title="New Eval Dataset" open={dsOpen} onCancel={() => setDsOpen(false)} onSubmit={(v) => dsMut.mutateAsync(v)} okText="Create" form={dsForm}>
        <Form form={dsForm} layout="vertical">
          <Form.Item name="name" label="Name" rules={[{ required: true }]}><Input placeholder="code-eval" /></Form.Item>
          <Form.Item name="task_type" label="Task type (optional)"><Input placeholder="code" /></Form.Item>
          <Form.Item name="description" label="Description (optional)"><Input /></Form.Item>
        </Form>
      </FormModal>

      <FormModal title="Promote from Distillation" open={promoteOpen} onCancel={() => setPromoteOpen(false)} onSubmit={(v) => promoteMut.mutateAsync(v)} okText="Promote" form={promoteForm}>
        <Form form={promoteForm} layout="vertical" initialValues={{ min_quality: 0.7, limit: 50 }}>
          <Form.Item name="dataset_name" label="Dataset name" rules={[{ required: true }]}><Input placeholder="code-eval" /></Form.Item>
          <Form.Item name="task_type" label="Task type (optional)"><Input placeholder="code" /></Form.Item>
          <Space>
            <Form.Item name="min_quality" label="Min quality"><InputNumber min={0} max={1} step={0.05} /></Form.Item>
            <Form.Item name="limit" label="Max items"><InputNumber min={1} max={500} /></Form.Item>
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Poisoned/error/too-short records are skipped automatically.
          </Typography.Text>
        </Form>
      </FormModal>

      <FormModal title="Run Eval" open={runOpen} onCancel={() => setRunOpen(false)} onSubmit={(v) => runMut.mutateAsync(v)} okText="Start" form={runForm}>
        <Form form={runForm} layout="vertical">
          <Form.Item name="dataset_id" label="Dataset" rules={[{ required: true }]}>
            <Select
              placeholder="Select a dataset"
              options={datasets.map((d: any) => ({ value: d.id, label: `${d.name} (${d.item_count} items)` }))}
            />
          </Form.Item>
          <Form.Item name="model" label="Model" rules={[{ required: true }]}>
            <Input placeholder="gemini-2.5-flash or qwen2.5-coder:7b" style={{ fontFamily: 'monospace' }} />
          </Form.Item>
        </Form>
      </FormModal>
    </div>
  );
}
