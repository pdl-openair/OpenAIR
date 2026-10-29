import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export interface PageHeaderState {
  title?: string;
  action?: ReactNode;
  transparent?: boolean;
}

interface PageHeaderContextValue {
  pageHeader: PageHeaderState;
  setPageHeader: (state: PageHeaderState) => void;
}

const PageHeaderContext = createContext<PageHeaderContextValue | null>(null);

export function PageHeaderProvider({ children }: { children: ReactNode }) {
  const [pageHeader, setPageHeader] = useState<PageHeaderState>({});

  const value = useMemo(
    () => ({ pageHeader, setPageHeader }),
    [pageHeader],
  );

  return (
    <PageHeaderContext.Provider value={value}>
      {children}
    </PageHeaderContext.Provider>
  );
}

export function usePageHeaderContext() {
  const context = useContext(PageHeaderContext);
  if (!context) {
    throw new Error("usePageHeaderContext must be used within PageHeaderProvider");
  }
  return context;
}

/** Pages call this to override breadcrumb title, header actions, or transparency. */
export function usePageHeader(state: PageHeaderState) {
  const { setPageHeader } = usePageHeaderContext();

  const { title, action, transparent } = state;

  useEffect(() => {
    setPageHeader({ title, action, transparent });
    return () => setPageHeader({});
  }, [action, setPageHeader, title, transparent]);
}
