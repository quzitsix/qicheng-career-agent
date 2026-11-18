"""Small, replaceable keyword catalog for the local demo (not vector RAG)."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

try:
    from zoneinfo import ZoneInfo
    LOCAL_ZONE = ZoneInfo("Asia/Shanghai")
except (ImportError, KeyError):
    LOCAL_ZONE = None


_SAMPLE_SOURCE = "虚构演示资料 · 不代表真实校内资源"
_STOPWORDS = (
    "请问", "有没有", "能不能", "可以", "适合", "帮我", "推荐", "给我", "想要", "想了解", "了解", "哪些", "一些", "一下", "一个", "关于", "如何", "怎么", "什么", "我是", "我们", "你们", "他们", "现在", "最近", "本周", "下周", "活动", "导师", "老师", "校友", "同学", "学生", "希望", "需要", "有什么", "想找", "我想", "的", "了", "吗", "呢", "啊", "是", "和", "与"
)
_SYNONYMS = {
    "产品": "产品经理 产品管理", "互联网": "产品经理", "数据": "数据分析", "ai": "人工智能", "人工智能": "数据分析",
    "读研": "科研 研究生", "保研": "科研 读研", "迷茫": "职业探索 职业迷茫", "方向": "职业探索", "公益": "公共服务 社会实践", "实习": "项目经历 实习", "预约": "咨询 预约", "it": "互联网 信息技术", "计算机": "互联网 信息技术 人工智能", "金融": "金融投资 银行 证券", "建筑": "建筑 工程设计", "法律": "法律服务",
}


def _local_now() -> datetime:
    return datetime.now(LOCAL_ZONE).replace(tzinfo=None) if LOCAL_ZONE else datetime.now()


def _tags(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [tag.strip() for tag in re.split(r"[|,，;；]", str(value or "")) if tag.strip()]


def _bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower().strip() in {"true", "1", "yes", "是", "可用"}


def _url(value: Any) -> str:
    value = str(value or "").strip()
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if result.tzinfo:
            result = result.astimezone(LOCAL_ZONE).replace(tzinfo=None) if LOCAL_ZONE else result.astimezone().replace(tzinfo=None)
        return result
    except (TypeError, ValueError):
        return None


def _terms(query: str) -> List[str]:
    query = str(query or "").lower().strip()
    additions = " ".join(value for key, value in _SYNONYMS.items() if key in query)
    for stopword in sorted(_STOPWORDS, key=len, reverse=True):
        query = query.replace(stopword, " ")
    terms = set()
    for token in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", query):
        if len(token) <= 2 or token.isascii():
            if len(token) > 1:
                terms.add(token)
        else:
            terms.add(token)
            terms.update(token[index:index+2] for index in range(len(token)-1))
    # Expanded phrases stay intact: splitting 产品管理 into 管理 would rank
    # unrelated executives above actual product backgrounds.
    terms.update(token for token in additions.split() if len(token) > 1)
    return sorted(terms)


def _score(query: str, record: Dict[str, Any]) -> float:
    terms = _terms(query)
    if not terms:
        return 1.0
    title = str(record.get("title", "")).lower()
    tags = " ".join(_tags(record.get("tags", []))).lower()
    text = " ".join(str(record.get(field, "")) for field in ("name", "subtitle", "description", "industry", "topic", "text", "city", "organization_type", "role_family", "education_domains")).lower()
    return sum((3 if term in title else 0) + (4 if term in tags else 0) + (1 if term in text else 0) for term in terms)


class Catalog:
    def __init__(self, data_dir: Path, reference_date: Optional[date] = None):
        self.data_dir = Path(data_dir)
        self.reference_date = reference_date
        self._imported_path = self.data_dir / "imported_catalog.json"
        sample_path = self.data_dir / "sample_catalog.json"
        self._sample = json.loads(sample_path.read_text(encoding="utf-8")) if sample_path.exists() else {"mentors": [], "activities": [], "documents": []}
        self._imported = json.loads(self._imported_path.read_text(encoding="utf-8")) if self._imported_path.exists() else {"mentors": [], "activities": [], "documents": []}
        mentor_path = self.data_dir / "imported_mentors.json"
        self._mentor_directory = json.loads(mentor_path.read_text(encoding="utf-8")) if mentor_path.exists() else {"mentors": []}

    def _now(self) -> datetime:
        return datetime.combine(self.reference_date, time.min) if self.reference_date else _local_now()

    def _has_real_mentors(self) -> bool:
        return bool(self._mentor_directory.get("mentors") or self._imported.get("mentors"))

    def _records(self, kind: str, include_samples: bool = False) -> List[Dict[str, Any]]:
        # With a real directory, keep fictional mentors and their documents out of
        # default recommendations. Explicit old sample sessions remain readable.
        merged = {}
        collections = [(True, self._sample), (False, self._imported), (False, self._mentor_directory)]
        for is_sample, collection in collections:
            if is_sample and self._has_real_mentors() and kind in {"mentors", "documents"} and not include_samples:
                continue
            for original in collection.get(kind, []):
                item = dict(original)
                item["is_sample"] = is_sample
                item["source_label"] = _SAMPLE_SOURCE if is_sample else str(item.get("source_label") or "已导入资料 · 内容以原始资料为准")
                merged[item["id"]] = item
        return list(merged.values())

    def data_summary(self) -> Dict[str, Any]:
        mentors = self._records("mentors")
        activities = self._records("activities")
        return {
            "mentor_count": len(mentors),
            "real_mentor_count": sum(not item["is_sample"] for item in mentors),
            "sample_mentor_count": sum(item["is_sample"] for item in mentors),
            "activity_count": len(activities),
            "sample_activity_count": sum(item["is_sample"] for item in activities),
            "mentor_documents_count": len(self._records("documents")),
            "privacy_mode": "deidentified" if self._mentor_directory.get("mentors") else "sample",
        }

    def _activity(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        item = dict(raw)
        now = self._now()
        for offset_name, time_name, output_name in (("start_offset_days", "start_time", "start_at"), ("deadline_offset_days", "deadline_time", "registration_deadline")):
            if item.get("is_sample") and offset_name in item:
                day = now.date() + timedelta(days=int(item[offset_name]))
                item[output_name] = day.isoformat() + "T" + str(item.get(time_name, "18:00")) + ":00"
        starts = _parse_datetime(item.get("start_at"))
        ends = _parse_datetime(item.get("end_at"))
        if starts and not ends and item.get("duration_minutes"):
            ends = starts + timedelta(minutes=int(item["duration_minutes"]))
            item["end_at"] = ends.isoformat(timespec="seconds")
        deadline = _parse_datetime(item.get("registration_deadline"))
        status = str(item.get("status") or "unknown").lower()
        if (ends or starts) and (ends or starts) < now:
            status = "ended"
        elif deadline and deadline < now and status not in {"cancelled", "ended"}:
            status = "closed"
        elif not starts or not deadline:
            if status in {"open", "active", "registration_open"}:
                status = "unknown"
        item.update({
            "type": "activity",
            "title": str(item.get("title", "未命名活动")),
            "subtitle": str(item.get("subtitle") or item.get("organizer") or "活动资源"),
            "description": str(item.get("description", "")),
            "tags": _tags(item.get("tags")),
            "status": status,
            "available": bool(starts and deadline and status in {"open", "active", "registration_open"} and starts > now and deadline >= now),
            "url": _url(item.get("url")),
            "start_at": item.get("start_at") or None,
            "registration_deadline": item.get("registration_deadline") or None,
            "location": str(item.get("location") or "待确认"),
        })
        if item["is_sample"]:
            item["availability_note"] = "仅模拟活动时效，不是真实活动，不能实际报名"
        else:
            item["availability_note"] = "以主办方公布的信息为准" if item["available"] else "报名状态或时间待确认，或已截止"
        return item

    def _mentor(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        # Return an explicit public projection. Private source columns or import
        # bookkeeping can never pass through simply because they exist in JSON.
        allowed = {"id", "title", "subtitle", "description", "tags", "industry", "city",
                   "organization_type", "role_family", "education_domains", "source_label",
                   "privacy_level", "display_name", "is_sample", "booking_method", "url",
                   "has_digital_mentor", "available"}
        item = {key: value for key, value in raw.items() if key in allowed}
        item.update({
            "type": "mentor",
            "mentor_id": str(raw["id"]),
            "title": str(raw.get("title") or raw.get("name") or "未命名导师"),
            "subtitle": str(raw.get("subtitle") or raw.get("industry") or "导师资源"),
            "description": str(raw.get("description", "")),
            "tags": _tags(raw.get("tags")),
            "has_digital_mentor": _bool(raw.get("has_digital_mentor")),
            "available": False if raw["is_sample"] else _bool(raw.get("available")),
            "booking_method": str(raw.get("booking_method") or "尚未提供预约方式"),
            "url": _url(raw.get("url")),
        })
        return item

    def public_resources(self) -> Dict[str, List[Dict[str, Any]]]:
        return {"mentors": [self._mentor(item) for item in self._records("mentors")], "activities": [self._activity(item) for item in self._records("activities")]}

    def mentor(self, mentor_id: str) -> Optional[Dict[str, Any]]:
        raw = next((item for item in self._records("mentors", include_samples=True) if item["id"] == mentor_id), None)
        return self._mentor(raw) if raw else None

    def find_mentors(self, query: str = "", industry: str = "", city: str = "", **kwargs: Any) -> List[Dict[str, Any]]:
        records = self.public_resources()["mentors"]
        display_reference = re.search(r"([\u4e00-\u9fff])?导师\s*[·#-]?\s*(\d{3,})", query)
        if display_reference:
            serial = display_reference.group(2)
            surname = display_reference.group(1)
            records = [item for item in records if re.search(r"[·#-]\s*" + re.escape(serial) + r"$", item["title"])
                       and (not surname or item["title"].startswith(surname + "导师"))]
        if industry:
            records = [item for item in records if str(industry).lower() in (item.get("industry", "") + " " + " ".join(item["tags"])).lower()]
        if city:
            city_term = str(city).strip().removesuffix("市")
            records = [item for item in records if city_term and city_term in str(item.get("city", ""))]
        if kwargs.get("mentor_id"):
            records = [item for item in records if item["id"] == str(kwargs["mentor_id"])]
        scored = [(float(_score(query, item)), item) for item in records]
        scored = [(score, item) for score, item in scored if score > 0]
        scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
        return [item for _, item in scored[:self._limit(kwargs)]]

    def find_activities(self, query: str = "", date_from: str = "", date_to: str = "", **kwargs: Any) -> List[Dict[str, Any]]:
        lower, upper = self._date_bound(date_from), self._date_bound(date_to)
        if lower and upper and lower > upper:
            return []
        results = []
        for item in self.public_resources()["activities"]:
            if item["status"] in {"ended", "closed", "cancelled", "canceled", "inactive"}:
                continue
            starts = _parse_datetime(item.get("start_at"))
            if lower and (not starts or starts.date() < lower):
                continue
            if upper and (not starts or starts.date() > upper):
                continue
            score = _score(query, item)
            if score > 0:
                results.append((score, item))
        results.sort(key=lambda pair: (-pair[0], pair[1].get("start_at") or "9999", pair[1]["id"]))
        return [item for _, item in results[:self._limit(kwargs)]]

    def search_experience(self, query: str, mentor_id: Optional[str] = None, **kwargs: Any) -> List[Dict[str, Any]]:
        # Enforce identity before ranking; never fallback to another mentor.
        records = self._records("documents", include_samples=mentor_id is not None)
        if mentor_id is not None:
            records = [item for item in records if item.get("mentor_id") == mentor_id]
        scored = [(float(_score(query, item)), item) for item in records]
        scored = [(score, item) for score, item in scored if score > 0]
        scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
        return [{"id": item["id"], "title": item.get("title", "导师经验资料"), "excerpt": item.get("text", ""), "source_label": item["source_label"], "mentor_id": item.get("mentor_id"), "is_sample": item["is_sample"], "url": _url(item.get("url"))} for _, item in scored[:self._limit(kwargs, 4)]]

    @staticmethod
    def _limit(kwargs: Dict[str, Any], default: int = 5) -> int:
        try:
            return min(10, max(1, int(kwargs.get("limit", default))))
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _date_bound(value: str) -> Optional[date]:
        if not value:
            return None
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            raise ValueError("日期必须采用 YYYY-MM-DD 格式")

    def import_files(self, paths: List[Path]) -> Dict[str, Any]:
        """Import explicitly supplied CSV/JSON/Markdown files; no URL fetching.

        File errors are isolated. Records are upserted by ID, then atomically saved.
        Metadata is not an authorization claim: review happens before this call.
        """
        result = {"files": [], "counts": {"mentors": 0, "activities": 0, "documents": 0}, "errors": []}
        pending = json.loads(json.dumps(self._imported))
        for supplied in paths:
            path = Path(supplied)
            try:
                if path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError("文件超过 2 MB；请分批导入")
                content = path.read_text(encoding="utf-8-sig")
                records = self._parse_file(path, content)
                validated = {kind: [self._normalize_import(kind, row) for row in rows] for kind, rows in records.items()}
                for kind, rows in validated.items():
                    index = {item["id"]: item for item in pending.get(kind, [])}
                    for item in rows:
                        index[item["id"]] = item
                    pending[kind] = list(index.values())
                    result["counts"][kind] += len(rows)
                result["files"].append(path.name)
            except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
                result["errors"].append({"file": path.name, "message": str(error)})
        if result["files"]:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            temporary = self._imported_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self._imported_path)
            self._imported = pending
        return result

    @staticmethod
    def _parse_file(path: Path, content: str) -> Dict[str, List[Dict[str, Any]]]:
        suffix = path.suffix.lower()
        if suffix == ".json":
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("JSON 顶层应包含 mentors、activities 或 documents 数组")
            result = {kind: data.get(kind, []) for kind in ("mentors", "activities", "documents")}
            if not any(result.values()):
                raise ValueError("JSON 中没有可导入记录")
            if not all(isinstance(rows, list) for rows in result.values()):
                raise ValueError("JSON 的资料集合必须是数组")
            return result
        if suffix == ".csv":
            reader = csv.DictReader(io.StringIO(content))
            fields = set(reader.fieldnames or [])
            kind = "mentors" if "name" in fields else "activities" if "start_at" in fields else None
            if not kind:
                raise ValueError("CSV 需要导师 name 列，或活动 start_at 列；请使用模板")
            rows = list(reader)
            if not rows:
                raise ValueError("CSV 没有数据行")
            return {kind: rows}
        if suffix in {".md", ".markdown"}:
            match = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", content, re.S)
            if not match:
                raise ValueError("Markdown 需要带 mentor_id 的顶部元数据，请使用模板")
            metadata = {}
            for line in match.group(1).splitlines():
                key, separator, value = line.partition(":")
                if separator:
                    metadata[key.strip()] = value.strip().strip("\"'")
            metadata["text"] = match.group(2).strip()
            metadata.setdefault("title", path.stem)
            return {"documents": [metadata]}
        raise ValueError("目前支持 .csv、.json、.md；Excel 请先另存为 UTF-8 CSV")

    @staticmethod
    def _normalize_import(kind: str, row: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(row, dict):
            raise ValueError("每条记录必须是对象")
        item = dict(row)
        if kind == "mentors":
            required = ("name",)
        elif kind == "activities":
            required = ("title",)
        else:
            required = ("mentor_id", "text")
        for field in required:
            if not str(item.get(field) or "").strip():
                raise ValueError("缺少必填字段：" + field)
        if not item.get("id"):
            seed = str(item.get("mentor_id", "")) + str(item.get("name") or item.get("title") or item.get("text"))
            item["id"] = kind[:-1] + "-" + hashlib.sha256(seed.encode()).hexdigest()[:12]
        item["id"] = str(item["id"])
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item["id"]):
            raise ValueError("id 只允许 1—100 位英文字母、数字、下划线或连字符")
        if kind == "activities":
            for field in ("start_at", "end_at", "registration_deadline"):
                if item.get(field) and not _parse_datetime(item[field]):
                    raise ValueError(field + " 不是有效的 ISO 日期时间")
            item["status"] = str(item.get("status") or "unknown").lower()
            if item["status"] not in {"unknown", "open", "active", "registration_open", "closed", "ended", "cancelled", "canceled", "inactive"}:
                raise ValueError("活动 status 应为 open、unknown、closed、ended 或 cancelled")
        item["tags"] = _tags(item.get("tags"))
        item["url"] = _url(item.get("url"))
        item["is_sample"] = False
        item["source_label"] = str(item.get("source_label") or "已导入资料 · 内容以原始资料为准")
        return item
