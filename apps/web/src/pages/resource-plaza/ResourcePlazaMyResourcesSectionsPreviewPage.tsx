import { Navigate } from "react-router-dom";

/** 分区预览已合入正式页，保留路由以免旧链接 404。 */
export default function ResourcePlazaMyResourcesSectionsPreviewPage() {
  return <Navigate to="/resources/mine" replace />;
}
