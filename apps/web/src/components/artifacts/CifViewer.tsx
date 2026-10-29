import { useEffect, useRef, useState } from "react";
import { Button, Space, Typography } from "antd";
import { ColumnHeightOutlined, ExpandOutlined, ReloadOutlined } from "@ant-design/icons";

// 3Dmol.js：改为本地 bundle 动态 import（Vite 自动分包）。生产环境（尤其
// 国内网络）无法保证 cdn.jsdelivr.net 可达，此前 CDN 注入失败会把 cif 渲染
// 整个打成错误态。
declare global {
  interface Window {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    $3Dmol?: any;
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let moduleLoadPromise: Promise<any> | null = null;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function load3Dmol(): Promise<any> {
  if (typeof window !== "undefined" && window.$3Dmol) return Promise.resolve(window.$3Dmol);
  if (moduleLoadPromise) return moduleLoadPromise;
  moduleLoadPromise = import("3dmol").then((mod) => {
    // UMD 包：默认导出即 3Dmol 命名空间，部分构建同时挂 window.$3Dmol。
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const resolved = (mod as any).default ?? mod ?? window.$3Dmol;
    if (!resolved?.createViewer) throw new Error("3Dmol.js 模块加载异常");
    return resolved;
  });
  return moduleLoadPromise;
}

export interface CifViewerProps {
  content: string;
  title?: string;
}

export function CifViewer({ content, title }: CifViewerProps) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const viewerRef = useRef<any>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [errorMsg, setErrorMsg] = useState<string>("");

  const initViewer = async () => {
    setStatus("loading");
    setErrorMsg("");
    try {
      const $3Dmol = await load3Dmol();
      if (!$3Dmol || !containerRef.current) {
        throw new Error("3Dmol 初始化失败");
      }
      // 旧 viewer 清理
      if (viewerRef.current) {
        try {
          viewerRef.current.clear();
        } catch {
          /* noop */
        }
        viewerRef.current = null;
      }
      const viewer = $3Dmol.createViewer(containerRef.current, {
        backgroundColor: "0xf7f8fa",
      });
      viewer.addModel(content, "cif");
      viewer.setStyle({}, { stick: { radius: 0.15 }, sphere: { scale: 0.35 } });
      viewer.addUnitCell();
      viewer.zoomTo();
      viewer.render();
      viewerRef.current = viewer;
      setStatus("ready");
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
      setStatus("error");
    }
  };

  useEffect(() => {
    void initViewer();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [content]);

  useEffect(() => {
    return () => {
      if (viewerRef.current) {
        try {
          viewerRef.current.clear();
        } catch {
          /* noop */
        }
      }
    };
  }, []);

  const action = (fn: () => void) => () => {
    try {
      fn();
    } catch {
      /* noop */
    }
  };

  if (status === "error") {
    return (
      <div style={{ padding: 16 }}>
        <Typography.Text type="danger">3D 渲染失败:{errorMsg}</Typography.Text>
        <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
          已切换为 CIF 原文降级展示；同目录同名 PNG 结构渲染图（若已生成）
          也可直接查看。
        </Typography.Paragraph>
        <details>
          <summary className="cursor-pointer">CIF 原文</summary>
          <pre
            style={{
              maxHeight: 420,
              overflow: "auto",
              background: "#fafafa",
              padding: 12,
              borderRadius: 6,
              fontSize: 12,
            }}
          >
            {content}
          </pre>
        </details>
      </div>
    );
  }

  return (
    <div>
      <div
        style={{
          padding: "8px 12px",
          borderBottom: "1px solid #f0f0f0",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <Typography.Text strong>{title ?? "晶体结构"}</Typography.Text>
        <Space size="small">
          <Button
            size="small"
            icon={<ReloadOutlined />}
            onClick={action(() => viewerRef.current?.spin("y", 0.5))}
          >
            自转
          </Button>
          <Button
            size="small"
            icon={<ColumnHeightOutlined />}
            onClick={action(() => viewerRef.current?.zoomTo())}
          >
            复位
          </Button>
          <Button
            size="small"
            icon={<ExpandOutlined />}
            onClick={action(() => viewerRef.current?.resize())}
          >
            重排
          </Button>
        </Space>
      </div>
      <div
        ref={containerRef}
        style={{
          width: "100%",
          height: 420,
          position: "relative",
          opacity: status === "loading" ? 0.5 : 1,
        }}
      />
    </div>
  );
}
