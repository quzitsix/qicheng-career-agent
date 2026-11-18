"""Import a mentor workbook into an explicitly deidentified public catalog.

Only allowlisted professional categories are emitted. Raw names are used briefly
for pseudonyms/identity HMAC, never written to the catalog or admin mapping.
The original workbook remains the local administrator's source of truth.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

POLICY_VERSION = 'mentor-directory-deidentified-v1'
SOURCE_LABEL = '第六期导师名录 · 脱敏信息'
EXCLUDED_FIELDS = ['完整姓名', '账号', '密码', '电话号', '电子邮箱', '志愿者', '来源及续聘状态',
                   '精确工作单位', '原始工作部门', '精确职务', '具体院系及学历层次', '入学及进站年份', '入群状态']
# Values in these lists are fixed by code, not arbitrary source content.
INDUSTRIES = [
    '建筑与工程设计', '能源与化工', '智能制造与高端装备', '汽车与其他电子设备制造',
    '信息传输与信息技术服务', '轻工业、电商与地产', '生物医药与健康', '金融与投资',
    '咨询与专业服务', '文化与传媒', '教育', '科学研究与技术服务', '公共部门与社会组织', '国际组织',
]
ORGANIZATION_TYPES = {
    '国有企业': '国有企业', '民营企业': '民营企业', '外资企业': '外资企业', '科研单位': '科研机构',
    '部队': '公共部门', '社会组织': '社会组织', '其他': '其他机构', '文化体育卫生机构': '文化体育卫生机构',
    '其他事业单位': '事业单位', '高等院校': '高等院校', '中初等教育单位': '基础教育机构',
    '党政机关': '公共部门', '国际组织': '国际组织',
}
CITIES = (
    '北京 上海 天津 重庆 广州 深圳 杭州 南京 苏州 无锡 常州 宁波 温州 合肥 福州 厦门 泉州 '
    '武汉 长沙 郑州 洛阳 济南 青岛 烟台 威海 石家庄 唐山 太原 大同 西安 咸阳 成都 绵阳 '
    '昆明 贵阳 南宁 桂林 海口 三亚 南昌 九江 兰州 西宁 银川 乌鲁木齐 拉萨 呼和浩特 包头 '
    '沈阳 大连 长春 吉林 哈尔滨 大庆 珠海 佛山 东莞 中山 惠州 汕头 香港 澳门 台北 高雄'
).split()
PROVINCES = ('河北 山西 辽宁 吉林 黑龙江 江苏 浙江 安徽 福建 江西 山东 河南 湖北 湖南 广东 海南 '
             '四川 贵州 云南 陕西 甘肃 青海 台湾 内蒙古 广西 西藏 宁夏 新疆').split()
ROLE_RULES = [
    ('产品与运营', ('产品经理', '产品总监', '产品负责人', '产品管理', '产品部')),
    ('金融与投资', ('投资', '基金', '证券', '银行', '金融', '交易', '资产管理', '投行')),
    ('法律服务', ('律师', '法务', '法律', '司法')),
    ('医疗健康', ('医师', '医生', '医疗', '临床')),
    ('教学科研', ('教授', '研究员', '讲师', '教师', '教学', '博士后', '研究院', '研究所')),
    ('工程设计', ('建筑师', '规划师', '设计师', '工程设计')),
    ('技术研发', ('工程师', '技术', '研发', '开发', '架构师', '算法', '软件')),
    ('市场与运营', ('营销', '市场', '销售', '运营', '品牌')),
    ('文化传媒', ('记者', '编辑', '传媒', '新闻', '出版', '导演')),
    ('咨询服务', ('咨询', '顾问')),
    ('公共事务', ('公务员', '机关', '公共事务', '公共服务', '军官')),
    ('组织管理', ('董事', '总裁', '经理', '主任', '主管', '总监', '书记', '院长', '校长', '部长', '处长', '科长', '负责人', '管理')),
]
EDUCATION_RULES = [
    ('计算机', ('计算机', '软件', '网络', '人工智能')),
    ('电子信息', ('电子', '通信', '微纳', '集成电路')),
    ('自动化与电气', ('自动化', '电机', '电气')),
    ('经济管理', ('经管', '经济', '管理', '金融', '五道口')),
    ('建筑与土木', ('建筑', '土木', '水利', '水电', '城规')),
    ('能源动力', ('能源', '能动', '热能', '核能', '工物', '动力')),
    ('机械制造', ('机械', '精仪', '精密', '汽车', '车辆', '航天', '航空')),
    ('材料化工', ('材料', '化学', '化工')),
    ('生命健康', ('生命', '生物', '医学院', '医学', '药学')),
    ('环境科学', ('环境', '生态')),
    ('数学与物理', ('数学', '物理', '理学院', '工程力学')),
    ('人文社科', ('人文', '社会', '历史', '哲学', '政治', '中文', '外语', '心理', '新闻', '传播', '法学')),
    ('教育', ('教育', '教研')),
    ('艺术设计', ('美术', '艺术', '设计')),
]
READABLE_HEADERS = {'行业分组', '姓名', '工作地', '单位性质', '工作单位', '工作部门', '职务',
                    '本科院系', '硕士院系', '博士院系', '博士后院系'}


def _text(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def _industry(value: Any) -> str:
    value = re.sub(r'[①②③④⑤⑥⑦⑧⑨⑩]|\d+\s*人|\s+', '', _text(value))
    return value if value in INDUSTRIES else '行业待补充'


def _location(value: Any) -> str:
    value = _text(value)
    # Match only fixed city/province names; never return a street or organization.
    for city in CITIES:
        if city in value:
            return city
    for province in PROVINCES:
        if province in value:
            return province
    if any(country in value for country in ('美国', '英国', '法国', '德国', '瑞士', '日本', '韩国', '澳大利亚', '新加坡', '加拿大', '肯尼亚', '埃塞俄比亚', '埃及', '泰国', '越南', '海外', '境外')):
        return '境外'
    return '地区未公开'


def _role(department: Any, title: Any) -> str:
    combined = _text(department) + ' ' + _text(title)
    for category, terms in ROLE_RULES:
        if any(term in combined for term in terms):
            return category
    return '岗位方向待补充'


def _education(values: list[Any]) -> list[str]:
    combined = ' '.join(_text(value) for value in values)
    return [category for category, terms in EDUCATION_RULES if any(term in combined for term in terms)]


def _display_name(name: str, ordinal: int) -> str:
    # One surname character at most; unfamiliar/one-character names are fully masked.
    first = name[0] if len(name) >= 2 and re.fullmatch(r'[\u4e00-\u9fff]', name[0]) else ''
    return f'{first}导师 · {ordinal:03d}' if first else f'校友导师 · {ordinal:03d}'


def load_mentor_workbook(path: Path, secret: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return public-only catalog and private row mapping; never print source rows."""
    if len(secret) < 16:
        raise ValueError('脱敏标识密钥至少需要16字节。')
    path = Path(path)
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError('导师表超过10MB，请拆分后导入。')
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    book = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    try:
        selected = None
        for sheet in book.worksheets:
            header = [_text(cell) for cell in next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))]
            if '行业分组' in header and '姓名' in header:
                selected, columns = sheet, {value: index for index, value in enumerate(header) if value in READABLE_HEADERS}
                break
        if selected is None:
            raise ValueError('没有找到包含行业分组、姓名列的主导师表。')
        mentors, mappings, seen = [], {}, set()
        counts = {'source_rows': 0, 'imported_mentors': 0, 'skipped_blank_names': 0, 'duplicate_rows': 0,
                  'unknown_industry': 0, 'unknown_location': 0, 'unknown_role': 0}
        current_group = '行业待补充'
        # Ask openpyxl only for allowlisted columns; no source row tuple containing
        # passwords, account IDs or contact details enters application code.
        column_streams = [selected.iter_rows(min_row=2, min_col=index + 1, max_col=index + 1, values_only=True)
                          for index in columns.values()]
        for row_number, cells in enumerate(zip(*column_streams), start=2):
            allowed = {key: cell[0] for key, cell in zip(columns, cells)}
            if not any(value is not None and _text(value) for value in allowed.values()):
                continue
            counts['source_rows'] += 1
            if _text(allowed.get('行业分组')):
                current_group = _industry(allowed['行业分组'])
            name = re.sub(r'\s+', '', _text(allowed.get('姓名')))
            if not name:
                counts['skipped_blank_names'] += 1
                continue
            # HMAC makes identities stable without allowing dictionary recovery from the catalog.
            identity = json.dumps([name, _text(allowed.get('工作单位'))], ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            identifier = 'mentor-cohort6-' + hmac.new(secret, identity, hashlib.sha256).hexdigest()[:24]
            if identifier in seen:
                counts['duplicate_rows'] += 1
                mappings[identifier]['rows'].append(row_number)
                continue
            seen.add(identifier)
            location = _location(allowed.get('工作地'))
            organization = ORGANIZATION_TYPES.get(_text(allowed.get('单位性质')), '机构类型未公开')
            role = _role(allowed.get('工作部门'), allowed.get('职务'))
            education = _education([allowed.get(key) for key in ('本科院系', '硕士院系', '博士院系', '博士后院系')])
            display = _display_name(name, len(mentors) + 1)
            tags = [current_group]
            if role != '岗位方向待补充':
                tags.append(role)
            tags.extend(education)
            if location != '地区未公开':
                tags.append(location)
            tags = list(dict.fromkeys(tags))
            parts = [f'行业方向：{current_group}。', f'职业类别：{role}。']
            if organization != '机构类型未公开':
                parts.append(f'机构类型：{organization}。')
            if education:
                parts.append('学习领域：' + '、'.join(education) + '。')
            parts.append('资料已脱敏；个人经验文档与可约时段尚未接入。')
            mentors.append({
                'id': identifier, 'name': display, 'title': display, 'display_name': display,
                'type': 'mentor', 'mentor_id': identifier,
                'industry': current_group, 'city': location, 'organization_type': organization,
                'role_family': role, 'education_domains': education,
                'subtitle': current_group + (' · ' + location if location != '地区未公开' else ''),
                'description': ''.join(parts), 'tags': tags,
                'has_digital_mentor': False, 'available': False,
                'booking_method': '预约信息尚未接入，请通过项目运营方确认。',
                'url': '', 'is_sample': False, 'privacy_level': 'deidentified', 'source_label': SOURCE_LABEL,
            })
            mappings[identifier] = {'sheet': selected.title, 'rows': [row_number]}
            counts['unknown_industry'] += current_group == '行业待补充'
            counts['unknown_location'] += location == '地区未公开'
            counts['unknown_role'] += role == '岗位方向待补充'
        counts['imported_mentors'] = len(mentors)
        imported_at = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
        report = {'counts': counts, 'source_sha256': source_hash, 'imported_at': imported_at,
                  'policy_version': POLICY_VERSION, 'excluded_fields': EXCLUDED_FIELDS,
                  'industries': dict(Counter(mentor['industry'] for mentor in mentors)),
                  'ignored_worksheets': max(0, len(book.worksheets) - 1)}
        return {'mentors': mentors, 'import_report': report}, {'source_sha256': source_hash, 'policy_version': POLICY_VERSION, 'mentors': mappings}
    finally:
        book.close()


def load_or_create_secret(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, 'w') as output:
            output.write(secrets.token_hex(32) + '\n')
    path.chmod(0o600)
    try:
        secret = bytes.fromhex(path.read_text().strip())
    except ValueError:
        raise ValueError('脱敏标识密钥文件格式错误。') from None
    if len(secret) < 16:
        raise ValueError('脱敏标识密钥长度不足。')
    return secret


def write_private_json(path: Path, content: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        json.dump(content, output, ensure_ascii=False, indent=2)
        output.write('\n')
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)
