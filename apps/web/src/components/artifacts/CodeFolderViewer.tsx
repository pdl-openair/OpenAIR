import { Empty, Spin, Tree, Typography } from "antd";
import type { DataNode } from "antd/es/tree";
import {
  FileOutlined,
  FolderOpenOutlined,
  FileTextOutlined,
  CodeOutlined,
  FileImageOutlined,
} from "@ant-design/icons";
import { useCallback, useEffect, useMemo, useState } from "react";
import { getApiBaseUrl } from "../../api/client";
import { fetchArtifactTree, type FileTreeNode } from "../../api/client";

const TEXT_EXTS = new Set([
  "py", "js", "ts", "tsx", "jsx", "yaml", "yml", "json", "md", "toml",
  "sh", "bash", "cfg", "ini", "txt", "csv", "xml", "html", "css", "scss",
  "rs", "go", "java", "c", "cpp", "h", "hpp", "rb", "php", "sql",
  "gitignore", "env", "lock", "toml", "tex", "bib", "sty",
]);

const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "svg", "webp"]);

function extOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
}

function isTextFile(name: string): boolean {
  const ext = extOf(name);
  if (TEXT_EXTS.has(ext)) return true;
  if (!ext && name.startsWith(".")) return true; // .gitignore etc
  return false;
}

function isImageFile(name: string): boolean {
  return IMAGE_EXTS.has(extOf(name));
}

function treeIcon(name: string, isLeaf: boolean) {
  if (!isLeaf) return <FolderOpenOutlined />;
  const ext = extOf(name);
  if (IMAGE_EXTS.has(ext)) return <FileImageOutlined />;
  if (TEXT_EXTS.has(ext) || ext === "md" || ext === "tex") return <FileTextOutlined />;
  if (["py", "js", "ts", "sh", "bash", "go", "rs", "java", "c", "cpp"].includes(ext)) return <CodeOutlined />;
  return <FileOutlined />;
}

function toAntdTree(node: FileTreeNode, prefix: string): DataNode {
  const key = prefix ? `${prefix}/${node.name}` : node.name;
  const isLeaf = node.type === "file";
  return {
    key,
    title: node.name,
    icon: treeIcon(node.name, isLeaf),
    isLeaf,
    children: node.children
      ? node.children.map((c) => toAntdTree(c, key))
      : undefined,
  };
}

export interface CodeFolderViewerProps {
  taskId: string;
  artifactId: string;
}

export function CodeFolderViewer({ taskId, artifactId }: CodeFolderViewerProps) {
  const [treeData, setTreeData] = useState<FileTreeNode | null>(null);
  const [loadingTree, setLoadingTree] = useState(true);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [loadingFile, setLoadingFile] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoadingTree(true);
    fetchArtifactTree(taskId, artifactId).then((tree) => {
      if (!cancelled) {
        setTreeData(tree);
        setLoadingTree(false);
      }
    });
    return () => { cancelled = true; };
  }, [taskId, artifactId]);

  const antdTree = useMemo(() => (treeData ? [toAntdTree(treeData, "")] : []), [treeData]);

  const loadFile = useCallback(async (relPath: string) => {
    setLoadingFile(true);
    setFileContent(null);
    try {
      const base = `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactId)}/files/`;
      const res = await fetch(base + encodeURIComponent(relPath));
      if (res.ok) {
        const text = await res.text();
        setFileContent(text);
      } else {
        setFileContent(`[加载失败: ${res.status}]`);
      }
    } catch {
      setFileContent("[加载失败]");
    } finally {
      setLoadingFile(false);
    }
  }, [taskId, artifactId]);

  const onSelect = useCallback(
    (keys: React.Key[]) => {
      const key = keys[0] as string | undefined;
      if (!key) return;
      setSelectedKey(key);
      const name = key.split("/").pop() ?? "";
      if (isTextFile(name)) {
        loadFile(key);
      } else if (isImageFile(name)) {
        setFileContent(null);
      } else {
        setFileContent(null);
      }
    },
    [loadFile],
  );

  if (loadingTree) return <Spin />;

  if (!treeData) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无法加载文件树" />;
  }

  const selectedName = selectedKey ? selectedKey.split("/").pop() ?? "" : "";

  return (
    <div className="wb-code-folder">
      <div className="wb-code-folder-tree">
        <div className="wb-code-folder-tree-title">
          <FolderOpenOutlined style={{ marginRight: 6 }} />
          {treeData.name}
        </div>
        <Tree
          showIcon
          blockNode
          treeData={antdTree}
          onSelect={onSelect}
          selectedKeys={selectedKey ? [selectedKey] : []}
          defaultExpandAll={false}
        />
      </div>
      <div className="wb-code-folder-content">
        {!selectedKey && (
          <div className="wb-code-folder-empty">
            <Typography.Text type="secondary">点击左侧文件查看内容</Typography.Text>
          </div>
        )}
        {selectedKey && loadingFile && <Spin />}
        {selectedKey && !loadingFile && isTextFile(selectedName) && fileContent !== null && (
          <div className="wb-code-folder-file-header">
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {selectedKey}
            </Typography.Text>
          </div>
        )}
        {selectedKey && !loadingFile && isTextFile(selectedName) && fileContent !== null && (
          <pre className="wb-code-folder-pre">{fileContent}</pre>
        )}
        {selectedKey && !loadingFile && isImageFile(selectedName) && (
          <div className="wb-code-folder-image-wrap">
            <img
              src={`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactId)}/files/${encodeURIComponent(selectedKey)}`}
              alt={selectedName}
              className="wb-code-folder-image"
            />
          </div>
        )}
        {selectedKey && !loadingFile && !isTextFile(selectedName) && !isImageFile(selectedName) && (
          <div className="wb-code-folder-empty">
            <Typography.Text type="secondary">
              不支持预览此文件类型 ({extOf(selectedName) || "未知"})
            </Typography.Text>
          </div>
        )}
      </div>
    </div>
  );
}
