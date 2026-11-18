import json
from app.catalog import Catalog


def test_product_query_does_not_expand_to_generic_management(tmp_path):
    (tmp_path / 'imported_mentors.json').write_text(json.dumps({'mentors': [
        {'id': 'product', 'title': '张导师 · 001', 'description': '职业类别：产品与运营', 'tags': ['产品与运营'], 'city': '北京'},
        {'id': 'executive', 'title': '李导师 · 002', 'description': '组织管理，经济管理', 'tags': ['组织管理', '经济管理'], 'city': '北京'},
    ]}, ensure_ascii=False))
    catalog = Catalog(tmp_path)
    assert [item['id'] for item in catalog.find_mentors(query='产品')] == ['product']
    assert [item['id'] for item in catalog.find_mentors(query='请介绍张导师 · 001的职业背景')] == ['product']
    assert catalog.find_mentors(query='产品', city='上海') == []
