import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Alert, Button, Card, Spin, Typography } from "antd";
import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { ApiError } from "../api/http";
import { fetchResearchStartSession, type ResearchStartSessionStatus } from "../api/client";
import { usePageHeader } from "../context/PageHeaderContext";
import { useUserNoticeOptional } from "../context/UserNoticeContext";
import {
  formatElapsedSeconds,
  projectResearchStartWaitingView,
} from "../lib/researchStartWaiting";
import {
  clearResearchStartPending,
  readResearchStartPendingForSession,
  rememberCurrentTask,
} from "../lib/userRouteState";

export { formatElapsedSeconds, projectResearchStartWaitingView } from "../lib/researchStartWaiting";
export type { ResearchStartWaitingView } from "../lib/researchStartWaiting";

const BASE_RETRY_MS = 1_500;
const MAX_RETRY_MS = 20_000;

function retryDelay(failures: number, retryAfterMs?: number): number {
  const exponential = Math.min(MAX_RETRY_MS, BASE_RETRY_MS * 2 ** Math.min(failures, 4));
  return Math.max(exponential, retryAfterMs ?? 0);
}

function outcomeDestination(taskId: string, sessionId: string): string {
  const query = new URLSearchParams({ planningSessionId: sessionId });
  return `/workbench/${encodeURIComponent(taskId)}?${query.toString()}`;
}

export default function ResearchStartWaitingPage() {
  const navigate = useNavigate();
  const { sessionId } = useParams<{ sessionId: string }>();
  const notices = useUserNoticeOptional();
  const acceptedIdentity = useMemo(() => readResearchStartPendingForSession(sessionId), [sessionId]);
  const [snapshot, setSnapshot] = useState<ResearchStartSessionStatus | null>(null);
  const [readUnavailable, setReadUnavailable] = useState(false);
  const [fatalError, setFatalError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [manualCheckSequence, setManualCheckSequence] = useState(0);

  usePageHeader({ action: null });

  const waitingView = projectResearchStartWaitingView(snapshot, readUnavailable);
  const elapsedLabel = formatElapsedSeconds(waitingView.elapsedSeconds);
  const returnHome = () => navigate("/", { replace: true });
  const checkAgain = useCallback(() => {
    setFatalError(null);
    setReadUnavailable(false);
    setManualCheckSequence((value) => value + 1);
  }, []);

  useEffect(() => {
    if (!sessionId) {
      setFatalError("未找到当前研究会话，请返回研究起点重新提交。");
      return;
    }

    let disposed = false;
    let timer: number | null = null;
    let requestInFlight = false;
    let consecutiveFailures = 0;

    const clearTimer = () => {
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
    };
    const schedule = (delay: number) => {
      clearTimer();
      if (!disposed && document.visibilityState !== "hidden") {
        timer = window.setTimeout(() => void reconcile(), delay);
      }
    };
    const reconcile = async () => {
      if (disposed || requestInFlight || document.visibilityState === "hidden") return;
      requestInFlight = true;
      setChecking(true);
      try {
        const next = await fetchResearchStartSession(sessionId);
        if (disposed) return;
        if (next.sessionId !== sessionId) {
          setFatalError("读取到的研究会话与当前页面不一致，已停止打开任务。");
          return;
        }

        const acceptedTaskId = acceptedIdentity?.reservedTaskId?.trim() || null;
        const serverReservedTaskId = next.reservedTaskId?.trim() || null;
        if (acceptedTaskId && serverReservedTaskId && acceptedTaskId !== serverReservedTaskId) {
          setFatalError("当前研究会话返回了不一致的任务身份，已停止打开任务。");
          return;
        }

        setSnapshot(next);
        setReadUnavailable(false);
        consecutiveFailures = 0;
        const view = projectResearchStartWaitingView(next);
        notices?.upsertNotice({
          id: `research-start:${sessionId}`,
          source: "research-start",
          title: view.title,
          message: view.message,
          level: view.level,
          status: view.shouldOpenWorkbench ? "done" : view.level === "error" ? "failed" : "active",
          elapsedSeconds: view.elapsedSeconds,
          nextActionHint: view.nextActionHint,
          openPanel: view.level === "error",
        });
        if (next.status === "completed") {
          const completedTaskId = next.result?.taskId?.trim() || null;
          if (!completedTaskId) {
            if (view.shouldReturnHome) {
              clearResearchStartPending();
              setFatalError(view.message);
            } else {
              setFatalError("当前研究会话未返回可打开的任务，请返回研究起点。");
            }
            return;
          }
          const expectedTaskId = serverReservedTaskId ?? acceptedTaskId;
          if (expectedTaskId && completedTaskId !== expectedTaskId) {
            setFatalError("完成结果与当前研究会话预留的任务不一致，已停止打开任务。");
            return;
          }
          rememberCurrentTask(completedTaskId);
          clearResearchStartPending();
          navigate(outcomeDestination(completedTaskId, sessionId), { replace: true });
          return;
        }
        if (next.status === "failed") {
          setFatalError(view.message);
          return;
        }
        if (next.status === "external_result_unknown") {
          // Do not retry the creation request; only re-read this exact session at a low cadence.
          schedule(MAX_RETRY_MS);
          return;
        }
        schedule(BASE_RETRY_MS);
      } catch (error) {
        if (disposed) return;
        consecutiveFailures += 1;
        setReadUnavailable(true);
        notices?.upsertNotice({
          id: `research-start:${sessionId}`,
          source: "research-start",
          title: "任务准备状态",
          message: "暂时无法读取最新状态，系统会按当前会话继续确认。",
          level: "warning",
          status: "active",
          nextActionHint: "请保持当前页面，或稍后在系统提示中查看结果。",
        });
        const retryAfterMs = error instanceof ApiError ? error.retryAfterMs : undefined;
        schedule(retryDelay(consecutiveFailures, retryAfterMs));
      } finally {
        requestInFlight = false;
        if (!disposed) setChecking(false);
      }
    };

    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        clearTimer();
      } else {
        void reconcile();
      }
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    void reconcile();
    return () => {
      disposed = true;
      clearTimer();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [acceptedIdentity?.reservedTaskId, manualCheckSequence, navigate, sessionId]);

  if (fatalError) {
    const alertType = waitingView.shouldReturnHome ? "success" : "error";
    return (
      <main className="flex min-h-full w-full items-center justify-center bg-shell-bg px-6 py-10">
        <Card className="w-full max-w-[600px] border-border shadow-sm">
          <Alert type={alertType} showIcon title={waitingView.title} description={fatalError} />
          <div className="mt-5 flex justify-end gap-2">
            <Button type="primary" icon={<ArrowLeftOutlined />} onClick={returnHome}>返回研究起点</Button>
          </div>
        </Card>
      </main>
    );
  }

  return (
    <main className="flex min-h-full w-full items-center justify-center bg-shell-bg px-6 py-10">
      <Card className="w-full max-w-[640px] border-border shadow-sm" data-testid="research-start-waiting-page">
        <div className="flex min-h-[240px] flex-col items-center justify-center text-center">
          {waitingView.unknown ? null : <Spin size="large" />}
          <Typography.Title level={3} className="mb-0! mt-5! text-shell-text!">{waitingView.title}</Typography.Title>
          <Typography.Paragraph className="mb-0! mt-2! text-text-secondary">
            {waitingView.message}
          </Typography.Paragraph>
          {elapsedLabel ? (
            <Typography.Paragraph className="mb-0! mt-1! text-text-secondary">
              {elapsedLabel}
            </Typography.Paragraph>
          ) : null}
          {waitingView.nextActionHint ? (
            <Typography.Paragraph className="mb-0! mt-2! text-text-secondary">
              {waitingView.nextActionHint}
            </Typography.Paragraph>
          ) : null}
          {readUnavailable || waitingView.unknown ? (
            <Button className="mt-5" icon={<ReloadOutlined />} loading={checking} onClick={checkAgain}>
              继续检查当前研究
            </Button>
          ) : null}
        </div>
      </Card>
    </main>
  );
}
