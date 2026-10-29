import { Outlet } from "react-router-dom";
import { Layout } from "antd";
import { AuthGuard } from "./AuthGuard";

const { Content } = Layout;

export function AuthLayout() {
  return (
    <AuthGuard>
      <Outlet />
    </AuthGuard>
  );
}

export function AuthPageShell() {
  return (
    <Layout style={{ minHeight: "100vh", overflow: "hidden" }}>
      <Content>
        <Outlet />
      </Content>
    </Layout>
  );
}
