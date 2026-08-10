#!/usr/bin/env python3
import asyncio
import logging
import sys
import tempfile
from pathlib import Path

from project_service.client import ProjectClient, RPCError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("bootstrap_douyin")

PROJECT_NAME = "抖音电商带货"
PROJECT_DESC = (
    "抖音电商带货自动化运营项目。整合 openclaw 选品/上架/视频/发布流水线与 "
    "fusion-agent-studio Agent 编排。目标：日均销售额过万。"
)

INSTRUCTION = """# 抖音电商带货 — 项目流程规则手册

## 目标
日均 GMV 10000 元。

## 一、选品（每日 01:00）
1. 趋势侦察：抓蝉妈妈三榜（商品热推/潜力爆品/直播商品）+ 抖店小店榜，聚类当日飙升品类。
2. 选品中心：按飙升关键词在抖店选品中心抓 20 个一件代发候选，存 product_catalog/{date}/{kw}/{pid}/。
3. AI 评分排序：评分=佣金30+销量25+评分25+退货20。
   A级(≥80)立即上架；B级(60-79)优化后上架；C级(<60)放弃。
   回流加权：上次成交率高品类 +5，退货率高品类 -5，新品类不加权。
取 Top5 A 级进入上架。

## 二、上架+宣传（01:30 起双账号）
铺货(markup 120/stock 200/一件代发) → 质检 → 确认发布。
质检清单：标题无违禁词(第一/最/国家级)、价格不高于市场价200%、图片无水印二维码、类目匹配、视频15-30秒无夸大宣传。
吸粉号(douyin-operation)：02:00制作→11:00午间发布→17:00晚间发布+日报，前10条不挂车养标签。
带货号(doudian-deliver)：<500粉种草不挂链；≥500粉挂小黄车。
视频：<500粉用 product_video_maker(商品图+edge-tts晓晓)；≥500粉 A级用 comfyui 数字人口播。
发布：douyin_upload skill 调 sau CLI。时段 11:30/18:00-20:00/21:00-22:00。

## 三、每日总结（23:00）
四段式日报：选品过程(命中率/品类偏好) + 上架过程(铺货成功率/QA驳回TOP) + 商品营销(播放/完播/小黄车点击/加购/转化) + 商品交付(成交/退货/售后)。
改进规则：退货率>10%品类下次选品-5；完播率<35%换钩子重测；转化率<1%且曝光>5000触发主图/价格优化；小黄车点击率<3%迭代CTA。

## 四、销售额过万分阶段
阶段1(1-15天,0→500粉)：养号养标签，不带货，GMV 0。
阶段2(16-30天,500→2000粉)：开小黄车，日1-2条带货视频，DOU+定向达人相似粉丝，GMV 500-2000/日。
阶段3(31-60天,2000→5000+粉)：矩阵号+日2条+数字人直播(GPM>500)+高客单专场，GMV 10000/日。
漏斗：5万播放×3%点击×30%加购×1%成交≈15单；中低客单冲单量，高客单提GMV。

## 五、红线
不刷粉/互关；AI内容须标注；简介不留微信电话(1000粉后企业号)；食品/医药/化妆品无绝对化用语；退货率>20%下架。

## 六、执行引擎
主路径：fusion-studio DAG画布 → fusion-agent-studio AgentGraph + AgentRuntime 执行（UDS JSON-RPC）。
项目容器：本项目（fusion-projects）存共享知识库与流程快照，所有 Agent 共用。
视频渲染：fusion-comfyui（8188）。LLM：fusion-mlx（11434）。
"""

KNOWLEDGE_FILES = {
    "选品规则": """# 选品规则

## 评分公式
总分 = 佣金得分(0-30) + 销量得分(0-25) + 评分得分(0-25) + 退货得分(0-20)

| 维度 | 满分 | 满分条件 | 半分条件 |
|------|------|----------|----------|
| 佣金率 | 30 | ≥20% | 15-19%得15 |
| 月销 | 25 | ≥300 | 100-299得10 |
| 评分 | 25 | ≥4.8 | 4.5-4.79得10 |
| 退货率 | 20 | ≤5% | 5-10%得10 |

分级：A≥80 立即上架；B 60-79 优化后上架；C<60 放弃。

## 热点加权
- 当日飙升品类（来自蝉妈妈三榜）：评分 +5
- 上次带货成交率高的品类：+5
- 上次退货率高的品类：-5
- 新品类（无回流数据）：不加权

## 数据来源
- 蝉妈妈 chanmama.com：商品热推榜(按带货达人数)/潜力爆品榜(按近期销量)/直播商品榜/抖店小店榜(按销售额)
- 抖店选品中心 fxg.jinritemai.com：一件代发商品
- 脚本：fetch_hotinfo.py / scrape_products.py / score_products.py
""",
    "文案模板": """# 文案模板

## 黄金3秒钩子四公式
A. 冲突："千万别买XX，除非你看完这个"
B. 价值："花XX元解决了困扰我3年的问题"
C. 悬念："发现一个XX行业不想让你知道的秘密"
D. 共鸣："有没有人每次XX都崩溃"

## 视频结构（15-30秒）
0-3s   黄金钩子
3-15s  第一个干货点（具体方法）
15-30s 第二个干货点（使用场景/效果对比）
结尾5s 引导："关注我，下期讲更狠的" / "要清单评论区扣1"

## 种草视频文案规范（<500粉）
① 真实感引入："用了两周了，来给你们说说真实感受"
② 具体产品细节（材质对比，不说"好"要说"好在哪"）
③ 亲身使用场景（具体到绿萝薄荷这种细节）
④ 价格锚点（"一顿外卖钱"生活化类比）
⑤ 购买引导（"链接放评论区了，直接去拍"）

## 带货视频文案规范（≥500粉）
痛点+卖点+价格+CTA，必含小黄车引导话术。

## 脚本生成
douyin_prompt_gen skill 调 fusion-mlx，输出 JSON：title/scenes[narrator,image_keywords]/tags
""",
    "避坑清单": """# 避坑清单

| 坑 | 后果 | 规避 |
|----|------|------|
| 刷粉/互关 | 标签错乱，永久限流 | 只做自然涨粉 |
| AI内容不标注 | 限流50%播放 | 视频开头标注"AI辅助创作" |
| 前10条挂车带货 | 低权重账号直接限流 | 纯做价值内容，不挂车 |
| 日更3-8条低质内容 | 算法反感，权重走低 | 每周3-4条精品 |
| 删除低数据视频 | 删除记录影响权重 | 隐藏不删除 |
| 简介留微信/电话 | 直接暗限流 | 1000粉后走企业号路径 |
| 追热点跨赛道 | 标签错乱，后续没流量 | 只蹭相关领域热点 |
| 绝对化用语(第一/最/国家级) | 违规下架 | 严格合规 |
| 食品/医药/化妆品夸大功效 | 广告法违规 | 无虚假宣传 |
| 退货率>20% | 影响店铺体验分 | 自动下架降权 |

## 算法优先级
完播率 > 点赞率 > 评论率 > 分享率
完播率目标≥55%，关注率≥3%，收藏率≥3%。
""",
    "日报模板": """# 每日运营日报模板

## 选品过程
- 候选商品: N 件 | 入选 Top5: M 件 | 命中率: M/N
- A级: x | B级: y | C级(放弃): z
- 命中品类: ...
- 🎯 改进: 品类偏好调整建议

## 上架过程
- 铺货: N 件 | 成功: M 件 | 成功率: M/N
- QA 通过: x | 驳回: y | 驳回原因 TOP: ...
- 🎯 改进: 类目匹配/标题优化

## 商品营销
- 发布视频: N 条 | 总播放: ... | 完播率: ...% (目标≥55%)
- 互动: 点赞/评论/分享
- 小黄车点击率: ...% (目标>3%) | 加购率: ...% | 转化率: ...% (目标>1%)
- 涨粉: +N (总: ...) | 距5000粉还差 ...
- 🎯 爆款归因: ... | 钩子迭代建议 | DOU+投放建议

## 商品交付
- 成交: N 单 | GMV: ... 元
- 退货: N 单 | 退货率: ...%
- 售后: ...
- 🎯 改进: 退货品类降权 / 物流时效 / 售后话术

## 里程碑
阶段进度（0→500→2000→5000粉 / GMV阶段目标）
""",
    "回流指标": """# 回流指标定义

## 选品回流（来自 doudian-deliver Task2007）
文件：shared/metrics/{date}/doudian_metrics.json

字段：
- category: 品类
- conversion_rate: 成交转化率（高→选品+5加权）
- refund_rate: 退货率（>10%→-5加权，>20%→下架）
- gmv: 成交额
- order_count: 订单数

## 抖音回流（来自 douyin-operation-evening Task3215）
文件：shared/metrics/douyin_metrics.json

字段：
- play_count / completion_rate / like_rate / comment_rate / share_rate
- follow_growth / total_fans
- cart_click_rate / add_cart_rate / convert_rate（≥500粉带货阶段）

## 改进规则触发阈值
- 退货率 > 10% → 选品 -5
- 退货率 > 20% → 下架
- 完播率 < 35% → 换钩子公式重测
- 转化率 < 1% 且 曝光 > 5000 → 主图/价格优化重上架
- 小黄车点击率 < 3% → CTA话术迭代
""",
}


async def main() -> int:
    client = ProjectClient()

    logger.info("检查 fusion-project-svc 连通性...")
    try:
        pong = await client.call("ping", {}, timeout=5.0)
        logger.info("服务在线: ping=%s", pong)
    except (RPCError, ConnectionError, OSError) as e:
        logger.error("无法连接 fusion-project-svc（%s）。请先运行: cd ~/fusion/fusion-projects && ./start.sh start", e)
        return 1

    existing = await client.list_projects(include_archived=False)
    proj = None
    for row in existing or []:
        if row.get("name") == PROJECT_NAME:
            proj = row
            logger.info("项目已存在，复用: id=%s", proj["id"])
            break

    if proj is None:
        logger.info("创建项目: %s", PROJECT_NAME)
        proj = await client.create_project(
            name=PROJECT_NAME,
            description=PROJECT_DESC,
            prompt_merge_mode="AGENT_FIRST",
            rag_mode="AUTO",
            rag_top_k=5,
            rag_threshold=0.65,
        )
        logger.info("项目已创建: id=%s", proj["id"])
    project_id = proj["id"]

    logger.info("写入项目指令（流程规则手册）...")
    ic = await client.save_instruction(project_id, INSTRUCTION)
    logger.info("指令已保存: updated_at=%s", ic.get("updated_at"))

    folder_ids: dict[str, str] = {}
    for folder_name in KNOWLEDGE_FILES:
        folders = await client.call(
            "project.knowledge.folder.list", {"project_id": project_id}
        )
        fid = None
        for f in folders or []:
            if f.get("name") == folder_name:
                fid = f["id"]
                logger.info("文件夹已存在，复用: %s id=%s", folder_name, fid)
                break
        if fid is None:
            folder = await client.call(
                "project.knowledge.folder.create",
                {"project_id": project_id, "name": folder_name},
            )
            fid = folder["id"]
            logger.info("文件夹已创建: %s id=%s", folder_name, fid)
        folder_ids[folder_name] = fid

    with tempfile.TemporaryDirectory() as tmpdir:
        for folder_name, content in KNOWLEDGE_FILES.items():
            fname = folder_name + ".md"
            fpath = Path(tmpdir) / fname
            fpath.write_text(content, encoding="utf-8")

            files = await client.call(
                "project.knowledge.file.list",
                {"project_id": project_id, "folder_id": folder_ids[folder_name]},
            )
            existing_file = None
            for fobj in files or []:
                if fobj.get("original_name") == fname:
                    existing_file = fobj
                    break

            if existing_file:
                kfile = await client.call(
                    "project.knowledge.file.replace",
                    {"file_id": existing_file["id"], "source_path": str(fpath)},
                )
                logger.info("知识文件已更新: %s/%s", folder_name, fname)
            else:
                kfile = await client.call(
                    "project.knowledge.file.upload",
                    {
                        "project_id": project_id,
                        "source_path": str(fpath),
                        "original_name": fname,
                        "folder_id": folder_ids[folder_name],
                        "mime_type": "text/markdown",
                    },
                )
                logger.info("知识文件已上传: %s/%s id=%s", folder_name, fname, kfile.get("id"))

    logger.info("触发 RAG 索引（依赖 fusion-rag 11436）...")
    total_files = 0
    indexed_ok = 0
    for folder_name in KNOWLEDGE_FILES:
        fid = folder_ids[folder_name]
        try:
            files = await client.call(
                "project.knowledge.file.list",
                {"project_id": project_id, "folder_id": fid},
            )
            for fobj in files or []:
                if fobj.get("original_name") != folder_name + ".md":
                    continue
                total_files += 1
                if fobj.get("index_status") == "INDEXED":
                    indexed_ok += 1
                    logger.info("  已索引(跳过): %s", fobj.get("name"))
                    continue
                r = await client.call(
                    "project.rag.index_file",
                    {"file_id": fobj["id"]},
                    timeout=60.0,
                )
                chunks = r.get("chunks", 0) if isinstance(r, dict) else 0
                logger.info("  已索引: %s chunks=%s", fobj.get("name"), chunks)
                indexed_ok += 1
        except RPCError as e:
            logger.warning("  RAG 索引失败 %s（fusion-rag 未启动?）: %s", folder_name, e)
    logger.info("RAG 索引完成: %d/%d 文件已索引", indexed_ok, total_files)

    DEFAULT_AGENT_PREFERRED = ["ExecBot", "DispatchBot", "ToolBot", "SoulBot"]
    logger.info("列出可用 Agent（来自 fusion-agent-studio api_server 11455）...")
    bound_agent_id = None
    try:
        agents = await client.call("project.agent.list", {})
        agent_count = len(agents or [])
        logger.info("可用 Agent 数: %d", agent_count)
        for a in (agents or [])[:10]:
            logger.info("  - %s: %s", a.get("agent_id"), a.get("name"))

        if agent_count == 0:
            logger.warning(
                "可用 Agent 数为 0，无法绑定。检查 fusion-agent-studio 是否已启动 "
                "(~/fusion/fusion-agent-studio/start.sh status) 及 index.json 是否已生成。"
            )
        else:
            existing_binding = await client.call(
                "project.agent.get", {"project_id": project_id}
            )
            already = existing_binding.get("agent_id") if existing_binding else None
            if already:
                logger.info("已存在 Agent 绑定，复用: %s", already)
                bound_agent_id = already
            else:
                pick = None
                for pref in DEFAULT_AGENT_PREFERRED:
                    for a in agents or []:
                        if a.get("name") == pref:
                            pick = a
                            break
                    if pick:
                        break
                if not pick:
                    pick = (agents or [])[0]
                bound_agent_id = pick.get("agent_id")
                logger.info("绑定默认 Agent: %s (%s)", pick.get("name"), bound_agent_id)
                await client.call(
                    "project.agent.set",
                    {"project_id": project_id, "agent_id": bound_agent_id, "merge_mode": "AGENT_FIRST"},
                )
                logger.info("默认 Agent 已绑定")
    except (RPCError, Exception) as e:
        logger.warning("Agent 列举/绑定跳过（fusion-agent-studio 可能未启动）: %s", e)

    logger.info("记录审计日志：项目骨架初始化完成")
    try:
        await client.call(
            "project.audit.log",
            {
                "project_id": project_id,
                "action": "bootstrap_douyin_ecommerce",
                "details": "项目骨架初始化：指令+5知识文件夹+5知识文件+默认Agent绑定",
            },
        )
    except RPCError as e:
        logger.warning("审计日志写入跳过: %s", e)

    logger.info("=" * 60)
    logger.info("✅ 抖音电商带货项目骨架搭建完成")
    logger.info("   project_id  = %s", project_id)
    logger.info("   指令手册    = %d 字符", len(INSTRUCTION))
    logger.info("   知识文件夹  = %d 个", len(KNOWLEDGE_FILES))
    logger.info("   RAG 已索引  = %d/%d", indexed_ok, total_files)
    if bound_agent_id:
        logger.info("   默认 Agent  = %s", bound_agent_id)
    else:
        logger.info("   默认 Agent  = 未绑定")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
