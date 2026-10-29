import { Navigate } from "react-router-dom";

/** 预览路由已并入正式精选页，保留路径以免旧链接失效。 */
export default function ResourcePlazaFeaturedPreviewPage() {
  return <Navigate to="/resources" replace />;
}
