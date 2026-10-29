#!/usr/bin/env python3
"""Offline smoke for the self-contained P0.7a OpenAlex + arXiv slice."""
from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))
from adapter.retrieval_adapter import run_retrieval  # noqa: E402

OPENALEX = {"results": [{
    "id": "https://openalex.org/W7000000001",
    "doi": "https://doi.org/10.0000/openair.openalex",
    "display_name": "OpenAIR self-contained OpenAlex candidate",
    "publication_year": 2025,
    "abstract_inverted_index": {"OpenAIR": [0], "metadata": [1], "candidate": [2]},
    "ids": {"openalex": "https://openalex.org/W7000000001"},
    "primary_location": {"landing_page_url": "https://openalex.org/W7000000001"},
}]} 
ARXIV = b'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry><id>https://arxiv.org/abs/2501.07001v1</id><published>2025-01-02T00:00:00Z</published>
  <title>OpenAIR self-contained arXiv candidate</title><summary>Metadata only.</summary>
  <author><name>OpenAIR Test</name></author><link title="pdf" href="https://arxiv.org/pdf/2501.07001v1" type="application/pdf"/>
  <arxiv:primary_category term="cond-mat.mtrl-sci"/></entry>
</feed>'''

class _DaemonThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        del fmt, args
    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith('/works'):
            body=json.dumps(OPENALEX).encode(); content='application/json'
        elif self.path.startswith('/api/query'):
            body=ARXIV; content='application/atom+xml'
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.send_header('Content-Type',content); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)

def main() -> int:
    server=_DaemonThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    old=os.environ.copy()
    try:
        port=server.server_address[1]
        os.environ.update({'OPENALEX_API_BASE_URL':f'http://127.0.0.1:{port}/works','ARXIV_API_BASE_URL':f'http://127.0.0.1:{port}/api/query','OPENTRAJECTORY_RETRIEVAL_DISABLE_RATE_LIMIT':'1'})
        output=run_retrieval(skill2_data={'user_query':'lithium metal battery','expanded_query_variants':[{'query':'"lithium metal battery" AND "fast charging"','variant_type':'strict','search_intent':'core_topic'}]},target_sources=['openalex','arxiv'],max_results_per_query=1,max_total_candidates=4,timeout_seconds=10,allow_partial_success=True)
        assert output['retrieval_report']['source_counts']=={'openalex':1,'arxiv':1}, output
        assert output['retrieval_report']['total_candidates']==2, output
        assert {item['source_name'] for item in output['metadata_candidates']}=={'openalex','arxiv'}, output
        assert all(item['provenance'] for item in output['metadata_candidates']), output
        print('PASS: OpenAIR P0.7a self-contained OpenAlex + arXiv offline smoke')
        return 0
    finally:
        os.environ.clear(); os.environ.update(old)
        server.shutdown(); server.server_close(); thread.join(timeout=5)

if __name__=='__main__':
    raise SystemExit(main())
