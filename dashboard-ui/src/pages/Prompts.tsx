import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Typography, Card, Table, Tag, Button, Space, App, Form, Input, Select, Drawer,
} from 'antd';
import { FileTextOutlined, PlusOutlined, CheckCircleOutlined } from '@ant-design/icons';
import {
  getPrompts, getPromptVersions, createPrompt, activatePromptVersion,
} from '../lib/api';
import PageSkeleton from '../components/shared/PageSkeleton';
import FormModal from '../components/shared/FormModal';
import dayjs from 'dayjs';

export default function Prompts() {
  const [createOpen, setCreateOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [form] = Form.useForm();
  const { message } = App.useApp();
  const qc = useQueryClient();

  const { data: prompts = [], isLoading } = useQuery<any[]>({
    queryKey: ['prompts'],
    queryFn: async () => (await getPrompts()).data ?? [],
  });

  const { data: versions } = useQuery<any>({
    queryKey: ['promptVersions', selected],
    queryFn: () => getPromptVersions(selected as string),
    enabled: !!selected,
  });

  const createMut = useMutation({
    mutationFn: createPrompt,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['prompts'] });
      message.success('Prompt version created');
      setCreateOpen(false);
      form.resetFields();
    },
  });

  const activateMut = useMutation({
    mutationFn: ({ name, version }: { name: string; version: number }) =>
      activatePromptVersion(name, version),
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: ['prompts'] });
      qc.invalidateQueries({ queryKey: ['promptVersions', v.name] });
      message.success(`Activated v${v.version}`);
    },
  });

  if (isLoading) return <PageSkeleton type="table" />;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Prompts</Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          New Prompt Version
        </Button>
      </div>

      <Card size="small">
        <Table
          rowKey="name"
          dataSource={prompts}
          pagination={false}
          locale={{ emptyText: 'No prompt overrides — the pipeline uses built-in code defaults.' }}
          columns={[
            { title: 'Name', dataIndex: 'name', render: (t) => <Typography.Text strong style={{ fontFamily: 'monospace' }}>{t}</Typography.Text> },
            { title: 'Versions', dataIndex: 'versions' },
            { title: 'Active', dataIndex: 'active_version', render: (v) => v ? <Tag color="success">v{v}</Tag> : <Tag>none</Tag> },
            { title: 'Updated', dataIndex: 'updated_at', render: (t) => t ? dayjs(t).format('MM-DD HH:mm') : '—' },
            { title: '', render: (_t, r: any) => <Button size="small" onClick={() => setSelected(r.name)}>Versions</Button> },
          ]}
        />
      </Card>

      <Drawer
        title={selected ? `Versions — ${selected}` : ''}
        width={680}
        open={!!selected}
        onClose={() => setSelected(null)}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          {(versions?.data ?? []).map((v: any) => (
            <Card
              key={v.id}
              size="small"
              title={<Space><FileTextOutlined />v{v.version}{v.is_active && <Tag color="success" icon={<CheckCircleOutlined />}>active</Tag>}{v.model && <Tag>{v.model}</Tag>}</Space>}
              extra={!v.is_active && (
                <Button size="small" type="primary" loading={activateMut.isPending}
                  onClick={() => activateMut.mutate({ name: v.name, version: v.version })}>
                  Activate
                </Button>
              )}
            >
              {v.description && <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>{v.description}</Typography.Paragraph>}
              <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0, maxHeight: 300, overflow: 'auto' }}>{v.content}</pre>
            </Card>
          ))}
        </Space>
      </Drawer>

      <FormModal
        title="New Prompt Version"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onSubmit={(values) => createMut.mutateAsync(values)}
        okText="Create"
        form={form}
      >
        <Form form={form} layout="vertical" initialValues={{ activate: true }}>
          <Form.Item name="name" label="Name (logical key)" rules={[{ required: true }]}>
            <Input placeholder="self_critique" style={{ fontFamily: 'monospace' }} />
          </Form.Item>
          <Form.Item name="content" label="Content" rules={[{ required: true }]}
            extra="Use {placeholders} matching the consuming code (e.g. {task_type}, {user_message}).">
            <Input.TextArea rows={8} style={{ fontFamily: 'monospace', fontSize: 12 }} />
          </Form.Item>
          <Space style={{ width: '100%' }} align="start">
            <Form.Item name="model" label="Model scope (optional)">
              <Input placeholder="atlas-code" style={{ fontFamily: 'monospace' }} />
            </Form.Item>
            <Form.Item name="activate" label="Activate on create">
              <Select options={[{ value: true, label: 'Yes' }, { value: false, label: 'No' }]} style={{ width: 100 }} />
            </Form.Item>
          </Space>
          <Form.Item name="description" label="Description (optional)">
            <Input placeholder="Why this version" />
          </Form.Item>
        </Form>
      </FormModal>
    </div>
  );
}
