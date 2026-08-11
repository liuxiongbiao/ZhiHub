# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.services.memory_service import extract_facts, save_memory, search_memory
import logging
logging.basicConfig(level=logging.INFO)

facts = extract_facts('以后请叫我小赵')
print('facts:', facts)
save_memory(1, 1, facts)
print('saved')
from pymilvus import connections, Collection
connections.connect(host='localhost', port=19530)
col = Collection('user_memories')
col.flush()
print('count:', col.num_entities)
rows = col.query(expr='tenant_id >= 0', output_fields=['fact_type','content'], limit=10)
print('rows:', rows)
print('search:', search_memory(1, 1, '你记得我叫什么吗'))
