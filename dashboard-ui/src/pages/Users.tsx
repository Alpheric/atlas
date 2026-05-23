import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Table, Button, Tag, Space, Modal, Form, Input, InputNumber, Select,
  Tooltip, Drawer, Descriptions, Statistic, Row, Col, Card, Typography,
  Popconfirm, Switch, Badge, message as antMessage, Divider, Alert,
} from 'antd';
import {
  UserAddOutlined, KeyOutlined, DeleteOutlined, EditOutlined,
  EyeOutlined, CopyOutlined, CheckCircleOutlined, StopOutlined,
  ReloadOutlined, PlusOutlined,
} from '@ant-design/icons';
import {
  getUsers, createUser, updateUser,
  getUserUsage, createApiKey, revokeApiKey, toggleApiKey,
} from '../lib/api';

const { Text, Title } = Typography;

interface UserRecord {
  id: string;
  name: string;
  email: string;
  role: string;
  is_active: boolean;
  rate_limit: number;
  monthly_token_limit: number;
  created_at: string;
  key_count: number;
  active_key_count: number;
  usage: {
    total_requests: number;
    prompt_tokens: number;
    completion_tokens: number;
    cost_usd: number;
  };
}

interface ApiKeyRecord {
  id: string;
  name: string;
  role: string;
  is_active: boolean;
  rate_limit: number;
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
  key_prefix: string;
  usage?: {
    total_requests: number;
    prompt_tokens: number;
    completion_tokens: number;
    cost_usd: number;
  };
}

interface NewKeyResult {
  key_id: string;
  name: string;
  api_key: string;
  key_prefix: string;
  role: string;
  rate_limit: number;
  expires_at: string | null;
  warning: string;
}

export default function UsersPage() {
  const { data: usersData, isFetching: loading, refetch: fetchUsers } = useQuery<UserRecord[]>({
    queryKey: ['users'],
    queryFn: async () => (await getUsers()).data ?? [],
  });
  const users = usersData ?? [];

  // Create user modal
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm();
  const [creating, setCreating] = useState(false);

  // Edit user modal
  const [editOpen, setEditOpen] = useState(false);
  const [editForm] = Form.useForm();
  const [editTarget, setEditTarget] = useState<UserRecord | null>(null);
  const [editing, setEditing] = useState(false);

  // User detail drawer
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerUser, setDrawerUser] = useState<UserRecord | null>(null);
  const [usageData, setUsageData] = useState<any>(null);
  const [usageLoading, setUsageLoading] = useState(false);

  // Create key modal
  const [keyModalOpen, setKeyModalOpen] = useState(false);
  const [keyForm] = Form.useForm();
  const [keyTarget, setKeyTarget] = useState<UserRecord | null>(null);
  const [creatingKey, setCreatingKey] = useState(false);
  const [newKey, setNewKey] = useState<NewKeyResult | null>(null);


  // ── Create user ──────────────────────────────────────────────────────────
  const handleCreate = async (values: any) => {
    setCreating(true);
    try {
      await createUser(values);
      antMessage.success('User created');
      setCreateOpen(false);
      createForm.resetFields();
      fetchUsers();
    } finally {
      setCreating(false);
    }
  };

  // ── Edit user ────────────────────────────────────────────────────────────
  const openEdit = (user: UserRecord) => {
    setEditTarget(user);
    editForm.setFieldsValue({
      name: user.name,
      role: user.role,
      rate_limit: user.rate_limit,
      monthly_token_limit: user.monthly_token_limit,
    });
    setEditOpen(true);
  };

  const handleEdit = async (values: any) => {
    if (!editTarget) return;
    setEditing(true);
    try {
      await updateUser(editTarget.id, values);
      antMessage.success('User updated');
      setEditOpen(false);
      fetchUsers();
    } finally {
      setEditing(false);
    }
  };

  const handleToggleActive = async (user: UserRecord) => {
    try {
      await updateUser(user.id, { is_active: !user.is_active });
      antMessage.success(user.is_active ? 'User deactivated' : 'User activated');
      fetchUsers();
    } catch (_) {}
  };

  // ── View detail drawer ───────────────────────────────────────────────────
  const openDrawer = async (user: UserRecord) => {
    setDrawerUser(user);
    setDrawerOpen(true);
    setUsageData(null);
    setUsageLoading(true);
    try {
      const data = await getUserUsage(user.id);
      setUsageData(data);
    } catch (_) {
    } finally {
      setUsageLoading(false);
    }
  };

  // ── Create API key ────────────────────────────────────────────────────────
  const openKeyModal = (user: UserRecord) => {
    setKeyTarget(user);
    setNewKey(null);
    keyForm.resetFields();
    setKeyModalOpen(true);
  };

  const handleCreateKey = async (values: any) => {
    if (!keyTarget) return;
    setCreatingKey(true);
    try {
      const result = await createApiKey(keyTarget.id, {
        name: values.name,
        rate_limit: values.rate_limit || undefined,
      });
      setNewKey(result);
      fetchUsers();
    } finally {
      setCreatingKey(false);
    }
  };

  // ── Revoke key ────────────────────────────────────────────────────────────
  const handleRevokeKey = async (userId: string, keyId: string) => {
    try {
      await revokeApiKey(userId, keyId);
      antMessage.success('Key revoked');
      if (drawerUser) {
        // refresh drawer usage
        const data = await getUserUsage(userId);
        setUsageData(data);
      }
      fetchUsers();
    } catch (_) {}
  };

  const handleToggleKey = async (userId: string, keyId: string, current: boolean) => {
    try {
      await toggleApiKey(userId, keyId, !current);
      antMessage.success(!current ? 'Key enabled' : 'Key disabled');
      if (drawerUser) {
        const data = await getUserUsage(userId);
        setUsageData(data);
      }
      fetchUsers();
    } catch (_) {}
  };

  // ── Table columns ────────────────────────────────────────────────────────
  const columns = [
    {
      title: 'Name',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, record: UserRecord) => (
        <Space>
          <Badge status={record.is_active ? 'success' : 'error'} />
          <Text strong>{name}</Text>
        </Space>
      ),
    },
    {
      title: 'Email',
      dataIndex: 'email',
      key: 'email',
      render: (email: string) => <Text type="secondary">{email}</Text>,
    },
    {
      title: 'Role',
      dataIndex: 'role',
      key: 'role',
      render: (role: string) => (
        <Tag color={role === 'admin' ? 'red' : role === 'operator' ? 'orange' : 'blue'}>
          {role}
        </Tag>
      ),
    },
    {
      title: 'Keys',
      key: 'keys',
      render: (_: any, r: UserRecord) => (
        <Space>
          <Text>{r.active_key_count} active</Text>
          <Text type="secondary">/ {r.key_count} total</Text>
        </Space>
      ),
    },
    {
      title: 'Requests',
      key: 'requests',
      render: (_: any, r: UserRecord) => (
        <Text>{r.usage.total_requests.toLocaleString()}</Text>
      ),
    },
    {
      title: 'Cost (USD)',
      key: 'cost',
      render: (_: any, r: UserRecord) => (
        <Text>${r.usage.cost_usd.toFixed(4)}</Text>
      ),
    },
    {
      title: 'Rate Limit',
      dataIndex: 'rate_limit',
      key: 'rate_limit',
      render: (v: number) => <Text>{v} rpm</Text>,
    },
    {
      title: 'Actions',
      key: 'actions',
      render: (_: any, record: UserRecord) => (
        <Space size="small">
          <Tooltip title="View details & keys">
            <Button size="small" icon={<EyeOutlined />} onClick={() => openDrawer(record)} />
          </Tooltip>
          <Tooltip title="Create API key">
            <Button size="small" icon={<KeyOutlined />} onClick={() => openKeyModal(record)}
              disabled={!record.is_active} />
          </Tooltip>
          <Tooltip title="Edit user">
            <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(record)} />
          </Tooltip>
          <Tooltip title={record.is_active ? 'Deactivate' : 'Activate'}>
            <Button
              size="small"
              icon={record.is_active ? <StopOutlined /> : <CheckCircleOutlined />}
              onClick={() => handleToggleActive(record)}
              danger={record.is_active}
            />
          </Tooltip>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: '24px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>User Management</Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => fetchUsers()} loading={loading}>Refresh</Button>
          <Button type="primary" icon={<UserAddOutlined />} onClick={() => setCreateOpen(true)}>
            New User
          </Button>
        </Space>
      </div>

      <Table
        dataSource={users}
        columns={columns}
        rowKey="id"
        loading={loading}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        size="small"
      />

      {/* ── Create User Modal ── */}
      <Modal
        title="Create New User"
        open={createOpen}
        onCancel={() => { setCreateOpen(false); createForm.resetFields(); }}
        onOk={() => createForm.submit()}
        confirmLoading={creating}
        okText="Create"
      >
        <Form form={createForm} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="Full Name" rules={[{ required: true }]}>
            <Input placeholder="Jane Smith" />
          </Form.Item>
          <Form.Item name="email" label="Email" rules={[{ required: true, type: 'email' }]}>
            <Input placeholder="jane@company.com" />
          </Form.Item>
          <Form.Item name="role" label="Role" initialValue="developer">
            <Select options={[
              { value: 'admin', label: 'Admin' },
              { value: 'operator', label: 'Operator' },
              { value: 'developer', label: 'Developer' },
              { value: 'viewer', label: 'Viewer' },
            ]} />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="rate_limit" label="Rate Limit (req/min)" initialValue={60}>
                <InputNumber min={1} max={10000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="monthly_token_limit" label="Monthly Token Limit" initialValue={0}
                help="0 = unlimited">
                <InputNumber min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>

      {/* ── Edit User Modal ── */}
      <Modal
        title={`Edit: ${editTarget?.name}`}
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => editForm.submit()}
        confirmLoading={editing}
        okText="Save"
      >
        <Form form={editForm} layout="vertical" onFinish={handleEdit}>
          <Form.Item name="name" label="Full Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="role" label="Role">
            <Select options={[
              { value: 'admin', label: 'Admin' },
              { value: 'operator', label: 'Operator' },
              { value: 'developer', label: 'Developer' },
              { value: 'viewer', label: 'Viewer' },
            ]} />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="rate_limit" label="Rate Limit (req/min)">
                <InputNumber min={1} max={10000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="monthly_token_limit" label="Monthly Token Limit" help="0 = unlimited">
                <InputNumber min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>

      {/* ── Create API Key Modal ── */}
      <Modal
        title={newKey ? 'API Key Created' : `New API Key for ${keyTarget?.name}`}
        open={keyModalOpen}
        onCancel={() => { setKeyModalOpen(false); setNewKey(null); keyForm.resetFields(); fetchUsers(); }}
        footer={newKey
          ? [<Button key="close" type="primary" onClick={() => {
              setKeyModalOpen(false); setNewKey(null); keyForm.resetFields();
            }}>Done</Button>]
          : undefined
        }
        onOk={newKey ? undefined : () => keyForm.submit()}
        confirmLoading={creatingKey}
        okText="Generate Key"
      >
        {newKey ? (
          <div>
            <Alert
              type="warning"
              showIcon
              message="Save this key now — it will not be shown again."
              style={{ marginBottom: 16 }}
            />
            <Input.Group compact style={{ display: 'flex' }}>
              <Input value={newKey.api_key} readOnly style={{ fontFamily: 'monospace', flex: 1 }} />
              <Button
                icon={<CopyOutlined />}
                onClick={() => {
                  navigator.clipboard.writeText(newKey.api_key);
                  antMessage.success('Copied to clipboard');
                }}
              >
                Copy
              </Button>
            </Input.Group>
            <Divider />
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="Name">{newKey.name}</Descriptions.Item>
              <Descriptions.Item label="Role">{newKey.role}</Descriptions.Item>
              <Descriptions.Item label="Rate Limit">{newKey.rate_limit} rpm</Descriptions.Item>
              <Descriptions.Item label="Prefix">{newKey.key_prefix}</Descriptions.Item>
            </Descriptions>
          </div>
        ) : (
          <Form form={keyForm} layout="vertical" onFinish={handleCreateKey}>
            <Form.Item name="name" label="Key Name" rules={[{ required: true }]}
              help="A descriptive name, e.g. 'Cursor IDE' or 'CI/CD pipeline'">
              <Input placeholder="Cursor IDE" />
            </Form.Item>
            <Form.Item name="rate_limit" label="Rate Limit Override (req/min)"
              help="Leave blank to inherit user's default">
              <InputNumber min={1} max={10000} style={{ width: '100%' }} placeholder="Inherit from user" />
            </Form.Item>
          </Form>
        )}
      </Modal>

      {/* ── User Detail Drawer ── */}
      <Drawer
        title={drawerUser ? `${drawerUser.name} — Details` : 'User Details'}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={700}
      >
        {drawerUser && (
          <>
            <Descriptions bordered size="small" column={2} style={{ marginBottom: 24 }}>
              <Descriptions.Item label="Email" span={2}>{drawerUser.email}</Descriptions.Item>
              <Descriptions.Item label="Role">
                <Tag color={drawerUser.role === 'admin' ? 'red' : 'blue'}>{drawerUser.role}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="Status">
                <Badge
                  status={drawerUser.is_active ? 'success' : 'error'}
                  text={drawerUser.is_active ? 'Active' : 'Inactive'}
                />
              </Descriptions.Item>
              <Descriptions.Item label="Rate Limit">{drawerUser.rate_limit} rpm</Descriptions.Item>
              <Descriptions.Item label="Token Limit">
                {drawerUser.monthly_token_limit === 0 ? 'Unlimited' : drawerUser.monthly_token_limit.toLocaleString()}
              </Descriptions.Item>
              <Descriptions.Item label="Created" span={2}>
                {new Date(drawerUser.created_at).toLocaleString()}
              </Descriptions.Item>
            </Descriptions>

            {/* Usage stats */}
            <Title level={5}>Usage</Title>
            <Row gutter={16} style={{ marginBottom: 24 }}>
              <Col span={6}>
                <Card size="small">
                  <Statistic title="Requests" value={usageData?.aggregate?.total_requests ?? drawerUser.usage.total_requests} loading={usageLoading} />
                </Card>
              </Col>
              <Col span={6}>
                <Card size="small">
                  <Statistic title="Prompt Tokens" value={usageData?.aggregate?.prompt_tokens ?? drawerUser.usage.prompt_tokens} loading={usageLoading} />
                </Card>
              </Col>
              <Col span={6}>
                <Card size="small">
                  <Statistic title="Output Tokens" value={usageData?.aggregate?.completion_tokens ?? drawerUser.usage.completion_tokens} loading={usageLoading} />
                </Card>
              </Col>
              <Col span={6}>
                <Card size="small">
                  <Statistic title="Cost (USD)" value={`$${(usageData?.aggregate?.cost_usd ?? drawerUser.usage.cost_usd).toFixed(4)}`} loading={usageLoading} />
                </Card>
              </Col>
            </Row>

            {/* Keys table */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <Title level={5} style={{ margin: 0 }}>API Keys</Title>
              <Button size="small" icon={<PlusOutlined />} type="primary"
                onClick={() => { setDrawerOpen(false); openKeyModal(drawerUser); }}
                disabled={!drawerUser.is_active}>
                New Key
              </Button>
            </div>
            <Table
              dataSource={usageData?.per_key ?? []}
              loading={usageLoading}
              rowKey="id"
              size="small"
              pagination={false}
              columns={[
                {
                  title: 'Name',
                  dataIndex: 'name',
                  key: 'name',
                  render: (name: string, rec: ApiKeyRecord) => (
                    <Space>
                      <Badge status={rec.is_active ? 'success' : 'error'} />
                      <Text>{name}</Text>
                    </Space>
                  ),
                },
                {
                  title: 'Prefix',
                  dataIndex: 'key_prefix',
                  key: 'key_prefix',
                  render: (v: string) => <Text code style={{ fontSize: 11 }}>{v}</Text>,
                },
                {
                  title: 'Requests',
                  key: 'usage',
                  render: (_: any, rec: ApiKeyRecord) => rec.usage?.total_requests ?? 0,
                },
                {
                  title: 'Last Used',
                  dataIndex: 'last_used_at',
                  key: 'last_used_at',
                  render: (v: string | null) => v
                    ? <Text type="secondary" style={{ fontSize: 11 }}>{new Date(v).toLocaleString()}</Text>
                    : <Text type="secondary" style={{ fontSize: 11 }}>Never</Text>,
                },
                {
                  title: 'Active',
                  dataIndex: 'is_active',
                  key: 'is_active',
                  render: (v: boolean, rec: ApiKeyRecord) => (
                    <Switch checked={v} size="small"
                      onChange={() => handleToggleKey(drawerUser.id, rec.id, v)} />
                  ),
                },
                {
                  title: '',
                  key: 'actions',
                  render: (_: any, rec: ApiKeyRecord) => (
                    <Popconfirm title="Revoke this key?" onConfirm={() => handleRevokeKey(drawerUser.id, rec.id)}>
                      <Button size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  ),
                },
              ]}
            />

            {/* Recent requests */}
            {usageData?.recent_requests?.length > 0 && (
              <>
                <Title level={5} style={{ marginTop: 24 }}>Recent Requests (last 20)</Title>
                <Table
                  dataSource={usageData.recent_requests}
                  rowKey={(r: any) => r.created_at}
                  size="small"
                  pagination={false}
                  columns={[
                    { title: 'Model', dataIndex: 'model', key: 'model', render: (v: string) => <Text code style={{ fontSize: 11 }}>{v}</Text> },
                    { title: 'Tokens', key: 'tokens', render: (_: any, r: any) => `${r.prompt_tokens} / ${r.completion_tokens}` },
                    { title: 'Cost', dataIndex: 'cost_usd', key: 'cost', render: (v: number) => `$${v.toFixed(5)}` },
                    { title: 'Latency', dataIndex: 'latency_ms', key: 'latency', render: (v: number) => v ? `${v}ms` : '—' },
                    { title: 'Time', dataIndex: 'created_at', key: 'created_at', render: (v: string) => <Text style={{ fontSize: 11 }}>{new Date(v).toLocaleString()}</Text> },
                  ]}
                />
              </>
            )}
          </>
        )}
      </Drawer>
    </div>
  );
}
