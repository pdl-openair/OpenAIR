/**
 * SkillManager — system public skills list + user private skills CRUD.
 *
 * Uses Ant Design components following the existing codebase patterns.
 */
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Button, Card, Col, Collapse, message, Modal, Pagination, Row, Space, Spin, Tag, Typography } from "antd";
import { DeleteOutlined, ThunderboltOutlined, UserOutlined } from "@ant-design/icons";
import { fetchSystemSkills, fetchUserSkills, deleteUserSkill, type SystemSkill, type UserSkill } from "../api/skills";
import type { AppLanguage } from "../lib/appPreferences";
import SkillUploadForm from "./SkillUploadForm";

const { Text, Paragraph } = Typography;

const USER_PAGE_SIZE = 10;
const SYS_PAGE_SIZE = 10;

const COPY = {
  "zh-CN": {
    loadFailed: "加载技能失败",
    deleteConfirmTitle: "确认删除",
    deleteConfirmContent: (name: string) => `确定要删除技能“${name}”吗？此操作不可撤销。`,
    delete: "删除",
    deleteSuccess: (name: string) => `技能“${name}”已删除`,
    deleteFailed: "删除失败",
    cancel: "取消",
    mySkills: "我上传的技能",
    privateAvailable: "私有可用",
    uploadSkill: "上传资源",
    fileCount: (n: number) => `${n} 个文件`,
    emptyPrivate: "还没有上传技能。上传后默认仅在你的授权范围内可见。",
    privateSkillsNote: "上传后默认仅你可用；展开可查看文件列表。",
    systemSkills: "系统公共技能",
    noDescription: "暂无描述",
    contentDataNote: "技能名称和说明来自上传内容，可能保留原语言。",
    uploadPrivateSkill: "上传资源"
  },
  "en-US": {
    loadFailed: "Failed to load Skills",
    deleteConfirmTitle: "Delete Skill",
    deleteConfirmContent: (name: string) => `Delete Skill "${name}"? This cannot be undone.`,
    delete: "Delete",
    deleteSuccess: (name: string) => `Skill "${name}" deleted`,
    deleteFailed: "Delete failed",
    cancel: "Cancel",
    mySkills: "My Skills",
    privateAvailable: "Private",
    uploadSkill: "Upload Skill",
    fileCount: (n: number) => `${n} file${n === 1 ? "" : "s"}`,
    emptyPrivate: "No private Skills yet. Upload a Skill to add a custom capability.",
    privateSkillsNote:
      "Private by default; expand a row to view files. Skill names come from upload content and may remain in their source language.",
    systemSkills: "System Skills",
    noDescription: "No description yet",
    contentDataNote:
      "Skill names, domains, and descriptions are capability content data, so they may remain in their source language. Page controls and shell copy follow the selected UI language.",
    uploadPrivateSkill: "Upload Private Skill"
  }
} as const;

/** Shared row chrome so user / system lists match in height and title weight. */
function SkillCollapseLabel({ title, tags, action }: { title: string; tags: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex w-full items-center gap-2">
      <span className="min-w-0 truncate text-sm !font-semibold leading-6 text-text">{title}</span>
      <span className="flex shrink-0 items-center gap-1">{tags}</span>
      {/* Fixed action slot keeps row height identical whether delete exists or not. */}
      <span
        className="ml-auto flex h-7 w-[4.25rem] shrink-0 items-center justify-end"
        onClick={(e: { stopPropagation: () => void }) => e.stopPropagation()}
        onKeyDown={(e: { stopPropagation: () => void }) => e.stopPropagation()}
      >
        {action ?? null}
      </span>
    </div>
  );
}

// Same header padding / icon alignment for both skill Collapse lists.
const SKILL_COLLAPSE_CLASS =
  "flex-1 [&_.ant-collapse-header]:!items-center [&_.ant-collapse-header]:!py-2.5 [&_.ant-collapse-header]:!px-3 [&_.ant-collapse-expand-icon]:!h-7 [&_.ant-collapse-expand-icon]:!self-center [&_.ant-collapse-header-text]:!flex-1 [&_.ant-collapse-header-text]:!my-0";

export default function SkillManager({
  language = "zh-CN",
  showSystemSkills = true
}: {
  language?: AppLanguage;
  showSystemSkills?: boolean;
}) {
  const copy = COPY[language];
  const [systemSkills, setSystemSkills] = useState<SystemSkill[]>([]);
  const [userSkills, setUserSkills] = useState<UserSkill[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [userPage, setUserPage] = useState(1);
  const [sysPage, setSysPage] = useState(1);

  const loadSkills = useCallback(async () => {
    setLoading(true);
    try {
      const [sys, usr] = await Promise.allSettled([
        showSystemSkills ? fetchSystemSkills() : Promise.resolve([] as SystemSkill[]),
        fetchUserSkills()
      ]);
      if (sys.status === "fulfilled") setSystemSkills(sys.value);
      else if (showSystemSkills) message.error(copy.loadFailed);
      if (usr.status === "fulfilled") setUserSkills(usr.value);
      else {
        setUserSkills([]);
        message.error(usr.reason instanceof Error ? usr.reason.message : copy.loadFailed);
      }
    } catch {
      message.error(copy.loadFailed);
    } finally {
      setLoading(false);
    }
  }, [copy.loadFailed, showSystemSkills]);

  useEffect(() => {
    loadSkills();
  }, [loadSkills]);

  const handleDelete = async (skillName: string) => {
    Modal.confirm({
      title: copy.deleteConfirmTitle,
      content: copy.deleteConfirmContent(skillName),
      okText: copy.delete,
      okType: "danger",
      cancelText: copy.cancel,
      onOk: async () => {
        try {
          await deleteUserSkill(skillName);
          message.success(copy.deleteSuccess(skillName));
          loadSkills();
        } catch (err) {
          message.error(err instanceof Error ? err.message : copy.deleteFailed);
        }
      }
    });
  };

  const userPageCount = Math.max(1, Math.ceil(userSkills.length / USER_PAGE_SIZE));
  const safeUserPage = Math.min(userPage, userPageCount);
  const pagedUserSkills = userSkills.slice((safeUserPage - 1) * USER_PAGE_SIZE, safeUserPage * USER_PAGE_SIZE);

  const visibleSystemSkills = systemSkills.filter(s => s.display_name);
  const sysPageCount = Math.max(1, Math.ceil(visibleSystemSkills.length / SYS_PAGE_SIZE));
  const safeSysPage = Math.min(sysPage, sysPageCount);
  const pagedSystemSkills = visibleSystemSkills.slice((safeSysPage - 1) * SYS_PAGE_SIZE, safeSysPage * SYS_PAGE_SIZE);

  return (
    <div className="skill-manager">
      <Spin spinning={loading}>
        <Row gutter={[16, 16]} align="stretch">
          <Col xs={24} lg={showSystemSkills ? 12 : 24} className="flex">
            <Card
              className="flex h-full w-full flex-col [&_.ant-card-body]:flex [&_.ant-card-body]:flex-1 [&_.ant-card-body]:flex-col"
              title={
                <Space>
                  <UserOutlined />
                  <span>{copy.mySkills}</span>
                  <Tag color="processing">{userSkills.length}</Tag>
                  <Tag color="blue">{copy.privateAvailable}</Tag>
                </Space>
              }
              // extra={
              //   <Button
              //     type="primary"
              //     icon={<UploadOutlined />}
              //     onClick={() => setUploadModalOpen(true)}
              //     size="small"
              //   >
              //     {copy.uploadSkill}
              //   </Button>
              // }
            >
              <Paragraph type="secondary" className="mb-3 text-sm">
                {copy.privateSkillsNote}
              </Paragraph>
              {userSkills.length === 0 ? (
                <div className="flex flex-1 items-center justify-center py-6 text-center">
                  <Text type="secondary">{copy.emptyPrivate}</Text>
                </div>
              ) : (
                <>
                  <Collapse
                    ghost
                    className={SKILL_COLLAPSE_CLASS}
                    items={pagedUserSkills.map(skill => ({
                      key: skill.skill_name,
                      label: (
                        <SkillCollapseLabel
                          title={skill.skill_name}
                          tags={<Tag className="!m-0">{copy.fileCount(skill.files.length)}</Tag>}
                          action={
                            <Button
                              type="text"
                              danger
                              icon={<DeleteOutlined />}
                              size="small"
                              className="!h-7 !px-1"
                              onClick={() => handleDelete(skill.skill_name)}
                            >
                              {copy.delete}
                            </Button>
                          }
                        />
                      ),
                      children:
                        skill.files.length === 0 ? (
                          <Text type="secondary">—</Text>
                        ) : (
                          <Space wrap size={[4, 4]}>
                            {skill.files.map(f => (
                              <Tag key={f}>{f}</Tag>
                            ))}
                          </Space>
                        )
                    }))}
                  />
                  {userSkills.length > USER_PAGE_SIZE ? (
                    <Pagination
                      className="mt-2 flex justify-end"
                      size="small"
                      current={safeUserPage}
                      pageSize={USER_PAGE_SIZE}
                      total={userSkills.length}
                      showSizeChanger={false}
                      onChange={setUserPage}
                    />
                  ) : null}
                </>
              )}
            </Card>
          </Col>

          {showSystemSkills ? (
            <Col xs={24} lg={12} className="flex">
              <Card
                className="flex h-full w-full flex-col [&_.ant-card-body]:flex [&_.ant-card-body]:flex-1 [&_.ant-card-body]:flex-col"
                title={
                  <Space>
                    <ThunderboltOutlined />
                    <span>{copy.systemSkills}</span>
                    <Tag color="green">{systemSkills.length}</Tag>
                  </Space>
                }
              >
                <Paragraph type="secondary" className="mb-3 text-sm">
                  {copy.contentDataNote}
                </Paragraph>
                <Collapse
                  ghost
                  className={SKILL_COLLAPSE_CLASS}
                  items={pagedSystemSkills.map(skill => ({
                    key: skill.skill_id,
                    label: (
                      <SkillCollapseLabel
                        title={skill.display_name}
                        tags={
                          <>
                            {skill.domain ? <Tag className="!m-0">{skill.domain}</Tag> : null}
                            {skill.status === "active" ? (
                              <Tag color="green" className="!m-0">
                                active
                              </Tag>
                            ) : null}
                          </>
                        }
                      />
                    ),
                    children: <Paragraph type="secondary">{skill.description || copy.noDescription}</Paragraph>
                  }))}
                />
                {visibleSystemSkills.length > SYS_PAGE_SIZE ? (
                  <Pagination
                    size="small"
                    current={safeSysPage}
                    pageSize={SYS_PAGE_SIZE}
                    total={visibleSystemSkills.length}
                    showSizeChanger={false}
                    onChange={page => setSysPage(page)}
                    className="mt-2 flex justify-end"
                  />
                ) : null}
              </Card>
            </Col>
          ) : null}
        </Row>
      </Spin>

      <Modal
        title={copy.uploadPrivateSkill}
        open={uploadModalOpen}
        onCancel={() => setUploadModalOpen(false)}
        footer={null}
        destroyOnClose
      >
        <SkillUploadForm
          onCancel={() => setUploadModalOpen(false)}
          onSuccess={() => {
            setUploadModalOpen(false);
            loadSkills();
          }}
        />
      </Modal>
    </div>
  );
}
