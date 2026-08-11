# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.core.embedding_client import embed_texts
from pymilvus import connections, Collection

connections.connect(host='localhost', port=19530)
col = Collection('user_memories')
col.load()
for q in ['用户的称呼昵称', '用户偏好', '我的手机号', '小赵 称呼', '用户希望被怎么称呼']:
    v = embed_texts([q])[0]
    res = col.search(data=[v], anns_field='vector', param={'metric_type':'IP','params':{'nprobe':10}},
                     limit=3, expr='tenant_id == 1 and user_id == 1', output_fields=['content'])
    print(f'{q!r} ->', [(round(h.score,4), h.entity.get('content')) for h in res[0]])
