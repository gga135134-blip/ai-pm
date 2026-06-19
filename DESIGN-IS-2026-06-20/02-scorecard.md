# AI-PM 设计审核评分卡
审核日期：2026-06-20
审核页面：项目详情页（看板/任务树）+ 知识库页

---

1. Good design is innovative — Score: 2/3
   Evidence: "AI指挥AI工人"范式在PM工具中未见先例；AI作战室实时显示agent步骤属首创；但视觉/交互层全部沿用标准SaaS卡片模式
   Justification: 产品概念革新明显，但UI层未能将这种创新体现在形式上，停留在"功能新、界面旧"

2. Good design makes a product useful — Score: 1/3
   Evidence: 到达核心界面（看板）前需经过7层独立区域；项目页内7个Tab产生大量导航开销；主要动作"启动自动执行"被淹没在多个区域中
   Justification: 主任务可以完成，但用户在到达目标前必须处理大量无关决策，形成不必要的认知绕路

3. Good design is aesthetic — Score: 1/3
   Evidence: 项目页同时出现5种accent色（蓝/紫/绿/红/橙）；知识库顶部7个按钮用3种颜色混排；无统一可见的颜色系统
   Justification: 颜色系统缺乏约束，每个功能区独立配色，整体视觉噪音高于3个不一致的阈值

4. Good design makes a product understandable — Score: 2/3
   Evidence: 顶部导航（AI Chat/总览/项目/知识库/设置）语义清晰；P1-P5优先级无界面内说明；"AI作战室"对新用户不透明
   Justification: 主导航清晰，但次级概念需要先验知识，1-2个控件不经提示无法准确命名

5. Good design is unobtrusive — Score: 1/3
   Evidence: 项目页：AI执行面板、AI拆解输入框、手动添加表单三个工具区在Tab上方叠加；知识库：IMA蓝色卡片横贯首屏
   Justification: 工具chrome与内容chrome争夺视觉主导权，内容作为"图"、UI作为"底"的关系倒置

6. Good design is honest — Score: 3/3
   Evidence: 实时显示AI费用（$0.0655）；授权模式明确标注"每步确认"；按钮标签（AI拆解/启动自动执行）与实际行为1:1对应；无营销夸大
   Justification: 产品在诚实度上表现最好，所有声明都有真实功能支撑

7. Good design is long-lasting — Score: 2/3
   Evidence: 功能主义的卡片布局不会显老；多色按钮行（彩虹按钮）是2022-2023的短暂流行，有1个时代印记
   Justification: 整体视觉语言耐久，但7按钮彩色行的设计模式已开始显得过时

8. Good design is thorough down to the last detail — Score: 1/3
   Evidence: 看板空列（执行中0、审核中0）未显示空状态引导文案；截图中未发现明显的focus/disabled样式；任务卡片缺少hover状态证据
   Justification: 核心状态（done/pending）处理到位，但空状态、focus环、disabled态均无明显设计

9. Good design is environmentally friendly — Score: 2/3
   Evidence: Jinja2服务端渲染估计JS<200KB；无明显idle动画；但7个顶部按钮+IMA卡片制造高注意力成本（认知污染）
   Justification: 技术层面轻量，但界面注意力消耗过高，用户认知负荷偏重

10. Good design is as little design as possible — Score: 0/3
    Evidence: 项目详情页：看板上方7层独立控制区；知识库：首屏7个并排操作按钮；6列看板含4个空列占据横向空间
    Justification: 页面被大量工具和控件主导，移除任何一个区域后任务流仍然完整，说明存在大量冗余设计

---

## 总分：15/30

| 原则 | 得分 |
|------|------|
| #1 创新 | 2 |
| #2 实用 | 1 |
| #3 美观 | 1 |
| #4 易懂 | 2 |
| #5 克制 | 1 |
| #6 诚实 | 3 |
| #7 耐久 | 2 |
| #8 细致 | 1 |
| #9 环保 | 2 |
| #10 极简 | 0 |
| **合计** | **15/30** |
