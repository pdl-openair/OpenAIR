import { createBrowserRouter, Navigate, useParams, useSearchParams } from "react-router-dom";
import AppLayout from "../components/AppLayout";
import { AdminGuard } from "../components/AdminGuard";
import { AuthLayout, AuthPageShell } from "../components/RouteLayouts";
import AdminQuotasPage from "../pages/AdminQuotasPage";
import ArtifactLibraryPage from "../pages/ArtifactLibraryPage";
import CommunityPage from "../pages/CommunityPage";
import CommunityDetailPage from "../pages/CommunityDetailPage";
import HomePage from "../pages/HomePage";
import ResearchStartWaitingPage from "../pages/ResearchStartWaitingPage";
import AdminGovernancePage from "../pages/AdminGovernancePage";
import ForgotPasswordPage from "../pages/ForgotPasswordPage";
import LoginPage from "../pages/LoginPage";
import OAuthCallbackPage from "../pages/OAuthCallbackPage";
import RegisterPage from "../pages/RegisterPage";
import ResetPasswordPage from "../pages/ResetPasswordPage";
import SettingsPage from "../pages/SettingsPage";
import StarterSamplePage from "../pages/StarterSamplePage";
import TaskBoardPage from "../pages/TaskBoardPage";
import TaskDetailPage from "../pages/TaskDetailPage";
import WorkbenchPage from "../pages/WorkbenchPage";
import NotFoundPage from "../pages/NotFoundPage";
import AgentSessionPage from "../pages/AgentSessionPage";
import AgentSessionHistoryPage from "../pages/AgentSessionHistoryPage";
import {
  ComputeResourcesPage,
  EquipmentComputePlatformPage,
  EquipmentResourcesPage,
  ResourcePlazaCatalogBPreviewPage,
  ResourcePlazaCatalogPage,
  ResourcePlazaDetailHandoffPage,
  ResourcePlazaFeaturedPreviewPage,
  ResourcePlazaHomePage,
  ResourcePlazaLayout,
  ResourcePlazaMyResourcesPage,
  ResourcePlazaMyResourcesSectionsPreviewPage,
} from "../pages/resource-plaza";
import "../router/types";

/** 旧「设备算力」详情链接重定向：jcc 归算力资源，其余归设备资源。 */
function LegacyEquipmentComputePlatformRedirect() {
  const { platformId } = useParams();
  const [params] = useSearchParams();
  const tab = params.get("tab");
  const target =
    platformId === "jcc"
      ? `/resources/compute/platforms/jcc`
      : "/resources/equipment";
  const suffix = tab ? `?tab=${encodeURIComponent(tab)}` : "";
  return <Navigate to={`${target}${suffix}`} replace />;
}

export const router = createBrowserRouter([
  {
    element: <AuthPageShell />,
    children: [
      { path: "/login", element: <LoginPage /> },
      { path: "/register", element: <RegisterPage /> },
      { path: "/forgot-password", element: <ForgotPasswordPage /> },
      { path: "/reset-password", element: <ResetPasswordPage /> },
      { path: "/auth/oauth/callback", element: <OAuthCallbackPage /> },
    ]
  },
  {
    path: "/",
    element: <AppLayout />,
    children: [
      {
        element: <AuthLayout />,
        children: [
          {
            index: true,
            element: <HomePage />,
            handle: { hideBreadcrumb: true }
          },
          {
            path: "research-start/waiting",
            element: <ResearchStartWaitingPage />,
            handle: { hideBreadcrumb: true },
          },
          {
            path: "research-start/waiting/:sessionId",
            element: <ResearchStartWaitingPage />,
            handle: { hideBreadcrumb: true },
          },
          {
            path: "agent-sessions",
            element: <AgentSessionHistoryPage />,
            handle: {
              breadcrumbTitle: "历史对话",
            },
          },
          {
            path: "agent-sessions/:sessionId",
            element: <AgentSessionPage />,
            handle: {
              breadcrumbTitle: "智能体会话",
              hideBreadcrumb: true,
              hideHeader: true,
            },
          },
          {
            path: "starters",
            element: <Navigate to="/resources" replace />,
          },
          {
            path: "resources",
            element: <ResourcePlazaLayout />,
            handle: {
              breadcrumbTitle: "资源广场",
              breadcrumbLink: "/resources",
            },
            children: [
              {
                index: true,
                element: <ResourcePlazaHomePage />,
              },
              {
                path: "preview-featured",
                element: <ResourcePlazaFeaturedPreviewPage />,
                handle: { breadcrumbTitle: "精选预览" },
              },
              {
                path: "preview-catalog-b",
                element: <ResourcePlazaCatalogBPreviewPage />,
                handle: { breadcrumbTitle: "目录方案 B 预览" },
              },
              {
                path: "preview-mine-sections",
                element: <ResourcePlazaMyResourcesSectionsPreviewPage />,
                handle: { breadcrumbTitle: "我的资源分区预览" },
              },
              {
                path: "catalog",
                element: <ResourcePlazaCatalogPage />,
                handle: { breadcrumbTitle: "公共资源" },
              },
              {
                path: "catalog/:resourceId",
                element: <ResourcePlazaDetailHandoffPage />,
                handle: { breadcrumbTitle: "资源详情" },
              },
              {
                path: "mine",
                element: <ResourcePlazaMyResourcesPage />,
                handle: { breadcrumbTitle: "我的资源" },
              },
              {
                path: "equipment",
                element: <EquipmentResourcesPage />,
                handle: { breadcrumbTitle: "设备资源" },
              },
              {
                path: "equipment/platforms/:platformId",
                element: <EquipmentComputePlatformPage />,
                handle: { breadcrumbTitle: "平台详情" },
              },
              {
                path: "compute",
                element: <ComputeResourcesPage />,
                handle: { breadcrumbTitle: "算力资源" },
              },
              {
                path: "compute/platforms/:platformId",
                element: <EquipmentComputePlatformPage />,
                handle: { breadcrumbTitle: "平台详情" },
              },
              {
                path: "equipment-compute",
                element: <Navigate to="/resources/equipment" replace />,
              },
              {
                path: "equipment-compute/platforms/:platformId",
                element: <LegacyEquipmentComputePlatformRedirect />,
              },
            ],
          },
          {
            path: "workbench",
            handle: {
              breadcrumbTitle: "项目看板",
              breadcrumbLink: "/workbench",
            },
            children: [
              {
                index: true,
                element: <TaskBoardPage />,
              },
              {
                // Intermediate segment so workbench breadcrumbs keep 项目详情:
                // 首页 / 项目看板 / 项目详情 / 工作台
                path: ":taskId",
                handle: {
                  breadcrumbTitle: "项目详情",
                  breadcrumbLink: "/tasks/:taskId",
                },
                children: [
                  {
                    index: true,
                    element: <WorkbenchPage />,
                    handle: { breadcrumbTitle: "工作台" },
                  },
                ],
              },
            ],
          },
          {
            path: "artifacts",
            element: <ArtifactLibraryPage />,
            handle: { breadcrumbTitle: "产物库" }
          },
          {
            path: "community",
            handle: {
              breadcrumbTitle: "研究社区",
              breadcrumbLink: "/community",
              // On the detail sub-page, append the publication title as a new
              // trailing crumb instead of replacing 研究社区.
              breadcrumbAppendTitle: true,
            },
            children: [
              {
                index: true,
                element: <CommunityPage />,
              },
              {
                // Detail: 首页 / 研究社区 / <publication title (via usePageHeader)>.
                path: ":publicationId",
                element: <CommunityDetailPage />,
              },
            ],
          },
          {
            path: "settings",
            element: <SettingsPage />,
            handle: { breadcrumbTitle: "设置" }
          },
          {
            path: "admin/quotas",
            element: (
              <AdminGuard>
                <AdminQuotasPage />
              </AdminGuard>
            ),
            handle: { breadcrumbTitle: "额度管理" }
          },
          {
            path: "admin/governance",
            element: (
              <AdminGuard>
                <AdminGovernancePage />
              </AdminGuard>
            ),
            handle: { breadcrumbTitle: "用户与治理" }
          },
          {
            path: "tasks",
            handle: {
              breadcrumbTitle: "项目看板",
              breadcrumbLink: "/workbench"
            },
            children: [
              {
                path: ":taskId",
                element: <TaskDetailPage />,
                handle: { breadcrumbTitle: "项目详情" },
              }
            ]
          },
          {
            path: "starter-samples",
            handle: {
              breadcrumbTitle: "起点广场",
              breadcrumbLink: "/resources"
            },
            children: [
              {
                path: ":starterId",
                element: <StarterSamplePage />,
                handle: { breadcrumbTitle: "样例任务" }
              }
            ]
          },
          {
            path: "*",
            element: <NotFoundPage />,
            handle: { hideHeader: true, hideSider: true }
          }
        ]
      }
    ]
  }
]);
