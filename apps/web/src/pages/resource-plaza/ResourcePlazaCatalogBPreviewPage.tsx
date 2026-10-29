import { Navigate } from "react-router-dom";

/** 预览路由已并入正式公共资源页，保留路径以免旧链接失效。 */
export default function ResourcePlazaCatalogBPreviewPage() {
  return <Navigate to="/resources/catalog" replace />;
}
