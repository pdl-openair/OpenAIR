import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type UserNoticeLevel = "progress" | "info" | "warning" | "error" | "success";
export type UserNoticeStatus = "active" | "done" | "failed";

export interface UserNotice {
  id: string;
  source: string;
  title: string;
  message: string;
  detail?: string | null;
  level: UserNoticeLevel;
  status: UserNoticeStatus;
  createdAt: number;
  updatedAt: number;
  elapsedSeconds?: number | null;
  nextActionHint?: string | null;
  unread: boolean;
}

export interface UpsertUserNoticeInput {
  id: string;
  source: string;
  title: string;
  message: string;
  detail?: string | null;
  level: UserNoticeLevel;
  status: UserNoticeStatus;
  elapsedSeconds?: number | null;
  nextActionHint?: string | null;
  unread?: boolean;
  openPanel?: boolean;
}

interface UserNoticeContextValue {
  notices: UserNotice[];
  panelOpen: boolean;
  unreadCount: number;
  activeCount: number;
  setPanelOpen: (open: boolean) => void;
  upsertNotice: (input: UpsertUserNoticeInput) => void;
  dismissNotice: (id: string) => void;
  clearNotices: () => void;
  markAllRead: () => void;
}

const UserNoticeContext = createContext<UserNoticeContextValue | null>(null);
const MAX_NOTICES = 20;

function shouldAutoOpen(input: UpsertUserNoticeInput, existing: UserNotice | undefined): boolean {
  if (input.openPanel) return true;
  if (input.level === "error") return true;
  if (input.level === "warning" && existing?.level !== "warning") return true;
  return false;
}

export function UserNoticeProvider({ children }: { children: ReactNode }) {
  const [notices, setNotices] = useState<UserNotice[]>([]);
  const [panelOpen, setPanelOpenState] = useState(false);

  const setPanelOpen = useCallback((open: boolean) => {
    setPanelOpenState(open);
    if (open) {
      setNotices(current => current.map(notice => (notice.unread ? { ...notice, unread: false } : notice)));
    }
  }, []);

  const upsertNotice = useCallback((input: UpsertUserNoticeInput) => {
    setNotices(current => {
      const existing = current.find(notice => notice.id === input.id);
      const now = Date.now();
      const autoOpen = shouldAutoOpen(input, existing);
      if (autoOpen) setPanelOpenState(true);
      const unread = input.unread
        ?? (!autoOpen && !panelOpen && (
          !existing
          || existing.message !== input.message
          || existing.level !== input.level
          || existing.status !== input.status
        ));
      const nextNotice: UserNotice = {
        id: input.id,
        source: input.source,
        title: input.title,
        message: input.message,
        detail: input.detail ?? null,
        level: input.level,
        status: input.status,
        createdAt: existing?.createdAt ?? now,
        updatedAt: now,
        elapsedSeconds: input.elapsedSeconds ?? existing?.elapsedSeconds ?? null,
        nextActionHint: input.nextActionHint ?? null,
        unread,
      };
      const rest = current.filter(notice => notice.id !== input.id);
      return [nextNotice, ...rest].slice(0, MAX_NOTICES);
    });
  }, [panelOpen]);

  const dismissNotice = useCallback((id: string) => {
    setNotices(current => current.filter(notice => notice.id !== id));
  }, []);

  const clearNotices = useCallback(() => {
    setNotices([]);
  }, []);

  const markAllRead = useCallback(() => {
    setNotices(current => current.map(notice => (notice.unread ? { ...notice, unread: false } : notice)));
  }, []);

  const unreadCount = notices.filter(notice => notice.unread).length;
  const activeCount = notices.filter(notice => notice.status === "active").length;

  const value = useMemo(
    () => ({
      notices,
      panelOpen,
      unreadCount,
      activeCount,
      setPanelOpen,
      upsertNotice,
      dismissNotice,
      clearNotices,
      markAllRead,
    }),
    [activeCount, clearNotices, dismissNotice, markAllRead, notices, panelOpen, setPanelOpen, unreadCount, upsertNotice],
  );

  return (
    <UserNoticeContext.Provider value={value}>
      {children}
    </UserNoticeContext.Provider>
  );
}

export function useUserNotice(): UserNoticeContextValue {
  const context = useContext(UserNoticeContext);
  if (!context) {
    throw new Error("useUserNotice must be used within UserNoticeProvider");
  }
  return context;
}

export function useUserNoticeOptional(): UserNoticeContextValue | null {
  return useContext(UserNoticeContext);
}
