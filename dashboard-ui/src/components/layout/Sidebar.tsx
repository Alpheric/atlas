import { Menu } from 'antd';
import {
  DashboardOutlined,
  MessageOutlined,
  BranchesOutlined,
  CloudServerOutlined,
  ExperimentOutlined,
  BarChartOutlined,
  ImportOutlined,
  SettingOutlined,
  TeamOutlined,
  AppstoreOutlined,
  CodeOutlined,
  UserOutlined,
  MedicineBoxOutlined,
} from '@ant-design/icons';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuthStore } from '../../stores/authStore';
import { ROLE_PAGE_ACCESS } from '../../lib/constants';

const allItems = [
  { key: '/', icon: <DashboardOutlined />, label: 'Overview' },
  { key: '/conversations', icon: <MessageOutlined />, label: 'Conversations' },
  { key: '/healing', icon: <MedicineBoxOutlined />, label: 'Self-Heal' },
  { key: '/routing', icon: <BranchesOutlined />, label: 'Routing' },
  { key: '/providers', icon: <CloudServerOutlined />, label: 'Providers' },
  { key: '/users', icon: <UserOutlined />, label: 'Users' },
  { key: '/accounts', icon: <TeamOutlined />, label: 'Accounts' },
  { key: '/models', icon: <AppstoreOutlined />, label: 'Models' },
  { key: '/playground', icon: <CodeOutlined />, label: 'Playground' },
  { key: '/training', icon: <ExperimentOutlined />, label: 'Training' },
  { key: '/analytics', icon: <BarChartOutlined />, label: 'Analytics' },
  { key: '/import', icon: <ImportOutlined />, label: 'Import' },
  { key: '/settings', icon: <SettingOutlined />, label: 'Settings' },
];

export default function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const user = useAuthStore((s) => s.user);

  // Filter menu items by role
  const items = allItems.filter((item) => {
    const allowedRoles = ROLE_PAGE_ACCESS[item.key];
    if (!allowedRoles || !user) return true; // show all if no auth
    return allowedRoles.includes(user.role);
  });

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '20px 24px 16px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <DashboardOutlined style={{ fontSize: 22, color: '#3b82f6' }} />
          <span style={{ fontSize: 18, fontWeight: 700, color: '#fff' }}>Alpheric.AI</span>
        </div>
        <div style={{ fontSize: 11, color: '#6b7280', marginTop: 4 }}>Atlas Model Family</div>
      </div>

      <Menu
        mode="inline"
        selectedKeys={[location.pathname]}
        items={items}
        onClick={({ key }) => navigate(key)}
        style={{ flex: 1, borderRight: 0 }}
      />

      <div style={{ padding: '12px 24px', borderTop: '1px solid rgba(255,255,255,0.06)', fontSize: 11, color: '#4b5563' }}>
        v0.2.0 — Enterprise
      </div>
    </div>
  );
}
