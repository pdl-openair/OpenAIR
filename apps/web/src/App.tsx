import { RouterProvider } from "react-router-dom";
import { App as AntdApp } from "antd";
import { AuthProvider } from "./context/AuthContext";
import { PageHeaderProvider } from "./context/PageHeaderContext";
import { router } from "./router/routes";

export default function App() {
  return (
    <AntdApp>
      <AuthProvider>
        <PageHeaderProvider>
          <RouterProvider router={router} />
        </PageHeaderProvider>
      </AuthProvider>
    </AntdApp>
  );
}
