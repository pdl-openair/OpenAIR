#!/usr/bin/env python3
"""统一流水线入口：按顺序运行 three-step pipeline

行为：从环境或命令行读取参数并依次执行三个模块（similar_retrieval, statistic_window, recommend_recipe）。
在 `DEBUG` 模式下会打印输入参数与每步的 stdout/ stderr。
"""
import os
import sys
import argparse
import logging
import subprocess


def setup_logging():
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format="%(asctime)s %(name)s %(levelname)s: %(message)s")
    return logging.getLogger("runner")


def run_cmd(cmd, logger, debug=False):
    logger.info("Running: %s", " ".join(cmd))
    if debug:
        r = subprocess.run(cmd, capture_output=True, text=True)
        logger.debug("exit=%s stdout=\n%s", r.returncode, r.stdout)
        if r.stderr:
            logger.debug("stderr=\n%s", r.stderr)
        r.check_returncode()
    else:
        subprocess.run(cmd, check=True)


def main():
    logger = setup_logging()
    parser = argparse.ArgumentParser(description="Run recommend three-step pipeline")
    parser.add_argument("--query", default=os.getenv("PIPELINE_QUERY", "用助熔剂法制备AlInSe₃"),
                        help="用户查询（目标材料描述）")
    parser.add_argument("--top-k", type=int, default=int(os.getenv("PIPELINE_TOPK", "30")),
                        help="返回的相似材料数量")
    parser.add_argument("--data-dir", default=os.getenv("DATA_DIR", "./data"),
                        help="数据目录路径（默认：./data）")
    parser.add_argument("--material", default=None,
                        help="材料化学式（用于生成输出文件名，如 BiSiTe3）")
    parser.add_argument("--debug", action="store_true", default=(os.getenv("LOG_LEVEL", "INFO").upper()=="DEBUG"),
                        help="启用调试模式")
    parser.add_argument("--similar-output", default=None,
                        help="相似检索输出文件路径")
    parser.add_argument("--window-output", default=None,
                        help="统计窗口输出文件路径")
    parser.add_argument("--recommend-output", default=None,
                        help="推荐配方输出文件路径")
    parser.add_argument("--feature-output", default=None,
                        help="配方特征输出文件路径")
    args = parser.parse_args()

    debug = args.debug
    logger.info("Pipeline start. data_dir=%s query=%s top_k=%s debug=%s", args.data_dir, args.query, args.top_k, debug)

    def get_material_name(query):
        """从查询中提取材料化学式"""
        import re
        patterns = [
            r'([A-Z][a-z]?\d*(?:[A-Z][a-z]?\d*)*)',  # 如 BiSiTe3, AlInSe3
            r'([A-Z][a-z]*(?:\d+)?(?:[A-Z][a-z]*(?:\d+)?)*)',  # 如 GaN
        ]
        for pattern in patterns:
            match = re.search(pattern, query)
            if match:
                name = match.group(1)
                name = re.sub(r'(\d+)', r'\1', name)
                return name
        return "unknown"

    material_name = args.material or get_material_name(args.query)
    logger.info("Material name: %s", material_name)

    # Build paths
    kb = os.path.join(args.data_dir, "knowledge_base", "knowledge_base_processed.jsonl")
    intuition = os.path.join(args.data_dir, "similar_mates", "intuition_template.jsonl")
    requirement = os.path.join(args.data_dir, "similar_mates", "how_to_parse_intuition.jsonl")
    similar_out = args.similar_output or os.path.join(args.data_dir, "similar_mates", f"similar_output_{material_name}.jsonl")

    window_in = similar_out
    window_template = os.path.join(args.data_dir, "recommand_window", "input_template.jsonl")
    window_out = args.window_output or os.path.join(args.data_dir, "recommand_window", f"window_output_{material_name}.jsonl")

    recommend_input = os.path.join(args.data_dir, "recommand_recipe", "input_real.jsonl")
    recommend_out = args.recommend_output or os.path.join(args.data_dir, "recommand_recipe", f"recommend_output_{material_name}.jsonl")
    feature_out = args.feature_output or os.path.join(args.data_dir, "recommand_recipe", f"feature_output_{material_name}.jsonl")

    # Step 1
    cmd1 = [sys.executable, "-m", "src.similar_retrieval",
            "--query", args.query,
            "--top-k", str(args.top_k),
            "--kb-path", kb,
            "--intuition-template", intuition,
            "--requirement-template", requirement,
            "--output", similar_out]
    run_cmd(cmd1, logger, debug=debug)

    # Step 2
    cmd2 = [sys.executable, "-m", "src.statistic_window",
            "--similar-file", window_in,
            "--input-file", window_template,
            "--output-file", window_out]
    run_cmd(cmd2, logger, debug=debug)

    # Step 3
    cmd3 = [sys.executable, "-m", "src.recommend_recipe",
            "--similar-file", similar_out,
            "--window-file", window_out,
            "--input-file", recommend_input,
            "--output-file", recommend_out,
            "--feature-output", feature_out]
    run_cmd(cmd3, logger, debug=debug)

    logger.info("Pipeline finished. outputs: %s, %s", recommend_out, feature_out)


if __name__ == "__main__":
    main()
