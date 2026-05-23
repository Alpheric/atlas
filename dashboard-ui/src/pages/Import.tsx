import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Typography, Card, Form, Input, Button, Result, Alert, Space, Upload, App } from 'antd';
import { ImportOutlined, UploadOutlined, InboxOutlined } from '@ant-design/icons';
import { triggerPaperclipImport } from '../lib/api';

const { Dragger } = Upload;

export default function Import() {
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState('');
  const { message } = App.useApp();

  const importMut = useMutation({
    mutationFn: (values: { api_url: string; api_key?: string }) =>
      triggerPaperclipImport(values.api_url, values.api_key),
    onMutate: () => { setError(''); setResult(null); },
    onSuccess: (stats: any) => {
      setResult(stats);
      message.success(`Imported ${stats.imported} conversations`);
    },
    onError: (e: any) => setError(e.response?.data?.detail || e.message || 'Import failed'),
  });
  const importing = importMut.isPending;

  const handlePaperclipImport = (values: { api_url: string; api_key?: string }) =>
    importMut.mutate(values);

  return (
    <div style={{ maxWidth: 700 }}>
      <Typography.Title level={4}>Import</Typography.Title>

      {/* Paperclip.ing */}
      <Card title={<Space><ImportOutlined style={{ color: '#3b82f6' }} />Import from Paperclip.ing</Space>} size="small" style={{ marginBottom: 16 }}>
        <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
          Import conversation history from your paperclip.ing instance.
        </Typography.Paragraph>

        <Form onFinish={handlePaperclipImport} layout="vertical" requiredMark={false}>
          <Form.Item name="api_url" label="API URL" rules={[{ required: true, message: 'API URL is required' }]}>
            <Input placeholder="https://your-instance.paperclip.ing" />
          </Form.Item>
          <Form.Item name="api_key" label="API Key">
            <Input.Password placeholder="sk-..." />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={importing} icon={<UploadOutlined />}>
              Start Import
            </Button>
          </Form.Item>
        </Form>

        {result && (
          <Result status="success" title="Import Complete" subTitle={`Imported ${result.imported} conversations`}
            extra={<Space direction="vertical" size={4}>
              <Typography.Text>Skipped (duplicates): {result.skipped}</Typography.Text>
              <Typography.Text type={result.errors > 0 ? 'danger' : undefined}>Errors: {result.errors}</Typography.Text>
            </Space>}
            style={{ padding: '16px 0' }}
          />
        )}
        {error && <Alert message="Import Failed" description={error} type="error" showIcon />}
      </Card>

      {/* JSONL Upload */}
      <Card title={<Space><ImportOutlined style={{ color: '#8b5cf6' }} />Import from JSONL</Space>} size="small">
        <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
          Upload an OpenAI-style JSONL file with conversations.
        </Typography.Paragraph>

        <Dragger
          name="file"
          accept=".jsonl,.json"
          action="/admin/import/jsonl-upload"
          maxCount={1}
          onChange={(info) => {
            if (info.file.status === 'done') {
              message.success(`${info.file.name} imported successfully`);
            } else if (info.file.status === 'error') {
              message.error(`${info.file.name} import failed`);
            }
          }}
        >
          <p style={{ fontSize: 32, color: '#6b7280' }}><InboxOutlined /></p>
          <p>Click or drag JSONL file to upload</p>
          <p style={{ fontSize: 12, color: '#9ca3af' }}>
            Each line: {`{"messages": [{"role": "user", "content": "..."}, ...]}`}
          </p>
        </Dragger>
      </Card>
    </div>
  );
}
