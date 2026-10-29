import { useEffect, useState } from "react";
import type { StageCatalog } from "@openair/shared";
import { fetchPackStageCatalog } from "../api/client";

export function useResearchDesignCatalog(pack: string, enabled: boolean) {
  const [loaded, setLoaded] = useState<{
    pack: string;
    catalog: StageCatalog[];
    error: string | null;
  } | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    void fetchPackStageCatalog(pack).then(
      (catalog) => {
        if (!cancelled) setLoaded({ pack, catalog, error: null });
      },
      () => {
        if (!cancelled)
          setLoaded({
            pack,
            catalog: [],
            error: "工作类型读取失败，请重新进入编辑后重试。",
          });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [pack, enabled]);
  const current = loaded?.pack === pack ? loaded : null;
  return {
    catalog: current?.catalog ?? [],
    loading: enabled && current === null,
    error: current?.error ?? null,
  };
}
