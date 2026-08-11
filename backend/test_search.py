# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.core.embedding_client import embed_texts
from pymilvus import connections, Collection

connections.connect(host='localhost', port=19530)
col = Collection('user_memories')
col.load()
for q in ['你记得我叫什么吗', '我的称呼是什么', '我喜欢什么风格', '小赵']:
    vec = embed_texts([q])[0]
    res = col.search(data=[vec], anns_field='vector', param={'metric_type':'IP','params':{'nprobe':10}},
                     limit=3, expr='tenant_id == 1 and user_id == 1', output_fields=['content'])
    print(f'{q!r} ->', [(round(h.score,4), h.entity.get('content')) for h in res[0]])
