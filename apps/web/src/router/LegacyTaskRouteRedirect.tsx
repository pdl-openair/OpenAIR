import { Navigate, useLocation, useParams } from "react-router-dom";

export function LegacyTaskRouteRedirect() {
  const { taskId } = useParams<{ taskId: string }>();
  const { search } = useLocation();

  if (!taskId) {
    return <Navigate to="/workbench" replace />;
  }

  return (
    <Navigate
      to={{
        pathname: `/workbench/${encodeURIComponent(taskId)}`,
        search,
      }}
      replace
    />
  );
}
