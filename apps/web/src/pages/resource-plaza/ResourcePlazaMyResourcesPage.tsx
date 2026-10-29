import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Input, Modal, Pagination, Space, Tabs, Typography, message } from "antd";
import { Heart, Search, Upload, type LucideIcon } from "lucide-react";
import { UploadOutlined } from "@ant-design/icons";
import {
  MineResourceCard,
  ResourcePlazaResultState,
  type MineCardItem,
  type MineSegment
} from "../../components/resource-plaza";
import SkillManager from "../../components/SkillManager";
import { deleteUserSkill, fetchUserSkills, type UserSkill } from "../../api/skills";
import { fetchAllResourceSaves, useResourceSaveState, RESOURCE_KIND_LABELS } from "../../features/resource-plaza";
import type { ResourcePlazaResource } from "../../features/resource-plaza/types";
import ResourceUploadModal from "./ResourceUploadModal";
import { RESOURCE_DOMAIN_LABELS, RESOURCE_SCENARIO_LABELS } from "../../features/resource-plaza/state";

const { Text, Paragraph } = Typography;

const PAGE_SIZE = 20;

const SEGMENT_TABS: Array<{
  value: MineSegment;
  label: string;
  icon: LucideIcon;
}> = [
  { value: "saved", label: "收藏", icon: Heart },
  { value: "uploaded", label: "上传", icon: Upload }
];

function MineSegmentTabLabel({ label, count, icon: Icon }: { label: string; count: number; icon: LucideIcon }) {
  return (
    <span className="inline-flex items-center gap-2 px-1" data-testid={`resource-mine-stat-${label}`}>
      <Icon size={16} aria-hidden className="shrink-0" />
      <span>{label}</span>
      <span className="px-1.5 py-0.5 text-[12px] font-semibold leading-none tabular-nums">{count}</span>
    </span>
  );
}

export default function ResourcePlazaMyResourcesPage() {
  const navigate = useNavigate();
  const [segment, setSegment] = useState<MineSegment>("saved");
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [savedResources, setSavedResources] = useState<ResourcePlazaResource[]>([]);
  const [uploads, setUploads] = useState<UserSkill[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [retryKey, setRetryKey] = useState(0);
  const [skillModalOpen, setSkillModalOpen] = useState(false);
  const [manageSkill, setManageSkill] = useState<UserSkill | null>(null);
  const [skillManagerOpen, setSkillManagerOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setFailed(false);
    Promise.all([fetchAllResourceSaves(controller.signal), fetchUserSkills()])
      .then(([saves, userSkills]) => {
        if (controller.signal.aborted) return;
        setSavedResources(saves);
        setUploads(userSkills);
      })
      .catch(error => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setFailed(true);
        setSavedResources([]);
        setUploads([]);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [retryKey]);

  const savedIds = useMemo(() => savedResources.map(resource => resource.id), [savedResources]);
  const { isSaved, isPending, toggleSave } = useResourceSaveState(savedIds);

  const items = useMemo<MineCardItem[]>(() => {
    const savedItems: MineCardItem[] = savedResources.map(resource => {
      const domainTags = resource.domains.slice(0, 2).map(d => RESOURCE_DOMAIN_LABELS[d]);
      const scenarioTags = resource.scenarios.slice(0, 2).map(s => RESOURCE_SCENARIO_LABELS[s]);
      const tags = [...domainTags, ...scenarioTags].slice(0, 3);

      return {
        id: `saved:${resource.id}`,
        segment: "saved",
        title: resource.name || "未命名资源",
        description: resource.description,
        typeLabel: RESOURCE_KIND_LABELS[resource.kind],
        sourceLabel: "公共资源",
        updatedAt: null,
        kind: resource.kind,
        href: `/resources/catalog/${encodeURIComponent(resource.id)}?origin=mine`,
        resourceId: resource.id,
        tags
      };
    });
    const uploadedItems: MineCardItem[] = uploads.map(skill => ({
      id: `uploaded:${skill.skill_name}`,
      segment: "uploaded",
      title: skill.skill_name,
      description: skill.description,
      typeLabel: "私有技能",
      sourceLabel: "上传",
      updatedAt: skill.created_at,
      kind: "skill",
      skillName: skill.skill_name,
      files: skill.files,
      tags: []
    }));
    return [...savedItems, ...uploadedItems];
  }, [savedResources, uploads]);

  const filtered = useMemo(() => {
    const needle = keyword.trim().toLowerCase();
    return items.filter(item => {
      if (item.segment !== segment) return false;
      if (!needle) return true;
      const haystack = `${item.title} ${item.description} ${item.typeLabel} ${item.sourceLabel}`.toLowerCase();
      return haystack.includes(needle);
    });
  }, [items, keyword, segment]);

  useEffect(() => {
    setPage(1);
  }, [segment, keyword]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE) || 1);
  const pageItems = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const handleSave = async (resourceId: string, nextSaved: boolean) => {
    const ok = await toggleSave(resourceId, nextSaved);
    if (ok && !nextSaved) {
      setSavedResources(previous => previous.filter(item => item.id !== resourceId));
    }
  };

  const openUploadedSkill = (skillName: string) => {
    const skill = uploads.find(item => item.skill_name === skillName) ?? null;
    setManageSkill(skill);
  };

  const openItem = (item: MineCardItem) => {
    if (item.href) {
      navigate(item.href);
      return;
    }
    if (item.segment === "uploaded" && item.skillName) {
      openUploadedSkill(item.skillName);
    }
  };

  const refreshAfterModal = () => {
    setRetryKey(value => value + 1);
  };

  const handleDeleteUploaded = () => {
    if (!manageSkill) return;
    const skillName = manageSkill.skill_name;
    Modal.confirm({
      title: "确认删除",
      content: `确定要删除技能“${skillName}”吗？此操作不可撤销。`,
      okText: "删除",
      okType: "danger",
      cancelText: "取消",
      onOk: async () => {
        setDeleting(true);
        try {
          await deleteUserSkill(skillName);
          message.success(`技能“${skillName}”已删除`);
          setManageSkill(null);
          setUploads(previous => previous.filter(item => item.skill_name !== skillName));
        } catch (error) {
          message.error(error instanceof Error ? error.message : "删除失败");
          throw error;
        } finally {
          setDeleting(false);
        }
      }
    });
  };

  return (
    <div className="mx-auto flex w-full h-full flex-col gap-6" data-testid="resource-plaza-my-resources-page">
      <section aria-label="筛选与搜索" className="flex-none">
        <Tabs
          type="card"
          activeKey={segment}
          onChange={key => setSegment(key as MineSegment)}
          data-testid="resource-my-tabs"
          className="[&_.ant-tabs-nav]:mb-0! [&_.ant-tabs-nav::before]:border-none! [&_.ant-tabs-tab]:rounded-t-lg! [&_.ant-tabs-tab]:border-border! [&_.ant-tabs-tab]:bg-surface-soft! [&_.ant-tabs-tab]:px-4! [&_.ant-tabs-tab]:py-2.5! [&_.ant-tabs-tab-active]:bg-surface! [&_.ant-tabs-tab-active]:text-brand! [&_.ant-tabs-tab-active]:font-bold! [&_.ant-tabs-tab-btn]:text-inherit!"
          tabBarExtraContent={
            <div className="flex min-w-0 flex-wrap items-center justify-end gap-2 max-[720px]:w-full max-[720px]:justify-start">
              <Input
                allowClear
                className="min-w-[200px] flex-1 sm:max-w-[280px]"
                prefix={<Search size={15} className="text-text-muted" aria-hidden />}
                value={keyword}
                onChange={event => setKeyword(event.target.value)}
                placeholder="搜索资源名称 / 关键词"
                aria-label="搜索我的资源"
              />
              <Button
                type="primary"
                icon={<UploadOutlined size={15} aria-hidden />}
                onClick={() => setSkillModalOpen(true)}
              >
                上传资源
              </Button>
              {/* <Button onClick={() => setSkillManagerOpen(true)}>技能管理</Button> */}
            </div>
          }
          items={SEGMENT_TABS.map(tab => ({
            key: tab.value,
            label: (
              <MineSegmentTabLabel
                label={tab.label}
                count={tab.value === "saved" ? savedResources.length : uploads.length}
                icon={tab.icon}
              />
            )
          }))}
        />
      </section>

      <div className="flex flex-col gap-5 rounded-xl bg-surface p-6 flex-1 min-h-0">
        <section className="flex flex-col flex-1 min-h-0" aria-live="polite" data-testid="resource-mine-grid">
          {loading ? <ResourcePlazaResultState state="loading" /> : null}
          {!loading && failed ? (
            <ResourcePlazaResultState
              state="safety_error"
              description="我的资源暂时无法加载。系统不会显示未经确认的本地缓存结果。"
              onRetry={() => setRetryKey(value => value + 1)}
              onHome={() => navigate("/resources")}
              homeLabel="返回资源广场"
            />
          ) : null}
          {!loading && !failed && filtered.length === 0 ? (
            <div
              className="px-6 py-12 my-auto text-center"
              data-testid={segment === "saved" ? "resource-saved-empty" : undefined}
            >
              <h3 className="mb-1 mt-0 text-base font-semibold text-text">这里还没有内容</h3>
              <p className="mb-4 mt-0 text-sm text-text-secondary">
                {segment === "saved" ? "可以先去公共资源收藏一些能力。" : "上传私有技能后会出现在这里。"}
              </p>
              <Button
                type="primary"
                onClick={() => {
                  if (segment === "uploaded") {
                    setSkillModalOpen(true);
                    return;
                  }
                  navigate("/resources/catalog");
                }}
              >
                {segment === "uploaded" ? "上传资源" : "去公共资源看看"}
              </Button>
            </div>
          ) : null}
          {!loading && !failed && pageItems.length > 0 ? (
            <div
              className="grid grid-cols-3 2xl:grid-cols-4 gap-4 max-[960px]:grid-cols-2 max-[620px]:grid-cols-1"
              data-testid={segment === "saved" ? "resource-saved-list" : "resource-uploaded-list"}
            >
              {pageItems.map(item => {
                const saved = item.resourceId ? isSaved(item.resourceId) : false;
                const savePending = item.resourceId ? isPending(item.resourceId) : false;
                return (
                  <MineResourceCard
                    key={item.id}
                    item={item}
                    saved={item.segment === "saved" ? true : saved}
                    savePending={savePending}
                    onView={() => openItem(item)}
                    onToggleSave={
                      item.resourceId && item.segment === "saved"
                        ? () => void handleSave(item.resourceId!, false)
                        : undefined
                    }
                  />
                );
              })}
            </div>
          ) : null}
        </section>

        {!loading && !failed && filtered.length > 0 ? (
          <div className="flex justify-end">
            <Pagination
              current={page}
              pageSize={PAGE_SIZE}
              total={filtered.length}
              onChange={setPage}
              showSizeChanger={true}
              showTotal={() => `共 ${totalPages} 页`}
              pageSizeOptions={[10, 20, 50]}
            />
          </div>
        ) : null}
      </div>

      <ResourceUploadModal
        open={skillModalOpen}
        onClose={() => {
          setSkillModalOpen(false);
          refreshAfterModal();
        }}
      />

      <Modal
        title="我上传的技能"
        open={skillManagerOpen}
        onCancel={() => {
          setSkillManagerOpen(false);
          refreshAfterModal();
        }}
        footer={null}
        width={880}
        destroyOnClose
        data-testid="resource-mine-skill-manager-modal"
      >
        <SkillManager showSystemSkills={false} />
      </Modal>

      <Modal
        title={"技能管理"}
        open={Boolean(manageSkill)}
        onCancel={() => setManageSkill(null)}
        footer={
          <Space>
            <Button onClick={() => setManageSkill(null)}>关闭</Button>
            <Button danger loading={deleting} onClick={handleDeleteUploaded}>
              删除
            </Button>
          </Space>
        }
        destroyOnClose
        data-testid="resource-mine-uploaded-detail-modal"
      >
        {manageSkill ? (
          <div>
            <Paragraph className="mb-0! text-[16px]! font-semibold">{manageSkill.skill_name || "未命名"}</Paragraph>
            <Paragraph type="secondary" className="m-0! text-[14px]!">
              {manageSkill.description || "未提供资源说明"}
            </Paragraph>
            <div className="mt-4">
              <Text strong>文件列表</Text>
              <div className="mt-2 flex flex-col gap-1.5">
                {manageSkill.files.length === 0 ? (
                  <Text type="secondary">—</Text>
                ) : (
                  manageSkill.files.map(file => (
                    <Paragraph
                      className="bg-surface-muted rounded-md p-1! text-[12px]! text-text-secondary! m-0!"
                      key={file}
                    >
                      {file}
                    </Paragraph>
                  ))
                )}
              </div>
            </div>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
