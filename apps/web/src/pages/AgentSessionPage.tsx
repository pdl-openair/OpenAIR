import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Button, Input, Spin, Typography, message } from "antd";
import { ArrowUpOutlined } from "@ant-design/icons";
import { ArrowLeft } from "lucide-react";
import { STAGE_LABELS, AGENT_SUPPORTED_ROLE_LABELS, labelUnknown, type TaskStage } from "@openair/shared";
import { StreamdownMarkdown } from "../components/artifacts/StreamdownMarkdown";
import { projectStructuredArtifactMarkdown } from "../components/artifacts/ArtifactRenderer";
import { labelForSubStage } from "../data/recordedRunAdapter";
import {
  fetchAgentSession,
  postAgentSessionMessage,
  downloadAgentSessionArtifact,
  downloadAgentSessionArtifactsZip,
  type AgentSession,
  type AgentSessionArtifact,
  type AgentSessionMessage,
  type AgentSessionStep,
} from "../features/agent-session/api";

const CHAT_WIDTH_MIN = 260;
const CHAT_WIDTH_MAX = 560;
const CHAT_WIDTH_DEFAULT = 340;

function clampChatWidth(value: number): number {
  return Math.min(CHAT_WIDTH_MAX, Math.max(CHAT_WIDTH_MIN, Math.round(value)));
}

function resolveSessionBackNav(from: string | null): { label: string; to: string } {
  if (from === "resource") {
    return { label: "返回资源广场", to: "/resources" };
  }
  // history entry, or direct link / missing param → prefer history list
  return { label: "返回历史对话", to: "/agent-sessions" };
}

function statusLabel(status: string, sending: boolean): string {
  if (sending || status === "running") return "执行中";
  if (status === "failed") return "本轮失败";
  if (status === "completed") return "已完成";
  if (status === "waiting_input") return "等待输入";
  if (status === "ready") return "就绪";
  return status;
}

function stageLabel(stage: string): string {
  if (stage in STAGE_LABELS) return STAGE_LABELS[stage as TaskStage];
  return labelForSubStage(stage);
}

function groupSteps(steps: AgentSessionStep[]): Array<{ stage: string; steps: AgentSessionStep[] }> {
  const order: string[] = [];
  const map = new Map<string, AgentSessionStep[]>();
  for (const step of steps) {
    if (!map.has(step.stage)) {
      order.push(step.stage);
      map.set(step.stage, []);
    }
    map.get(step.stage)!.push(step);
  }
  return order.map(stage => ({ stage, steps: map.get(stage) ?? [] }));
}

function ArtifactCard({
  artifact,
  active,
  sessionId,
}: {
  artifact: AgentSessionArtifact;
  active: boolean;
  sessionId: string;
}) {
  const [downloading, setDownloading] = useState(false);
  const bodyJson = (() => {
    try {
      return JSON.stringify(artifact.body ?? {});
    } catch {
      return "";
    }
  })();
  const markdown = bodyJson ? projectStructuredArtifactMarkdown(bodyJson, artifact.title) : null;
  const rawFallback = (() => {
    try {
      return JSON.stringify(artifact.body ?? {}, null, 2);
    } catch {
      return String(artifact.body ?? "");
    }
  })();

  const onDownload = async () => {
    if (downloading) return;
    setDownloading(true);
    try {
      await downloadAgentSessionArtifact(sessionId, artifact.artifactId);
    } catch {
      message.error("下载失败，请稍后重试。");
    } finally {
      setDownloading(false);
    }
  };

  return (
    <article
      className={`rounded-[12px] border px-4 py-3 ${active ? "border-primary bg-surface" : "border-border bg-surface-soft"}`}
      data-testid="agent-session-artifact"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="m-0 text-[15px] font-semibold text-text">{artifact.title}</h3>
          {artifact.artifactContract ? (
            <p className="mb-0 mt-1 text-xs text-text-muted">{artifact.artifactContract}</p>
          ) : null}
        </div>
        {artifact.downloadAvailable !== false ? (
          <Button
            size="small"
            loading={downloading}
            onClick={() => void onDownload()}
            data-testid="agent-session-artifact-download"
          >
            下载
          </Button>
        ) : null}
      </div>
      {!markdown && artifact.summary ? (
        <p className="mb-0 mt-2 whitespace-pre-wrap text-[13px] leading-6 text-text-secondary">{artifact.summary}</p>
      ) : null}
      <div className="mt-3 max-h-[min(70vh,720px)] overflow-auto rounded-lg bg-surface-soft p-3 agent-session-scroll">
        {markdown ? (
          <StreamdownMarkdown content={markdown} testId="agent-session-artifact-markdown" />
        ) : (
          <pre className="m-0 text-[11px] leading-5 text-text-secondary">{rawFallback}</pre>
        )}
      </div>
    </article>
  );
}

export default function AgentSessionPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { sessionId = "" } = useParams();
  const backNav = resolveSessionBackNav(searchParams.get("from"));
  const [session, setSession] = useState<AgentSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [zipping, setZipping] = useState(false);
  const [chatWidth, setChatWidth] = useState(CHAT_WIDTH_DEFAULT);
  const chatEndRef = useRef<HTMLDivElement | null>(null);
  const chatDragRef = useRef<null | { startX: number; startWidth: number }>(null);

  useEffect(() => {
    if (!sessionId.trim()) {
      setFailed(true);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setFailed(false);
    fetchAgentSession(sessionId, controller.signal)
      .then(setSession)
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [sessionId]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [session?.messages.length, sending]);

  useEffect(() => {
    if (!sessionId || session?.status !== "running" || sending) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      try {
        const latest = await fetchAgentSession(sessionId);
        if (cancelled) return;
        setSession(latest);
        if (latest.status === "running") timer = setTimeout(() => void poll(), 1000);
      } catch {
        if (!cancelled) timer = setTimeout(() => void poll(), 2000);
      }
    };
    timer = setTimeout(() => void poll(), 500);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [sending, session?.status, sessionId]);

  useEffect(() => {
    const onMove = (event: MouseEvent) => {
      const drag = chatDragRef.current;
      if (!drag) return;
      // Dragging the handle left widens the chat panel.
      const next = clampChatWidth(drag.startWidth + (drag.startX - event.clientX));
      setChatWidth(next);
    };
    const onUp = () => {
      if (!chatDragRef.current) return;
      chatDragRef.current = null;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  const startChatResize = (event: ReactMouseEvent) => {
    event.preventDefault();
    chatDragRef.current = { startX: event.clientX, startWidth: chatWidth };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  const grouped = useMemo(() => groupSteps(session?.steps ?? []), [session?.steps]);
  const selectedArtifactId = session?.artifacts.length
    ? session.artifacts[session.artifacts.length - 1]?.artifactId
    : null;
  const chatBusy = sending || session?.status === "running";
  const isSkillSession = session?.sessionKind === "skill";

  const send = async () => {
    const content = draft.trim();
    if (!content || !sessionId || sending || !session) return;
    const optimisticUser: AgentSessionMessage = {
      messageId: `local_user_${Date.now()}`,
      role: "user",
      content,
      createdAt: new Date().toISOString(),
    };
    setDraft("");
    setSending(true);
    setSession({
      ...session,
      status: "running",
      messages: [...session.messages, optimisticUser],
      updatedAt: new Date().toISOString(),
    });
    try {
      const next = await postAgentSessionMessage(sessionId, content);
      setSession(next);
    } catch {
      setSession(current =>
        current
          ? {
              ...current,
              status: current.status === "running" ? "waiting_input" : current.status,
            }
          : current,
      );
      message.error("发送失败，请稍后重试。");
    } finally {
      setSending(false);
    }
  };

  const downloadZip = async () => {
    if (!sessionId || zipping || !session?.artifacts.length) return;
    setZipping(true);
    try {
      await downloadAgentSessionArtifactsZip(sessionId);
    } catch {
      message.error("打包下载失败，请稍后重试。");
    } finally {
      setZipping(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center" data-testid="agent-session-loading">
        <Spin />
      </div>
    );
  }

  if (failed || !session) {
    return (
      <div className="flex h-full w-full flex-col bg-bg" data-testid="agent-session-failed">
        <main className="flex min-h-0 flex-1 flex-col px-6 py-6">
          <div className="flex max-w-lg flex-col gap-3 rounded-xl bg-surface p-6">
            <Button
              type="link"
              className="self-start px-0!"
              icon={<ArrowLeft size={15} />}
              onClick={() => navigate(backNav.to)}
            >
              {backNav.label}
            </Button>
            <Typography.Title level={3}>无法打开智能体会话</Typography.Title>
            <Typography.Paragraph type="secondary" className="!mb-0">
              会话不存在或无权访问。请从资源广场重新「立即使用」，或到历史对话中打开已有会话。
            </Typography.Paragraph>
            <Link to="/agent-sessions">查看历史对话</Link>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 w-full flex-col bg-bg" data-testid="agent-session-page">
      <main className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden px-6 pt-3 pb-4 max-[900px]:px-6 max-[560px]:px-4">
        <header className="flex shrink-0 flex-col gap-1.5">
          <Button
            type="link"
            className="self-start px-0!"
            icon={<ArrowLeft size={15} />}
            onClick={() => navigate(backNav.to)}
            data-testid="agent-session-back"
          >
            {backNav.label}
          </Button>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <Typography.Title level={4} className="mb-0! truncate text-base! leading-6!">
                {session.title}
              </Typography.Title>
              <p className="mb-0 mt-0.5 truncate text-[12px] text-text-secondary">
                {session.sessionKind === "skill" ? "技能" : "智能体"} · {session.agentDisplayName}
                <span className="mx-2 text-border">|</span>
                {statusLabel(session.status, sending)}
              </p>
            </div>
            <Link
              to={`/resources/catalog/${encodeURIComponent(session.resourceId)}`}
              className="shrink-0 text-[13px] text-text-secondary hover:text-brand"
            >
              查看资源详情
            </Link>
          </div>
        </header>

        <div className="flex min-h-0 flex-1 gap-3 max-[1100px]:flex-col max-[1100px]:overflow-auto">
          <aside
            className="flex w-[240px] shrink-0 flex-col overflow-hidden rounded-[12px] border border-border bg-surface max-[1100px]:w-full max-[1100px]:min-h-[200px]"
            aria-label={isSkillSession ? "Skill 步骤" : "智能体步骤"}
            data-testid="agent-session-steps"
          >
            <div className="border-b border-border px-4 py-3">
              <h2 className="m-0 text-[15px] font-semibold text-text">
                {isSkillSession ? "Skill 步骤" : "智能体步骤"}
              </h2>
              <p className="mb-0 mt-1 text-xs text-text-muted">
                {isSkillSession
                  ? "来自 Skill registry pack / substage 绑定"
                  : "来自 Agent registry mappings"}
              </p>
            </div>
            <div className="agent-session-scroll min-h-0 flex-1 overflow-auto px-3 py-3">
              {grouped.map(group => (
                <div key={group.stage} className="mb-4 last:mb-0">
                  <p className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-text-muted">
                    {stageLabel(group.stage)}
                  </p>
                  <ul className="m-0 list-none space-y-1 p-0">
                    {group.steps.map(step => {
                      const active = step.stepKey === session.activeStepKey;
                      return (
                        <li
                          key={step.stepKey}
                          className={`rounded-lg px-3 py-2 text-[13px] ${
                            active ? "bg-primary/10 font-medium text-text" : "text-text-secondary"
                          }`}
                        >
                          <div>{labelForSubStage(step.substage)}</div>
                          {step.supportedRole ? (
                            <div className="mt-0.5 text-[11px] text-text-muted">
                              {labelUnknown(AGENT_SUPPORTED_ROLE_LABELS, step.supportedRole)}
                            </div>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))}
            </div>
          </aside>

          <div className="flex min-h-0 min-w-0 flex-1 max-[1100px]:flex-col max-[1100px]:gap-3">
            <section
              className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-[12px] border border-border bg-surface"
              aria-label={isSkillSession ? "Skill 产物" : "会话产物"}
              data-testid="agent-session-artifacts"
            >
              <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
                <div className="min-w-0">
                  <h2 className="m-0 text-[15px] font-semibold text-text">
                    {isSkillSession ? "Skill 产物" : "产物"}
                  </h2>
                  <p className="mb-0 mt-1 text-xs text-text-muted">
                    {isSkillSession
                      ? "发送消息后会调用当前 Skill 并写入产物"
                      : "发送消息后会调用真实 Agent 并写入产物"}
                  </p>
                </div>
                {session.artifacts.length > 0 ? (
                  <Button
                    size="small"
                    loading={zipping}
                    onClick={() => void downloadZip()}
                    data-testid="agent-session-artifacts-zip"
                  >
                    产物打包下载
                  </Button>
                ) : null}
              </div>
              <div className="agent-session-scroll min-h-0 flex-1 space-y-3 overflow-auto px-4 py-4">
                {session.artifacts.length === 0 ? (
                  <p className="m-0 text-[13px] leading-6 text-text-secondary">
                    {isSkillSession
                      ? "还没有产物。在右侧描述研究目标后，这里会展示 Skill 输出。"
                      : "还没有产物。在右侧描述研究目标后，这里会展示 Agent 输出。"}
                  </p>
                ) : (
                  session.artifacts.map(artifact => (
                    <ArtifactCard
                      key={artifact.artifactId}
                      artifact={artifact}
                      active={artifact.artifactId === selectedArtifactId}
                      sessionId={session.sessionId}
                    />
                  ))
                )}
              </div>
            </section>

            <div
              className="relative z-10 hidden w-1.5 shrink-0 cursor-col-resize bg-transparent transition-colors after:absolute after:top-1/2 after:left-1/2 after:h-7 after:w-0.5 after:-translate-x-1/2 after:-translate-y-1/2 after:rounded-sm after:bg-border-strong after:opacity-40 after:transition-all hover:after:h-12 hover:after:bg-accent-strong hover:after:opacity-100 active:after:h-12 active:after:bg-accent-strong active:after:opacity-100 min-[1101px]:block"
              role="separator"
              aria-orientation="vertical"
              aria-label="调整产物区与对话区宽度"
              aria-valuemin={CHAT_WIDTH_MIN}
              aria-valuemax={CHAT_WIDTH_MAX}
              aria-valuenow={chatWidth}
              onMouseDown={startChatResize}
              data-testid="agent-session-chat-resizer"
            />

            <section
              className="flex min-h-0 w-full shrink-0 flex-col overflow-hidden rounded-[12px] border border-border bg-surface min-[1101px]:w-[var(--agent-session-chat-width)]"
              style={{ ["--agent-session-chat-width" as string]: `${chatWidth}px` }}
              aria-label="对话"
              data-testid="agent-session-chat"
            >
              <div className="border-b border-border px-4 py-3">
                <h2 className="m-0 text-[15px] font-semibold text-text">对话</h2>
                <p className="mb-0 mt-1 text-xs text-text-muted" data-testid="agent-session-chat-status">
                  {statusLabel(session.status, sending)}
                </p>
              </div>
              <div className="agent-session-scroll min-h-0 flex-1 space-y-3 overflow-auto px-4 py-3">
                {session.messages.map(item => (
                  <div
                    key={item.messageId}
                    className={`rounded-lg px-3 py-2 text-[13px] leading-6 ${
                      item.role === "user"
                        ? "ml-6 bg-primary/10 text-text"
                        : item.role === "system"
                          ? "bg-surface-soft text-text-secondary"
                          : "mr-4 bg-surface-soft text-text"
                    }`}
                  >
                    {item.content}
                  </div>
                ))}
                {chatBusy ? (
                  <div
                    className="mr-4 flex items-center gap-2 rounded-lg bg-surface-soft px-3 py-2 text-[13px] text-text-secondary"
                    data-testid="agent-session-chat-running"
                  >
                    <Spin size="small" />
                    <span>执行中…</span>
                  </div>
                ) : null}
                <div ref={chatEndRef} />
              </div>
              <div className="shrink-0 border-t border-border bg-surface p-3">
                <div
                  className="rounded-xl border border-border bg-surface p-3 transition-colors focus-within:border-brand"
                  data-testid="agent-session-composer"
                >
                  <Input.TextArea
                    variant="borderless"
                    value={draft}
                    onChange={event => setDraft(event.target.value)}
                    autoSize={{ minRows: 2, maxRows: 5 }}
                    placeholder={chatBusy ? "执行中，请稍候…" : "描述你的问题，补充约束或指令"}
                    disabled={chatBusy}
                    className="p-0! text-sm! leading-6! [&_.ant-input]:resize-none! [&_.ant-input]:bg-transparent! rounded-none!"
                    onPressEnter={event => {
                      if (event.shiftKey) return;
                      event.preventDefault();
                      void send();
                    }}
                  />
                  <div className="mt-2 flex items-center justify-end">
                    <Button
                      type="primary"
                      shape="circle"
                      size="small"
                      icon={<ArrowUpOutlined />}
                      loading={sending}
                      disabled={!draft.trim() || chatBusy}
                      onClick={() => void send()}
                      aria-label="发送"
                      data-testid="agent-session-send"
                    />
                  </div>
                </div>
              </div>
            </section>
          </div>
        </div>
      </main>
    </div>
  );
}
