# 接续点 — 2026-06-20

## 已完成

- **Phase 1：颜色系统统一**（commit 2c2964e，已推送）
  - base.html 加 CSS :root 变量
  - 全站 teal/indigo/orange/purple → violet-600（AI）/ blue-600（主操作）/ amber-600（警告）
  - 11 个模板文件，grep 零残留

- **Phase 2：项目详情页重构**（本地完成，待 commit）
  - `app/templates/project_detail.html` 721行 → 全新 3 层结构
  - 头部：名称/状态/费用 + 编辑 + ▾更多(复制/删除) + AI状态小绿点（点击展开控制面板）
  - 5 Tab：看板 / 任务树 / 资料库(N) / 记录（决策+支出内部切换）/ 🤖 AI指挥（项目AI+拆解+作战室合一）
  - FAB：右下角固定「+」，展开 AI拆解 / 手动添加（浮层表单）
  - 看板：kanban-cols repeat(6,220px) 横向滚动
  - 移动端：Tab overflow-x:auto，FAB bottom:80px

## 下一步：Phase 3 — 知识库页精简

**目标：** `app/templates/notes.html` 顶部 8 个彩色按钮 → 精简为 2 个主 CTA

**现状问题（审核原文）：**
> 知识库 7 个并排彩色按钮行，导致 #3 美观(1分)、#5 克制(1分) 失败

**新结构：**
```
顶部操作栏（一行）:
  [+ 新建] [导入 ▾]    回收站图标（或放菜单里）

[导入 ▾] 下拉内容:
  📄 上传文件
  🔗 URL 导入
  📋 粘贴内容（AI 分类）
  📖 IMA 同步

[AI 整理] 的入口:
  → 移入侧边栏底部，或知识库 AI 助手页面（/notes/chat）已有，直接链接过去
```

**保留不变：**
- 侧边栏（文件夹树 + 标签）
- 笔记列表（搜索 + 筛选 + 批量操作）
- IMA 同步状态卡（如已有）

**步骤：**
1. 读 `app/templates/notes.html` 了解现有完整结构（尤其是顶部区域 lines 1-100）
2. 确认哪些按钮触发 modal/form/跳转（不要删掉功能，只是收起入口）
3. 写新的顶部操作区（保留所有 POST 路由）

## Phase 4（之后）

- 空状态设计（看板空列、知识库空状态）
- 交互细节（focus ring、disabled 状态、loading 状态）

## Phase 5（最终）

- 全局 grep 验证
- commit + 推服务器：`cd /www/wwwroot/ai-pm && git pull && pm2 restart ai-pm`
