import { useEffect, useState } from 'react';
import {
  Typography, Card, Button, Input, Select, Slider, Row, Col, Space, Tag, Spin,
  Statistic, App, Tooltip,
} from 'antd';
import {
  SendOutlined, ThunderboltOutlined, DollarOutlined, ClockCircleOutlined,
  CodeOutlined, CopyOutlined, SwapOutlined,
} from '@ant-design/icons';
import { getModels, runPlayground } from '../lib/api';

const { TextArea } = Input;

interface PlaygroundResult {
  model: string;
  provider?: string;
  content?: string;
  error?: string;
  latency_ms: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  cost_usd?: number;
}

export default function Playground() {
  const [models, setModels] = useState<any[]>([]);
  const [selectedModel, setSelectedModel] = useState('atlas-plan');
  const [compareModel, setCompareModel] = useState<string | null>(null);
  const [prompt, setPrompt] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [temperature, setTemperature] = useState(0.7);
  const [maxTokens, setMaxTokens] = useState(500);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<PlaygroundResult | null>(null);
  const [compareResult, setCompareResult] = useState<PlaygroundResult | null>(null);
  const [compareMode, setCompareMode] = useState(false);
  const { message } = App.useApp();

  useEffect(() => {
    getModels().then((m) => setModels(m.data || [])).catch(() => {});
  }, []);

  const handleSend = async () => {
    if (!prompt.trim()) return;
    setLoading(true);
    setResult(null);
    setCompareResult(null);

    try {
      const promises = [
        runPlayground({
          model: selectedModel, prompt, system_prompt: systemPrompt,
          temperature, max_tokens: maxTokens,
        }),
      ];
      if (compareMode && compareModel) {
        promises.push(
          runPlayground({
            model: compareModel, prompt, system_prompt: systemPrompt,
            temperature, max_tokens: maxTokens,
          })
        );
      }
      const results = await Promise.all(promises);
      setResult(results[0]);
      if (results[1]) setCompareResult(results[1]);
    } catch (e: any) {
      message.error(e.message || 'Request failed');
    }
    setLoading(false);
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    message.success('Copied to clipboard');
  };

  const modelOptions = models.map((m: any) => ({
    label: (
      <span>
        {m.id.startsWith('atlas-') ? '🔮 ' : ''}{m.id}
        <Tag color={m.owned_by === 'ollama' ? 'green' : m.owned_by === 'alpheric.ai' ? 'gold' : 'blue'} style={{ marginLeft: 8, fontSize: 10 }}>
          {m.owned_by}
        </Tag>
      </span>
    ),
    value: m.id,
  }));

  const ResultCard = ({ res, title }: { res: PlaygroundResult; title: string }) => (
    <Card
      size="small"
      title={
        <Space>
          <span>{title}</span>
          <Tag color={res.error ? 'red' : 'green'}>{res.model}</Tag>
          {res.provider && <Tag color="purple">{res.provider}</Tag>}
        </Space>
      }
      extra={
        res.content ? (
          <Tooltip title="Copy response">
            <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => copyToClipboard(res.content!)} />
          </Tooltip>
        ) : null
      }
    >
      {res.error ? (
        <div style={{ color: '#ef4444', padding: 12 }}>{res.error}</div>
      ) : (
        <div style={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: 13, lineHeight: 1.6, padding: 8, background: 'rgba(0,0,0,0.15)', borderRadius: 8, maxHeight: 400, overflow: 'auto' }}>
          {res.content}
        </div>
      )}
      <Row gutter={16} style={{ marginTop: 12 }}>
        <Col span={6}>
          <Statistic title="Latency" value={res.latency_ms} suffix="ms" valueStyle={{ fontSize: 16, color: res.latency_ms > 5000 ? '#ef4444' : '#10b981' }} prefix={<ClockCircleOutlined />} />
        </Col>
        <Col span={6}>
          <Statistic title="Tokens In" value={res.prompt_tokens || 0} valueStyle={{ fontSize: 16, color: '#3b82f6' }} prefix={<CodeOutlined />} />
        </Col>
        <Col span={6}>
          <Statistic title="Tokens Out" value={res.completion_tokens || 0} valueStyle={{ fontSize: 16, color: '#8b5cf6' }} prefix={<ThunderboltOutlined />} />
        </Col>
        <Col span={6}>
          <Statistic title="Cost" value={res.cost_usd || 0} prefix={<DollarOutlined />} precision={6} valueStyle={{ fontSize: 16, color: (res.cost_usd || 0) > 0 ? '#f59e0b' : '#10b981' }} />
        </Col>
      </Row>
    </Card>
  );

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>Prompt Playground</Typography.Title>
          <Typography.Text type="secondary">Test prompts against any available model in real-time</Typography.Text>
        </div>
        <Button
          icon={<SwapOutlined />}
          type={compareMode ? 'primary' : 'default'}
          onClick={() => setCompareMode(!compareMode)}
        >
          {compareMode ? 'Compare Mode ON' : 'Compare Models'}
        </Button>
      </div>

      <Row gutter={16}>
        {/* Left: Config */}
        <Col xs={24} lg={8}>
          <Card size="small" title="Configuration">
            <Space direction="vertical" style={{ width: '100%' }} size="middle">
              <div>
                <Typography.Text strong style={{ fontSize: 12 }}>Model</Typography.Text>
                <Select
                  style={{ width: '100%', marginTop: 4 }}
                  value={selectedModel}
                  onChange={setSelectedModel}
                  options={modelOptions}
                  showSearch
                  filterOption={(input, option) =>
                    (option?.value as string)?.toLowerCase().includes(input.toLowerCase())
                  }
                />
              </div>

              {compareMode && (
                <div>
                  <Typography.Text strong style={{ fontSize: 12 }}>Compare With</Typography.Text>
                  <Select
                    style={{ width: '100%', marginTop: 4 }}
                    value={compareModel}
                    onChange={setCompareModel}
                    options={modelOptions}
                    showSearch
                    placeholder="Select model to compare"
                    filterOption={(input, option) =>
                      (option?.value as string)?.toLowerCase().includes(input.toLowerCase())
                    }
                  />
                </div>
              )}

              <div>
                <Typography.Text strong style={{ fontSize: 12 }}>
                  Temperature: {temperature}
                </Typography.Text>
                <Slider min={0} max={2} step={0.1} value={temperature} onChange={setTemperature} />
              </div>

              <div>
                <Typography.Text strong style={{ fontSize: 12 }}>
                  Max Tokens: {maxTokens}
                </Typography.Text>
                <Slider min={50} max={4096} step={50} value={maxTokens} onChange={setMaxTokens} />
              </div>

              <div>
                <Typography.Text strong style={{ fontSize: 12 }}>System Prompt</Typography.Text>
                <TextArea
                  rows={3}
                  value={systemPrompt}
                  onChange={(e) => setSystemPrompt(e.target.value)}
                  placeholder="You are a helpful assistant..."
                  style={{ marginTop: 4 }}
                />
              </div>
            </Space>
          </Card>
        </Col>

        {/* Right: Prompt + Results */}
        <Col xs={24} lg={16}>
          <Card size="small" title="Prompt">
            <TextArea
              rows={5}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Enter your prompt here... (Shift+Enter for new line)"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
            />
            <div style={{ marginTop: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Press Enter to send, Shift+Enter for new line
              </Typography.Text>
              <Button
                type="primary"
                icon={<SendOutlined />}
                loading={loading}
                onClick={handleSend}
                disabled={!prompt.trim()}
                size="large"
              >
                Send
              </Button>
            </div>
          </Card>

          {loading && (
            <Card size="small" style={{ marginTop: 12, textAlign: 'center', padding: 24 }}>
              <Spin size="large" />
              <div style={{ marginTop: 12 }}>
                <Typography.Text type="secondary">Running inference...</Typography.Text>
              </div>
            </Card>
          )}

          {result && !loading && (
            <div style={{ marginTop: 12 }}>
              <ResultCard res={result} title={compareMode ? 'Model A' : 'Response'} />
            </div>
          )}

          {compareResult && !loading && (
            <div style={{ marginTop: 12 }}>
              <ResultCard res={compareResult} title="Model B" />
            </div>
          )}
        </Col>
      </Row>
    </div>
  );
}
