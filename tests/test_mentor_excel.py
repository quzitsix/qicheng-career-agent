import json
import stat
from pathlib import Path

from openpyxl import Workbook

from app.mentor_excel import load_mentor_workbook, load_or_create_secret, write_private_json

HEADERS = ['行业分组', '志愿者', '来源', '姓名', '账号', '密码', '工作地', '电话号', '电子邮箱',
           '单位性质', '工作单位', '工作部门', '职务', '本科院系', '硕士院系', '博士院系', '博士后院系',
           '本科入学年份', '硕士入学年份', '博士入学年份', '博士后进站年份', '额外秘密列']


def make_workbook(path):
    book = Workbook()
    sheet = book.active
    sheet.title = '导师总名单'
    sheet.append(HEADERS)
    sheet.append(['信息传输与信息技术服务①\n2人', '志愿者秘密', '续聘', '张保密甲', 'account-secret-canary',
                  'password-secret-canary', '北京市海淀区详细地址998号', '13800000000', 'private@example.com',
                  '民营企业', '精确公司秘密A', '产品部', '精确职务秘密-产品经理', '计算机系', '', '', '',
                  2001, '', '', '', 'do-not-publish-canary'])
    sheet.append([None, None, None, '李保密乙', 'another-private-account', 'another-password', '未知地址秘密',
                  '13900000000', 'other@example.com', '秘密机构类型', '精确公司秘密B', '私密部门名称',
                  '私密职务名称', '私密院系名称', '', '', '', 1992])
    sheet.merge_cells('A2:A3')
    sheet.append([None, None, None, '张保密甲', '', '', '', '', '', '', '精确公司秘密A'])
    sheet.append(['没有列入白名单的行业秘密', '', '', '未知姓名丙', '', '', '', '', '', '', '公司秘密C'])
    duplicate_sheet = book.create_sheet('按志愿者分工')
    duplicate_sheet.append(['入群'] + HEADERS[1:])
    duplicate_sheet.append(['是', 'ignored-volunteer-canary', '', '王不应导入', '', '', '', '', '', '', '其他公司'])
    book.save(path)
    book.close()


def test_privacy_allowlist_and_sheet_selection(tmp_path):
    path = tmp_path / 'mentors.xlsx'
    make_workbook(path)
    catalog, mapping = load_mentor_workbook(path, b'test-secret-at-least-16-bytes')
    assert len(catalog['mentors']) == 3
    assert catalog['import_report']['counts']['duplicate_rows'] == 1
    assert catalog['import_report']['ignored_worksheets'] == 1
    first, second, third = catalog['mentors']
    assert first['title'] == '张导师 · 001'
    assert first['industry'] == second['industry'] == '信息传输与信息技术服务'
    assert first['city'] == '北京'
    assert first['role_family'] == '产品与运营'
    assert first['education_domains'] == ['计算机']
    assert second['city'] == '地区未公开'
    assert second['organization_type'] == '机构类型未公开'
    assert second['role_family'] == '岗位方向待补充'
    assert second['education_domains'] == []
    assert third['industry'] == '行业待补充'
    assert mapping['mentors'][first['id']]['rows'] == [2, 4]
    public = json.dumps(catalog, ensure_ascii=False)
    private = json.dumps(mapping, ensure_ascii=False)
    for canary in ['张保密甲', '李保密乙', '未知姓名丙', '王不应导入', 'account-secret-canary', 'password-secret-canary',
                   'another-password', '13800000000', '13900000000', 'private@example.com', '志愿者秘密',
                   '精确公司秘密A', '精确公司秘密B', '精确职务秘密', '私密部门名称', '私密职务名称',
                   '私密院系名称', 'do-not-publish-canary', 'ignored-volunteer-canary', '详细地址998号']:
        assert canary not in public
        assert canary not in private
    assert all(not item['available'] and not item['has_digital_mentor'] and not item['is_sample'] for item in catalog['mentors'])


def test_stable_identifiers_and_private_files(tmp_path):
    path = tmp_path / 'mentors.xlsx'
    make_workbook(path)
    secret_path = tmp_path / 'runtime' / 'identity_secret'
    secret = load_or_create_secret(secret_path)
    assert load_or_create_secret(secret_path) == secret
    first, mapping = load_mentor_workbook(path, secret)
    second, _ = load_mentor_workbook(path, secret)
    assert [item['id'] for item in first['mentors']] == [item['id'] for item in second['mentors']]
    other, _ = load_mentor_workbook(path, b'different-at-least-16-byte-key')
    assert first['mentors'][0]['id'] != other['mentors'][0]['id']
    output = tmp_path / 'runtime' / 'map.json'
    write_private_json(output, mapping)
    assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(output.read_text()) == mapping
