import { useEffect, useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { Typography, Card, Form, Input, InputNumber, Select, Slider, Button, Space, App } from 'antd';
import { KeyOutlined, BranchesOutlined, ExperimentOutlined, SaveOutlined } from '@ant-design/icons';
import { getSettings, saveSettings } from '../lib/api';

const DEFAULT_SETTINGS = {
  anthropic_api_key: '',
  openai_api_key: '',
  vertex_project_id: '',
  ollama_base_url: 'http://localhost:11434',
  default_strategy: 'best_quality',
  exploration_rate: 0.1,
  training_base_model: 'mistralai/Mistral-7B-Instruct-v0.3',
  training_lora_rank: 16,
  training_min_quality: 0.7,
  training_min_samples: 500,
};

export default function SettingsPage() {
  const [form] = Form.useForm();
  const [dirty, setDirty] = useState(false);
  const { message } = App.useApp();

  const { data, isLoading: loading } = useQuery({
    queryKey: ['settings'],
    // Backend may not support settings yet — fall back to defaults.
    queryFn: () => getSettings().catch(() => DEFAULT_SETTINGS),
  });

  useEffect(() => {
    if (data) form.setFieldsValue(data);
  }, [data, form]);

  const saveMut = useMutation({
    mutationFn: saveSettings,
    onSuccess: () => { message.success('Settings saved'); setDirty(false); },
    onError: () => message.error('Failed to save settings'),
  });
  const saving = saveMut.isPending;

  const handleSave = () => saveMut.mutate(form.getFieldsValue());

  return (
    <div style={{ maxWidth: 700 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Settings</Typography.Title>
        <Button type="primary" icon={<SaveOutlined />} onClick={handleSave} loading={saving} disabled={!dirty}>
          Save Changes
        </Button>
      </div>

      <Form form={form} layout="vertical" onValuesChange={() => setDirty(true)} style={{ opacity: loading ? 0.5 : 1, pointerEvents: loading ? 'none' : 'auto' }}>
        {/* Provider Keys */}
        <Card title={<Space><KeyOutlined style={{ color: '#f59e0b' }} />Provider API Keys</Space>} size="small" style={{ marginBottom: 16 }}>
          <Form.Item name="anthropic_api_key" label="Anthropic API Key">
            <Input.Password placeholder="sk-ant-..." visibilityToggle />
          </Form.Item>
          <Form.Item name="openai_api_key" label="OpenAI API Key">
            <Input.Password placeholder="sk-..." visibilityToggle />
          </Form.Item>
          <Form.Item name="vertex_project_id" label="Google Vertex Project ID">
            <Input placeholder="my-gcp-project" />
          </Form.Item>
          <Form.Item name="ollama_base_url" label="Ollama Base URL">
            <Input placeholder="http://localhost:11434" />
          </Form.Item>
        </Card>

        {/* Routing */}
        <Card title={<Space><BranchesOutlined style={{ color: '#3b82f6' }} />Routing Policy</Space>} size="small" style={{ marginBottom: 16 }}>
          <Form.Item name="default_strategy" label="Default Strategy">
            <Select options={[
              { value: 'best_quality', label: 'Best Quality' },
              { value: 'lowest_cost', label: 'Lowest Cost' },
              { value: 'lowest_latency', label: 'Lowest Latency' },
            ]} />
          </Form.Item>
          <Form.Item name="exploration_rate" label="Exploration Rate">
            <Slider min={0} max={0.5} step={0.05} tooltip={{ formatter: (v) => `${((v || 0) * 100).toFixed(0)}%` }} />
          </Form.Item>
        </Card>

        {/* Training */}
        <Card title={<Space><ExperimentOutlined style={{ color: '#8b5cf6' }} />Training Configuration</Space>} size="small">
          <Form.Item name="training_base_model" label="Base Model">
            <Input style={{ fontFamily: 'monospace' }} />
          </Form.Item>
          <Space>
            <Form.Item name="training_lora_rank" label="LoRA Rank"><InputNumber min={4} max={128} /></Form.Item>
            <Form.Item name="training_min_quality" label="Min Quality"><InputNumber min={0} max={1} step={0.1} /></Form.Item>
            <Form.Item name="training_min_samples" label="Min Samples"><InputNumber min={10} max={50000} /></Form.Item>
          </Space>
        </Card>
      </Form>
    </div>
  );
}
