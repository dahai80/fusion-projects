#!/usr/bin/env python3
import asyncio
import json
import logging
import socket
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("agentgraph")

SOCK = "/tmp/fusion-studio.sock"
GRAPH_NAME = "抖音电商带货-运营流水线"
GRAPH_DESC = (
    "选品(蝉妈妈热推榜+抖音热搜)→AI评分分级+LLM热点匹配→"
    "抓取商品主图→视频生成(上图下文卡片)→CDP真实发布→每日总结。"
    "自营实现，脚本在 fusion-ecommerce/scripts/。"
)

MODEL = "mlx-community/Qwen3.5-9B-4bit"

ECOM = "~/fusion/fusion-ecommerce"
SCRIPTS = ECOM + "/scripts"

NODES = [
    {
        "id": "start", "type": "start", "label": "开始", "x": 100, "y": 300,
    },
    {
        "id": "fetch_trend", "type": "tool", "label": "抓取蝉妈妈热推榜商品",
        "tool_name": "terminal",
        "tool_params": {
            "command": "python3 " + SCRIPTS + "/fetch.py",
            "cwd": ECOM,
            "timeout": 120,
        },
        "retry_on_error": True, "x": 300, "y": 300,
    },
    {
        "id": "fetch_topics", "type": "tool", "label": "抓取抖音热搜词",
        "tool_name": "terminal",
        "tool_params": {
            "command": "python3 " + SCRIPTS + "/fetch_topics.py --scroll 6",
            "cwd": ECOM,
            "timeout": 120,
        },
        "retry_on_error": True, "x": 500, "y": 300,
    },
    {
        "id": "score_products", "type": "tool", "label": "确定性base评分(佣金+销量+评分+退货)",
        "tool_name": "terminal",
        "tool_params": {
            "command": "python3 " + SCRIPTS + "/score.py",
            "cwd": ECOM,
            "timeout": 60,
        },
        "retry_on_error": True, "x": 700, "y": 300,
    },
    {
        "id": "match_topics", "type": "tool", "label": "LLM语义匹配商品↔热点(回填hot_relevance)",
        "tool_name": "terminal",
        "tool_params": {
            "command": "python3 " + SCRIPTS + "/match_topics.py",
            "cwd": ECOM,
            "timeout": 400,
        },
        "retry_on_error": True, "x": 900, "y": 300,
    },
    {
        "id": "pick_grade", "type": "llm", "label": "读取Top1商品信息",
        "model": MODEL,
        "system_prompt": (
            "你读取上游 score_products 工具打印的评分摘要(Top10 列表)。"
            "找出 rank=1 (排名第一) 的商品，从打印文本中提取它的字段，"
            "输出 JSON: {\"grade\":\"A\",\"title\":\"商品名\",\"category\":\"品类\",\"markup\":140}。"
            "grade 只能 A/B/C；markup 是加价率数字(如 140)；title/category 必须来自上游打印的真实值，不许编造。"
            "只输出这个 JSON，不要解释。"
        ),
        "temperature": 0.0, "max_tokens": 128, "retry_on_error": True,
        "disable_tools": True,
        "tool_params": {"output_schema": {
            "type": "object",
            "properties": {
                "grade": {"type": "string"},
                "title": {"type": "string"},
                "category": {"type": "string"},
                "markup": {"type": "integer"},
            },
            "required": ["grade", "title", "category", "markup"],
        }},
        "x": 1100, "y": 300,
    },
    {
        "id": "grade_is_a", "type": "condition", "label": "是否A级",
        "condition_expr": "\"A\" in structured_output", "x": 1300, "y": 200,
    },
    {
        "id": "grade_is_b", "type": "condition", "label": "是否B级",
        "condition_expr": "\"B\" in structured_output", "x": 1300, "y": 400,
    },
    {
        "id": "fetch_img_a", "type": "tool", "label": "A级-抓取商品主图(TOP1)",
        "tool_name": "terminal",
        "tool_params": {
            "command": "python3 " + SCRIPTS + "/fetch_images.py --rank 1",
            "cwd": ECOM,
            "timeout": 120,
        },
        "retry_on_error": True, "x": 1400, "y": 80,
    },
    {
        "id": "list_a", "type": "tool", "label": "A级-生成视频(TOP1)",
        "tool_name": "terminal",
        "tool_params": {
            "command": "python3 " + SCRIPTS + "/video.py --rank 1",
            "cwd": ECOM,
            "timeout": 180,
        },
        "retry_on_error": True, "x": 1600, "y": 140,
    },
    {
        "id": "fetch_img_b", "type": "tool", "label": "B级-抓取商品主图(TOP2)",
        "tool_name": "terminal",
        "tool_params": {
            "command": "python3 " + SCRIPTS + "/fetch_images.py --rank 2",
            "cwd": ECOM,
            "timeout": 120,
        },
        "retry_on_error": True, "x": 1400, "y": 240,
    },
    {
        "id": "list_b", "type": "tool", "label": "B级-生成视频(加价120)",
        "tool_name": "terminal",
        "tool_params": {
            "command": "python3 " + SCRIPTS + "/video.py --rank 2",
            "cwd": ECOM,
            "timeout": 180,
        },
        "retry_on_error": True, "x": 1600, "y": 300,
    },
    {
        "id": "reject_c", "type": "end", "label": "C级-放弃", "x": 1600, "y": 460,
    },
    {
        "id": "publish", "type": "tool", "label": "CDP真实发布到抖音",
        "tool_name": "terminal",
        "tool_params": {
            "command": "python3 " + SCRIPTS + "/publish.py --execute",
            "cwd": ECOM,
            "timeout": 120,
        },
        "retry_on_error": True, "x": 1800, "y": 220,
    },
    {
        "id": "end", "type": "end", "label": "结束(日报由编排层调skill.execute)", "x": 1900, "y": 220,
    },
]

EDGES = [
    {"source_id": "start", "target_id": "fetch_trend"},
    {"source_id": "fetch_trend", "target_id": "fetch_topics"},
    {"source_id": "fetch_topics", "target_id": "score_products"},
    {"source_id": "score_products", "target_id": "match_topics"},
    {"source_id": "match_topics", "target_id": "pick_grade"},
    {"source_id": "pick_grade", "target_id": "grade_is_a"},
    {"source_id": "grade_is_a", "target_id": "fetch_img_a", "label": "true"},
    {"source_id": "grade_is_a", "target_id": "grade_is_b", "label": "false"},
    {"source_id": "fetch_img_a", "target_id": "list_a"},
    {"source_id": "grade_is_b", "target_id": "fetch_img_b", "label": "true"},
    {"source_id": "grade_is_b", "target_id": "reject_c", "label": "false"},
    {"source_id": "fetch_img_b", "target_id": "list_b"},
    {"source_id": "list_a", "target_id": "publish"},
    {"source_id": "list_b", "target_id": "publish"},
    {"source_id": "publish", "target_id": "end"},
]


def rpc_call(method, params=None, timeout=30.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(SOCK)
    s.sendall((json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}) + "\n").encode())
    buf = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
        if buf.endswith(b"\n"):
            break
    s.close()
    resp = json.loads(buf.decode())
    if "error" in resp:
        raise RuntimeError("[{}] {}".format(resp["error"].get("code"), resp["error"].get("message")))
    return resp.get("result")


def find_existing_graph():
    result = rpc_call("graph.list")
    graphs = result.get("graphs", result) if isinstance(result, dict) else result
    for g in graphs or []:
        if g.get("name") == GRAPH_NAME:
            return g.get("graph_id") or g.get("id")
    return None


def main():
    logger.info("连通 fusion-agent-studio daemon (%s)...", SOCK)
    try:
        rpc_call("ping")
    except Exception as e:
        logger.error("无法连接 daemon: %s。先启动: ~/fusion/fusion-agent-studio/start.sh start", e)
        return 1

    existing_id = find_existing_graph()
    if existing_id:
        logger.info("同名图已存在，先删除重建: %s", existing_id)
        rpc_call("graph.delete", {"graph_id": existing_id})

    graph_data = {
        "name": GRAPH_NAME,
        "description": GRAPH_DESC,
        "nodes": NODES,
        "edges": EDGES,
        "start_node_id": "start",
        "version": "1.0",
    }
    logger.info("创建 AgentGraph: %s (%d 节点, %d 边)", GRAPH_NAME, len(NODES), len(EDGES))
    logger.info("分级路由: score(确定性)→pick_grade(llm读Top1)→is_a/is_b(true/false二段式)")
    created = rpc_call("graph.create", {"graph_data": graph_data})
    gid = created.get("graph_id")
    logger.info("图已创建: graph_id=%s", gid)

    verify = rpc_call("graph.get", {"graph_id": gid})
    v_nodes = verify.get("nodes", {})
    node_count = len(v_nodes) if isinstance(v_nodes, dict) else len(v_nodes)
    logger.info("校验: 节点=%d 边=%d start=%s", node_count, len(verify.get("edges", [])), verify.get("start_node_id"))

    logger.info("=" * 60)
    logger.info("✅ AgentGraph 搭建完成")
    logger.info("   graph_id    = %s", gid)
    logger.info("   节点        = %d", node_count)
    logger.info("   边          = %d", len(verify.get("edges", [])))
    logger.info("   流程        = start→fetch(蝉妈妈热推榜)→fetch_topics(抖音热搜)")
    logger.info("                →score(base评分)→match_topics(LLM热点匹配)→pick_grade(LLM读Top1)")
    logger.info("                A/B→fetch_img(商品主图)→video(卡片视频)→publish(CDP真实发布)→end; C→放弃")
    logger.info("                (日报由编排层 graph.execute 完成后调 skill.execute daily_report)")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
