#!/usr/bin/env python3
"""简单 HTTP 服务：在收到 POST /api/run 时运行流水线并返回结果。

期望 JSON body: {"query": "...", "top_k": 30, "data_dir": "data", "debug": true}
环境变量优先级：请求参数 -> 环境变量 -> 默认值。
"""
import os
import sys
import logging
import subprocess
import json
from flask import Flask, request, jsonify, Response


def setup_logging():
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format="%(asctime)s %(levelname)s: %(message)s")
    return logging.getLogger("api_server")


def extract_error_message(stderr: str, returncode: int) -> str:
    """从 stderr 中提取简洁的错误信息"""
    if not stderr:
        return f"Pipeline failed with exit code {returncode}"

    lines = stderr.strip().split('\n')
    error_lines = []

    for line in lines:
        if 'ERROR' in line.upper() or 'ERROR:' in line or 'Error:' in line:
            error_lines.append(line.strip())
        elif 'Exception' in line or 'Traceback' in line:
            break

    if error_lines:
        return error_lines[0]

    last_line = lines[-1] if lines else ''
    if len(last_line) > 200:
        last_line = last_line[:200] + '...'
    return last_line if last_line else f"Pipeline failed with exit code {returncode}"


logger = setup_logging()
app = Flask(__name__)


@app.route("/", methods=["GET"])
def hello():
    return jsonify({"service": "recommend-runner", "status": "ready"})


@app.route('/api/run', methods=['POST'])
def run_pipeline():
    req = request.get_json(silent=True) or {}
    query = req.get('query') or os.getenv('PIPELINE_QUERY') or '用助熔剂法制备AlInSe₃'
    top_k = int(req.get('top_k', os.getenv('PIPELINE_TOPK', 30)))
    data_dir = req.get('data_dir', os.getenv('DATA_DIR', 'data'))
    debug = bool(req.get('debug', os.getenv('DEBUG', os.getenv('LOG_LEVEL', 'INFO').upper()=='DEBUG')))
    use_cache = bool(req.get('use_cache', False))

    logger.info('Received run request. query=%s top_k=%s data_dir=%s debug=%s use_cache=%s', query, top_k, data_dir, debug, use_cache)

    runner_script = os.path.join(os.getcwd(), 'runner', 'run_pipeline.py')
    if not os.path.exists(runner_script):
        logger.error('runner script not found: %s', runner_script)
        return Response(json.dumps({
            'code': 500,
            'status': 'error',
            'message': 'runner script not found',
            'recommend_recipes': None
        }, ensure_ascii=False), status=200, mimetype='application/json')

    cmd = [sys.executable, runner_script, '--query', query, '--top-k', str(top_k), '--data-dir', data_dir]
    if debug:
        cmd.append('--debug')

    env = os.environ.copy()
    env['USE_CACHE'] = '1' if use_cache else '0'

    try:
        if debug:
            proc = subprocess.run(cmd, text=True, timeout=int(os.getenv('PIPELINE_TIMEOUT', 1800)), env=env)
        else:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=int(os.getenv('PIPELINE_TIMEOUT', 1800)), env=env)

        recommend_out = os.path.join(data_dir, 'recommand_recipe', 'output_result-FromContainer.jsonl')
        feature_out = os.path.join(data_dir, 'recommand_recipe', 'similar_recipes_feat_result-FromContainer.jsonl')

        if os.path.exists(recommend_out):
            with open(recommend_out, 'r', encoding='utf-8') as f:
                recommend_data = json.load(f)
                recommend_data_cn = json.loads(json.dumps(recommend_data, ensure_ascii=False))
                recommend_recipes = recommend_data_cn
        else:
            recommend_recipes = None

        if proc.returncode == 0:
            return Response(json.dumps({
                'code': 200,
                'status': 'success',
                'message': 'Pipeline executed successfully',
                'recommend_recipes': recommend_recipes
            }, ensure_ascii=False), status=200, mimetype='application/json')
        else:
            error_msg = extract_error_message(proc.stderr, proc.returncode)
            logger.error('Pipeline failed: %s', error_msg)
            return Response(json.dumps({
                'code': proc.returncode,
                'status': 'error',
                'message': error_msg,
                'recommend_recipes': None
            }, ensure_ascii=False), status=200, mimetype='application/json')
    except subprocess.TimeoutExpired as te:
        logger.exception('Pipeline timeout')
        return Response(json.dumps({
            'code': 504,
            'status': 'error',
            'message': 'Pipeline timeout',
            'recommend_recipes': None
        }, ensure_ascii=False), status=200, mimetype='application/json')
    except Exception as e:
        logger.exception('Pipeline execution failed')
        return Response(json.dumps({
            'code': 500,
            'status': 'error',
            'message': str(e),
            'recommend_recipes': None
        }, ensure_ascii=False), status=200, mimetype='application/json')


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    host = os.getenv('HOST', '0.0.0.0')
    logger.info('Starting API server on %s:%s', host, port)
    app.run(host=host, port=port)
